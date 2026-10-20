from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from drums2midi.job import DEFAULT_ROOT, Job, source_hash


@dataclass
class RunOptions:
    out_root: Path = DEFAULT_ROOT
    quality: str = "fast"
    style: str = "raw"
    grid: int = 16
    device: str = "auto"
    tempo_model: str = "final"
    count_in: int = 2
    count_in_note: int = 37
    keep_other: bool = False
    force: bool = False


class Reporter(Protocol):
    def stage(self, name: str, detail: str = "", determinate: bool = False) -> None: ...
    def progress(self, fraction: float) -> None: ...
    def finish(self, detail: str = "") -> None: ...
    def skipped(self, name: str, detail: str = "") -> None: ...


class NullReporter:
    def stage(self, name: str, detail: str = "", determinate: bool = False) -> None: ...
    def progress(self, fraction: float) -> None: ...
    def finish(self, detail: str = "") -> None: ...
    def skipped(self, name: str, detail: str = "") -> None: ...


@dataclass
class RunResult:
    job: Job
    beats: Any
    notes: list
    layout: Any
    midi_path: Path
    rpp_path: Path
    counts: dict[str, int]
    timings: dict[str, float] = field(default_factory=dict)


def run(source: str, opts: RunOptions, reporter: Reporter | None = None) -> RunResult:
    from drums2midi.ingest import ingest_file, is_url, title_for_file

    reporter = reporter or NullReporter()
    if is_url(source):
        raise NotImplementedError("URL ingest is not implemented yet; pass an audio file")

    src = Path(source).expanduser().resolve()
    job = Job.create(opts.out_root, title_for_file(src), str(src), source_hash(src))
    runner = _Runner(job, opts, reporter)

    def do_ingest() -> None:
        ingest_file(src, job.path("original.wav"))

    runner.stage("ingest", {}, do_ingest, detail=src.name)
    return runner.process()


def resimplify(job_dir: Path, opts: RunOptions, reporter: Reporter | None = None) -> RunResult:
    job = Job.open(job_dir)
    runner = _Runner(job, opts, reporter or NullReporter())
    return runner.process()


class _Runner:
    """Executes stages in order; once a stage runs, every later stage runs too."""

    def __init__(self, job: Job, opts: RunOptions, reporter: Reporter) -> None:
        self.job = job
        self.opts = opts
        self.reporter = reporter
        self.dirty = opts.force
        self.timings: dict[str, float] = {}

    def stage(self, name, params, fn, *, detail="", determinate=False) -> dict[str, Any]:
        if not self.dirty and self.job.is_done(name, params):
            self.reporter.skipped(name, "cached")
            return self.job.stage_info(name)
        self.dirty = True
        self.reporter.stage(name, detail, determinate)
        t0 = time.perf_counter()
        info = fn() or {}
        seconds = round(time.perf_counter() - t0, 2)
        self.timings[name] = seconds
        self.job.mark_done(name, params, seconds=seconds, **info)
        self.reporter.finish(f"{seconds:.1f}s")
        return self.job.stage_info(name)

    def process(self) -> RunResult:
        from drums2midi.audio import duration_seconds
        from drums2midi.midi_out import CountIn, build_sequence, write_midi
        from drums2midi.reaper import STEM_VOLUME_DB, AudioTrack, write_rpp
        from drums2midi.separate import MODELS, separate
        from drums2midi.simplify import count_by_class, load_notes, save_notes, simplify
        from drums2midi.tempo import Beats, detect_beats
        from drums2midi.transcribe import load_activations, transcribe

        job, opts = self.job, self.opts
        model = MODELS[opts.quality]

        def do_separate() -> dict:
            used = separate(
                job.path("original.wav"),
                job.path("drums.wav"),
                quality=opts.quality,
                device=opts.device,
                no_drums_wav=job.path("no_drums.wav") if opts.keep_other else None,
                progress=self.reporter.progress,
            )
            return {"device": used}

        self.stage("separate", {"model": model}, do_separate, detail=model, determinate=True)

        def do_transcribe() -> dict:
            act = transcribe(job.path("drums.wav"), job.path("activations.npy"))
            return {"frames": int(act.shape[0])}

        self.stage("transcribe", {}, do_transcribe, detail="adtof")

        def do_tempo() -> dict:
            beats = detect_beats(
                job.path("original.wav"), device=opts.device, model=opts.tempo_model
            )
            beats.save(job.path("beats.json"))
            return {"bpm": beats.bpm, "meter": beats.meter}

        self.stage("tempo", {"model": opts.tempo_model}, do_tempo, detail="beat_this")
        beats = Beats.load(job.path("beats.json"))

        def do_simplify() -> dict:
            notes = simplify(
                load_activations(job.path("activations.npy")),
                beats,
                style=opts.style,
                grid=opts.grid,
            )
            save_notes(job.path("notes.json"), notes, {"style": opts.style, "grid": opts.grid})
            return {"counts": count_by_class(notes)}

        self.stage(
            "simplify", {"style": opts.style, "grid": opts.grid}, do_simplify, detail=opts.style
        )
        notes = load_notes(job.path("notes.json"))

        count_in = (
            CountIn(bars=opts.count_in, note=opts.count_in_note) if opts.count_in > 0 else None
        )
        output_params = {
            "count_in": opts.count_in,
            "count_in_note": opts.count_in_note,
            "tempo": "constant",
        }
        seq = build_sequence(notes, beats, count_in=count_in)

        def do_output() -> dict:
            write_midi(job.path("drums.mid"), seq, track_name=job.title)
            duration = duration_seconds(job.path("original.wav"))
            write_rpp(
                job.path("project.RPP"),
                title=job.title,
                seq=seq,
                stem=AudioTrack(
                    name=f"{job.title} (drums)",
                    file="drums.wav",
                    duration=duration,
                    position=seq.layout.shift,
                    pan=-1.0,
                    volume_db=STEM_VOLUME_DB,
                ),
                original=AudioTrack(
                    name=f"{job.title} (original)",
                    file="original.wav",
                    duration=duration,
                    position=seq.layout.shift,
                    muted=True,
                ),
            )
            return {"shift": seq.layout.shift, "count_in_seconds": seq.layout.count_in_seconds}

        self.stage("output", output_params, do_output, detail="drums.mid + project.RPP")

        return RunResult(
            job=job,
            beats=beats,
            notes=notes,
            layout=seq.layout,
            midi_path=job.path("drums.mid"),
            rpp_path=job.path("project.RPP"),
            counts=count_by_class(notes),
            timings=self.timings,
        )
