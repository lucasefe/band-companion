from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from drums2midi.job import DEFAULT_ROOT, Job, source_hash

PRIMARY = "conservative"  # the unmuted MIDI track and drums.mid; other profiles get a suffix
OUTPUT_FORMAT = 2  # bump when the output stage writes different files/content for the same inputs


def notes_file(profile: str) -> str:
    return "notes.json" if profile == PRIMARY else f"notes-{profile}.json"


def midi_file(profile: str) -> str:
    return "drums.mid" if profile == PRIMARY else f"drums-{profile}.mid"


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
    # lyrics
    lyrics: bool = True
    artist: str | None = None
    title: str | None = None
    lyrics_file: Path | None = None
    lyrics_offset: float | None = None  # None = measure against the vocal stem


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
    counts: dict[str, dict[str, int]]  # profile -> class -> hits
    lyrics: dict[str, Any]  # contents of lyrics.json
    timings: dict[str, float] = field(default_factory=dict)


def run(source: str, opts: RunOptions, reporter: Reporter | None = None) -> RunResult:
    from drums2midi.ingest import ingest_file, ingest_url, is_url, probe_url, title_for_file

    reporter = reporter or NullReporter()

    if is_url(source):
        meta = probe_url(source)
        canonical = meta.get("webpage_url") or source
        job = Job.create(
            opts.out_root, meta.get("title") or canonical, canonical, source_hash(canonical)
        )
        job.manifest["source_meta"] = meta
        job.save()
        runner = _Runner(job, opts, reporter)

        def do_ingest() -> dict:
            downloaded = ingest_url(
                canonical, job.path("original.wav"), job.dir, progress=reporter.progress
            )
            return {"downloaded": downloaded.name}

        runner.stage("ingest", {}, do_ingest, detail="yt-dlp", determinate=True)
    else:
        src = Path(source).expanduser().resolve()
        job = Job.create(opts.out_root, title_for_file(src), str(src), source_hash(src))
        runner = _Runner(job, opts, reporter)

        def do_ingest_file() -> None:
            ingest_file(src, job.path("original.wav"))

        runner.stage("ingest", {}, do_ingest_file, detail=src.name)

    return runner.process()


def resimplify(job_dir: Path, reporter: Reporter | None = None, **overrides: Any) -> RunResult:
    """Rerun from `simplify` on with the job's saved options plus any overrides."""
    job = Job.open(job_dir)
    opts = options_from_job(job, **overrides)
    return _Runner(job, opts, reporter or NullReporter()).process()


def redo_lyrics(job_dir: Path, reporter: Reporter | None = None, **overrides: Any) -> RunResult:
    """Force the lyrics stage (and therefore the outputs) with the saved options plus overrides."""
    job = Job.open(job_dir)
    opts = options_from_job(job, **overrides)
    return _Runner(job, opts, reporter or NullReporter(), rerun={"lyrics"}).process()


def options_from_job(job: Job, **overrides: Any) -> RunOptions:
    saved = job.manifest.get("options", {})
    kwargs = {k: v for k, v in saved.items() if k in RunOptions.__dataclass_fields__}
    kwargs.update({k: v for k, v in overrides.items() if v is not None})
    if "out_root" in kwargs:
        kwargs["out_root"] = Path(kwargs["out_root"])
    if kwargs.get("lyrics_file"):
        kwargs["lyrics_file"] = Path(kwargs["lyrics_file"])
    kwargs["force"] = bool(overrides.get("force", False))
    return RunOptions(**kwargs)


class _Runner:
    """Executes stages in order; once a stage runs, every later stage runs too."""

    def __init__(
        self, job: Job, opts: RunOptions, reporter: Reporter, rerun: set[str] | None = None
    ) -> None:
        self.job = job
        self.opts = opts
        self.reporter = reporter
        self.rerun = rerun or set()
        self.dirty = opts.force
        self.timings: dict[str, float] = {}
        job.manifest["options"] = {
            k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(opts).items()
        }
        job.save()

    def stage(self, name, params, fn, *, detail="", determinate=False) -> dict[str, Any]:
        if not self.dirty and name not in self.rerun and self.job.is_done(name, params):
            self.reporter.skipped(name, "cached")
            return self.job.stage_info(name)
        self.dirty = True
        self.reporter.stage(name, detail, determinate)
        t0 = time.perf_counter()
        info = fn() or {}
        seconds = round(time.perf_counter() - t0, 2)
        self.timings[name] = seconds
        detail_text = info.pop("detail", f"{seconds:.1f}s")
        self.job.mark_done(name, params, seconds=seconds, **info)
        self.reporter.finish(detail_text)
        return self.job.stage_info(name)

    def process(self) -> RunResult:
        import json

        from drums2midi.audio import duration_seconds
        from drums2midi.midi_out import CountIn, build_sequence, write_midi
        from drums2midi.reaper import STEM_VOLUME_DB, AudioTrack, MidiTrack, write_rpp
        from drums2midi.separate import MODELS, separate
        from drums2midi.simplify import (
            PROFILES,
            count_by_class,
            load_notes,
            save_notes,
            simplify,
        )
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
                vocals_wav=job.path("vocals.wav"),
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
            activations = load_activations(job.path("activations.npy"))
            counts = {}
            for profile, thresholds in PROFILES.items():
                notes = simplify(
                    activations, beats, style=opts.style, grid=opts.grid, thresholds=thresholds
                )
                save_notes(
                    job.path(notes_file(profile)),
                    notes,
                    {"style": opts.style, "grid": opts.grid, "profile": profile},
                )
                counts[profile] = count_by_class(notes)
            return {"counts": counts}

        self.stage(
            "simplify",
            {
                "style": opts.style,
                "grid": opts.grid,
                "profiles": {p: list(t) for p, t in PROFILES.items()},
            },
            do_simplify,
            detail=opts.style,
        )
        notes = {p: load_notes(job.path(notes_file(p))) for p in PROFILES}

        lyrics_params = {
            "enabled": opts.lyrics,
            "artist": opts.artist,
            "title": opts.title,
            "file": str(opts.lyrics_file) if opts.lyrics_file else None,
            "offset": opts.lyrics_offset,
        }
        self.stage("lyrics", lyrics_params, lambda: self._do_lyrics(), detail="lrclib")
        lyrics_info = json.loads(job.path("lyrics.json").read_text())

        count_in = (
            CountIn(bars=opts.count_in, note=opts.count_in_note) if opts.count_in > 0 else None
        )
        output_params = {
            "count_in": opts.count_in,
            "count_in_note": opts.count_in_note,
            "tempo": "constant",
            "format": OUTPUT_FORMAT,
        }
        seqs = {p: build_sequence(notes[p], beats, count_in=count_in) for p in PROFILES}
        layout = seqs[PRIMARY].layout
        song_lines = [(t, text) for t, text in lyrics_info.get("lines_song_time", [])]
        lyric_lines = [(t + layout.shift, text) for t, text in song_lines]

        def do_output() -> dict:
            for profile, seq in seqs.items():
                write_midi(
                    job.path(midi_file(profile)),
                    seq,
                    track_name=f"{job.title} {profile}",
                    lyrics=lyric_lines,
                )
            self._write_lrc_files(lyrics_info, song_lines, lyric_lines, layout, count_in)
            duration = duration_seconds(job.path("original.wav"))
            write_rpp(
                job.path("project.RPP"),
                stem=AudioTrack(
                    name=f"{job.title} (drums)",
                    file="drums.wav",
                    duration=duration,
                    position=layout.shift,
                    pan=-1.0,
                    volume_db=STEM_VOLUME_DB,
                ),
                midi=[
                    MidiTrack(
                        name=f"{job.title} (MIDI {profile})",
                        item_name=f"{job.title} {profile}",
                        seq=seq,
                        pan=1.0,
                        muted=profile != PRIMARY,
                    )
                    for profile, seq in seqs.items()
                ],
                original=AudioTrack(
                    name=f"{job.title} (original)",
                    file="original.wav",
                    duration=duration,
                    position=layout.shift,
                    muted=True,
                ),
                markers=lyric_lines,
            )
            return {"shift": layout.shift, "count_in_seconds": layout.count_in_seconds}

        self.stage("output", output_params, do_output, detail="midi + lrc + rpp")

        return RunResult(
            job=job,
            beats=beats,
            notes=notes[PRIMARY],
            layout=layout,
            midi_path=job.path(midi_file(PRIMARY)),
            rpp_path=job.path("project.RPP"),
            counts={p: count_by_class(n) for p, n in notes.items()},
            lyrics=lyrics_info,
            timings=self.timings,
        )

    def _do_lyrics(self) -> dict:
        """Find lyrics, check their timing against the vocal stem, write lyrics.{json,txt}.

        The .lrc files are written by the output stage, which knows the count-in shift.
        Never raises: a miss is recorded in lyrics.json so the stage is cached like any other.
        """
        from drums2midi.audio import duration_seconds
        from drums2midi.lyrics import (
            estimate_offset,
            hints_from_file,
            hints_from_youtube,
            lyrics_from_file,
            lyrics_from_lrclib,
            lyrics_to_dict,
            plain_text,
            vocal_activity,
        )

        job, opts = self.job, self.opts
        job.path("lyrics.txt").unlink(missing_ok=True)
        payload: dict[str, Any] = {"found": False, "lines_song_time": []}
        if not opts.lyrics:
            payload["reason"] = "disabled"
            _write_lyrics_json(job, payload)
            return {"detail": "skipped"}

        duration = duration_seconds(job.path("original.wav"))
        if job.manifest.get("source_meta"):
            hints = hints_from_youtube(job.manifest["source_meta"], duration)
        else:
            hints = hints_from_file(Path(job.manifest["source"]), duration)
        if opts.artist:
            hints.artist = opts.artist
        if opts.title:
            hints.title = opts.title
        payload["hints"] = {"artist": hints.artist, "title": hints.title, "duration": duration}

        try:
            if opts.lyrics_file:
                lyrics = lyrics_from_file(Path(opts.lyrics_file).expanduser())
                considered: list[dict[str, Any]] = []
            else:
                lyrics, considered = lyrics_from_lrclib(hints)
            payload["considered"] = considered
        except Exception as e:  # noqa: BLE001
            payload["error"] = f"{type(e).__name__}: {e}"
            _write_lyrics_json(job, payload)
            return {"detail": "[red]lookup failed[/]"}

        if lyrics is None:
            payload["reason"] = "no match on lrclib"
            _write_lyrics_json(job, payload)
            return {"detail": "[yellow]not found[/]"}

        if lyrics.synced:
            if opts.lyrics_offset is not None:
                lyrics.offset = opts.lyrics_offset
                payload["offset"] = {"applied": lyrics.offset, "source": "manual"}
            elif job.path("vocals.wav").exists():
                est = estimate_offset(lyrics, vocal_activity(job.path("vocals.wav")))
                lyrics.offset = est.applied
                lyrics.confidence = est.agreement_at_applied
                payload["offset"] = {
                    "applied": est.applied,
                    "suggested": est.suggested,
                    "agreement_at_zero": est.agreement_at_zero,
                    "agreement_at_applied": est.agreement_at_applied,
                    "source": "vocal stem",
                }

        job.path("lyrics.txt").write_text(plain_text(lyrics), encoding="utf-8")
        payload.update(
            {
                "found": True,
                "lyrics": lyrics_to_dict(lyrics),
                "header": {
                    "ar": lyrics.meta.get("artist") or hints.artist or "",
                    "ti": lyrics.meta.get("title") or hints.title or "",
                    "al": lyrics.meta.get("album") or "",
                },
                "lines_song_time": [[ln.time + lyrics.offset, ln.text] for ln in lyrics.timed],
            }
        )
        _write_lyrics_json(job, payload)

        who = f"{lyrics.meta.get('artist', '?')} – {lyrics.meta.get('title', '?')}"
        kind = f"{len(lyrics.timed)} lines" if lyrics.synced else "text only"
        return {"detail": f"{who} · {kind}", "source": lyrics.source}

    def _write_lrc_files(self, lyrics_info, song_lines, lyric_lines, layout, count_in) -> None:
        """lyrics.lrc on the count-in timeline (with a visible countdown), lyrics-original.lrc
        in song time for the untouched original.wav."""
        from drums2midi.audio import duration_seconds
        from drums2midi.lyrics import countdown_lines, format_lrc

        job = self.job
        for name in ("lyrics.lrc", "lyrics-original.lrc"):
            job.path(name).unlink(missing_ok=True)
        if not song_lines:
            return

        meta = lyrics_info.get("lyrics", {}).get("meta", {})
        base = lyrics_info.get("header") or {
            "ar": meta.get("artist", ""),
            "ti": meta.get("title", ""),
            "al": meta.get("album", ""),
        }

        def header(total_seconds: float) -> dict[str, str]:
            return dict(base) | {
                "length": f"{int(total_seconds // 60):02d}:{int(total_seconds % 60):02d}",
                "re": "drums2midi",
            }

        duration = duration_seconds(job.path("original.wav"))
        countdown = (
            countdown_lines(layout.count_in_start, count_in.bars, layout.meter, 60.0 / layout.bpm)
            if count_in
            else []
        )
        job.path("lyrics.lrc").write_text(
            format_lrc(countdown + lyric_lines, header=header(layout.shift + duration)),
            encoding="utf-8",
        )
        job.path("lyrics-original.lrc").write_text(
            format_lrc(song_lines, header=header(duration)), encoding="utf-8"
        )


def _write_lyrics_json(job: Job, payload: dict[str, Any]) -> None:
    from drums2midi.lyrics import save_lyrics_json

    save_lyrics_json(job.path("lyrics.json"), payload)
