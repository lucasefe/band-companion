from __future__ import annotations

import json
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from backline import job as J
from backline.catalog import find_by_hash, write_record
from backline.config import Config, load_config, song_folder
from backline.job import DEFAULT_ROOT, Job, source_hash

PRIMARY = "conservative"  # the unmuted MIDI track and <title>.mid; other profiles get a suffix
GRID_VERSION = 3  # bump when choose_grid() changes; the tempo stage is cached on it
OUTPUT_FORMAT = 16  # bump when the output stage writes different files/content for the same inputs


@dataclass
class RunOptions:
    out_root: Path = DEFAULT_ROOT
    deliver_to: Path | None = None  # explicit song folder for the .lrc and the render
    set_name: str | None = None  # else tracks/<set>/<NN - Title> from backline.toml
    number: int | None = None
    quality: str = "fast"
    style: str = "raw"
    quantize: int = 16  # grid for the quantized styles (not implemented yet)
    device: str = "auto"
    tempo_model: str = "final"
    grid: str = "auto"  # auto | straight | tracked
    bpm: float | None = None  # force a straight grid at this tempo
    count_in: int = 2
    count_in_note: int = 37
    keep_other: bool = False
    force: bool = False
    overwrite: bool = False  # regenerate the project even after Reaper has saved it
    # lyrics
    lyrics: bool = True
    artist: str | None = None
    title: str | None = None
    lyrics_file: Path | None = None
    lyrics_offset: float | None = None  # None = measure against the vocal stem
    align: bool = False  # time the lines from the recording (Whisper) even if the sync looks fine


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
    layout: Any
    counts: dict[str, dict[str, int]]  # profile -> class -> hits
    lyrics: dict[str, Any]  # contents of lyrics.json
    project_path: Path
    output_dir: Path
    outputs: dict[str, Path]  # lrc / midi / render target
    fx_chain: bool
    project_kept: bool = False  # Reaper owns the project; it was read, not written
    lyrics_replaced: bool = False  # the lookup's lines were put into a Reaper-saved project
    lyric_lines: int = 0  # markers exported to the .lrc
    renders_removed: list[str] = field(default_factory=list)  # Reaper's -001 copies cleaned up
    deduped: bool = False  # the song folder already existed for this source
    timings: dict[str, float] = field(default_factory=dict)


def run(
    source: str,
    opts: RunOptions,
    reporter: Reporter | None = None,
    config: Config | None = None,
) -> RunResult:
    from backline.ingest import (
        ingest_file,
        ingest_url,
        is_url,
        probe_url,
    )
    from backline.lyrics import hints_from_file, hints_from_youtube

    reporter = reporter or NullReporter()

    if is_url(source):
        meta = probe_url(source)
        canonical = meta.get("webpage_url") or source
        hints = hints_from_youtube(meta, float(meta.get("duration") or 0))
        job = Job.create(
            opts.out_root,
            title=opts.title or hints.title or meta.get("title") or "untitled",
            artist=opts.artist or hints.artist,
            source=canonical,
            hash=source_hash(canonical),
        )
        runner = _Runner(job, opts, reporter, config=config)

        def do_ingest_url() -> dict:
            downloaded = ingest_url(
                canonical, job.path(J.ORIGINAL), job.path(J.INPUT), progress=reporter.progress
            )
            _write_source_json(
                job, {"kind": "url", "url": canonical, "file": downloaded.name, "meta": meta}
            )
            return {"downloaded": downloaded.name}

        runner.stage(
            "ingest",
            {},
            do_ingest_url,
            [J.ORIGINAL, J.SOURCE_JSON],
            detail="yt-dlp",
            determinate=True,
        )
    else:
        src = Path(source).expanduser().resolve()
        if not src.is_file():
            raise FileNotFoundError(f"file not found: {src}")
        hints = hints_from_file(src, 0.0)
        job = Job.create(
            opts.out_root,
            title=opts.title or hints.title or src.stem,
            artist=opts.artist or hints.artist,
            source=str(src),
            hash=source_hash(src),
        )
        runner = _Runner(job, opts, reporter, config=config)

        def do_ingest_file() -> dict:
            copy = job.path(J.INPUT) / f"source{src.suffix.lower()}"
            shutil.copy2(src, copy)
            ingest_file(copy, job.path(J.ORIGINAL))
            _write_source_json(job, {"kind": "file", "path": str(src), "file": copy.name})
            return {"copied": copy.name}

        runner.stage("ingest", {}, do_ingest_file, [J.ORIGINAL, J.SOURCE_JSON], detail=src.name)

    return runner.process()


def resimplify(
    job_dir: Path,
    reporter: Reporter | None = None,
    config: Config | None = None,
    **overrides: Any,
) -> RunResult:
    """Rerun from `simplify` on with the job's saved options plus any overrides."""
    job = Job.open(job_dir)
    opts = options_from_job(job, **overrides)
    return _Runner(job, opts, reporter or NullReporter(), config=config).process()


def redo_lyrics(
    job_dir: Path,
    reporter: Reporter | None = None,
    config: Config | None = None,
    replace: bool = False,
    **overrides: Any,
) -> RunResult:
    """Look the lyrics up again with the saved options plus overrides. A project that is still
    ours is regenerated with the result; one Reaper has saved keeps its markers — they may be
    the user's edits — unless `replace` says to swap them."""
    job = Job.open(job_dir)
    opts = options_from_job(job, **overrides)
    return _Runner(
        job,
        opts,
        reporter or NullReporter(),
        rerun={"lyrics"},
        replace_lyrics=replace,
        config=config,
    ).process()


def export(
    job_dir: Path,
    reporter: Reporter | None = None,
    config: Config | None = None,
) -> RunResult:
    """Song folder from the project as saved by Reaper: the markers become the .lrc."""
    job = Job.open(job_dir)
    opts = options_from_job(job)
    return _Runner(job, opts, reporter or NullReporter(), rerun={"output"}, config=config).process()


def export_all(
    config: Config | None = None, reporter: Reporter | None = None
) -> list[tuple[Path, RunResult | Exception]]:
    """export() for every song folder under the configured tracks/ whose backline.json points
    at a job that still exists. Folders without a job (hand-made) are skipped."""
    from backline.catalog import RECORD_NAME, read_record

    cfg = config or load_config()
    results: list[tuple[Path, RunResult | Exception]] = []
    if not cfg.tracks:
        return results
    for record_path in sorted(cfg.tracks.glob(f"*/*/{RECORD_NAME}")):
        folder = record_path.parent
        record = read_record(folder) or {}
        job_dir = Path(record.get("job", "")).expanduser() if record.get("job") else None
        if not job_dir or not job_dir.is_dir():
            continue
        try:
            results.append((folder, export(job_dir, reporter, cfg)))
        except Exception as e:  # noqa: BLE001 — one broken song must not stop the others
            results.append((folder, e))
    return results


def rename(
    job_dir: Path,
    *,
    title: str | None = None,
    artist: str | None = None,
    reporter: Reporter | None = None,
    config: Config | None = None,
) -> RunResult:
    """Fix a song's title/artist everywhere: job folder, project, song folder and its files.

    Rendered audio is renamed too, so nothing needs re-rendering. Outputs are then regenerated
    so the project, the lyrics header and backline.json carry the new name.
    """
    from backline.job import sanitize_name

    job = Job.open(job_dir)
    old_stem = job.file_stem
    job.manifest["song"] = {
        "artist": artist if artist is not None else job.artist,
        "title": title or job.title,
    }
    new_stem = job.file_stem
    job.save()

    # The project keeps working under the new name; the output stage refreshes it if it is ours.
    old_path = job.dir / f"{old_stem}.RPP"
    if old_path.exists() and new_stem != old_stem:
        old_path.rename(job.dir / f"{new_stem}.RPP")

    # Song folder: keep its number, rename the folder and the deliverables inside it.
    saved = job.manifest.get("options", {})
    folder = Path(saved["deliver_to"]) if saved.get("deliver_to") else None
    if folder and folder.is_dir() and new_stem != old_stem:
        for ext in (".lrc", ".wav", ".mid"):
            src = folder / f"{old_stem}{ext}"
            if src.exists():
                src.rename(folder / f"{new_stem}{ext}")
        m = re.match(r"^(\s*\d+\s*[-._)]\s*)", folder.name)
        prefix = m.group(1) if m else ""
        target = folder.with_name(f"{prefix}{sanitize_name(job.title)}")
        if target != folder and not target.exists():
            folder.rename(target)
            folder = target
        job.manifest["options"]["deliver_to"] = str(folder)
        job.save()
    for ext in (".mid",):
        for old_file in job.path(J.OUTPUT).glob(f"{old_stem}*{ext}"):
            old_file.rename(old_file.with_name(old_file.name.replace(old_stem, new_stem, 1)))

    # The job folder itself.
    name = f"{job.artist} - {job.title}" if job.artist else job.title
    new_dir = job.dir.with_name(f"{sanitize_name(name)} ({job.manifest['hash']})")
    if new_dir != job.dir and not new_dir.exists():
        job.dir.rename(new_dir)
        job = Job.open(new_dir)

    opts = options_from_job(job, deliver_to=job.manifest["options"].get("deliver_to"))
    return _Runner(job, opts, reporter or NullReporter(), rerun={"output"}, config=config).process()


def options_from_job(job: Job, **overrides: Any) -> RunOptions:
    saved = dict(job.manifest.get("options", {}))
    if isinstance(
        saved.get("grid"), int
    ):  # before the beat grid existed, `grid` was the quantize step
        saved["quantize"] = saved.pop("grid")
    kwargs = {k: v for k, v in saved.items() if k in RunOptions.__dataclass_fields__}
    kwargs.update({k: v for k, v in overrides.items() if v is not None})
    for key in ("out_root", "deliver_to", "lyrics_file"):
        if kwargs.get(key):
            kwargs[key] = Path(kwargs[key])
    kwargs["force"] = bool(overrides.get("force", False))
    return RunOptions(**kwargs)


def _tidy_renders(render: Path) -> list[str]:
    """Reaper renders to `<stem>-001.wav` when `<stem>.wav` exists (the "silently increment"
    option). The newest render is the one meant: it becomes `<stem>.wav`, older copies go."""
    stem = re.escape(render.stem)
    candidates = [
        p
        for p in render.parent.glob(f"{render.stem}*.wav")
        if re.fullmatch(rf"{stem}(-\d{{3}})?\.wav", p.name)
    ]
    if len(candidates) < 2:
        return []
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    removed = []
    for p in sorted(candidates):
        if p != newest:
            p.unlink()
            removed.append(p.name)
    if newest != render:
        newest.rename(render)
    return removed


def _write_source_json(job: Job, payload: dict[str, Any]) -> None:
    payload["ingested_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    job.path(J.SOURCE_JSON).write_text(json.dumps(payload, indent=1, ensure_ascii=False))


class _Runner:
    """Executes stages in order; once a stage runs, every later stage runs too."""

    def __init__(
        self,
        job: Job,
        opts: RunOptions,
        reporter: Reporter,
        rerun: set[str] | None = None,
        replace_lyrics: bool = False,
        config: Config | None = None,
    ) -> None:
        self.job = job
        self.opts = opts
        self.reporter = reporter
        self.rerun = rerun or set()
        self.replace_lyrics = replace_lyrics  # swap the markers of a Reaper-saved project
        self.config = config or load_config()
        self.dirty = opts.force
        self.timings: dict[str, float] = {}
        job.manifest["options"] = {
            k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(opts).items()
        }
        job.save()

    def stage(
        self,
        name: str,
        params: dict[str, Any],
        fn: Callable[[], dict | None],
        artifacts: list[str | Path],
        *,
        detail: str = "",
        determinate: bool = False,
    ) -> dict[str, Any]:
        paths = [a if isinstance(a, Path) else self.job.path(a) for a in artifacts]
        if not self.dirty and name not in self.rerun and self.job.is_done(name, params, paths):
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
        from backline.audio import duration_seconds
        from backline.lyrics import countdown_lines
        from backline.midi_out import CountIn, build_sequence, write_midi
        from backline.reaper import (
            ORIGINAL_PAN,
            ORIGINAL_VOLUME_DB,
            STEM_VOLUME_DB,
            AudioTrack,
            MidiTrack,
            RenderSettings,
            is_generated,
            load_fx_template,
            read_markers,
            replace_markers,
            write_rpp,
        )
        from backline.separate import MODELS, separate
        from backline.simplify import (
            PROFILES,
            count_by_class,
            load_notes,
            save_notes,
            simplify,
        )
        from backline.tempo import Beats, GridInfo, choose_grid, detect_beats
        from backline.transcribe import load_activations, transcribe

        job, opts = self.job, self.opts
        model = MODELS[opts.quality]

        def do_separate() -> dict:
            used = separate(
                job.path(J.ORIGINAL),
                job.path(J.DRUMS),
                quality=opts.quality,
                device=opts.device,
                vocals_wav=job.path(J.VOCALS),
                no_drums_wav=job.path(J.NO_DRUMS) if opts.keep_other else None,
                progress=self.reporter.progress,
            )
            return {"device": used}

        self.stage(
            "separate",
            {"model": model},
            do_separate,
            [J.DRUMS, J.VOCALS],
            detail=model,
            determinate=True,
        )

        def do_transcribe() -> dict:
            act = transcribe(job.path(J.DRUMS), job.path(J.ACTIVATIONS))
            return {"frames": int(act.shape[0])}

        self.stage("transcribe", {}, do_transcribe, [J.ACTIVATIONS], detail="adtof")

        def do_tempo() -> dict:
            if (
                job.path(J.BEATS).exists()
                and job.stage_info("tempo").get("model") == opts.tempo_model
            ):
                beats = Beats.load(job.path(J.BEATS))  # only the grid parameters changed
            else:
                beats = detect_beats(
                    job.path(J.ORIGINAL), device=opts.device, model=opts.tempo_model
                )
                beats.save(job.path(J.BEATS))
            grid, info = choose_grid(
                beats,
                job.path(J.DRUMS),
                duration_seconds(job.path(J.ORIGINAL)),
                mode=opts.grid,
                bpm=opts.bpm,
            )
            grid.save(job.path(J.GRID))
            return {
                "model": opts.tempo_model,
                "bpm": info.bpm,
                "meter": grid.meter,
                "grid": asdict(info),
                "detail": f"{info.mode} {info.bpm:g} bpm",
            }

        self.stage(
            "tempo",
            {
                "model": opts.tempo_model,
                "grid": opts.grid,
                "bpm": opts.bpm,
                "version": GRID_VERSION,
            },
            do_tempo,
            [J.BEATS, J.GRID],
            detail="beat_this",
        )
        beats = Beats.load(job.path(J.GRID))  # everything downstream uses the grid
        grid_info = GridInfo(**job.stage_info("tempo")["grid"])

        def do_simplify() -> dict:
            activations = load_activations(job.path(J.ACTIVATIONS))
            counts = {}
            for profile, thresholds in PROFILES.items():
                notes = simplify(
                    activations, beats, style=opts.style, grid=opts.quantize, thresholds=thresholds
                )
                save_notes(
                    job.path(J.notes_file(profile, PRIMARY)),
                    notes,
                    {"style": opts.style, "grid": opts.quantize, "profile": profile},
                )
                counts[profile] = count_by_class(notes)
            return {"counts": counts}

        self.stage(
            "simplify",
            {
                "style": opts.style,
                "grid": opts.quantize,
                "profiles": {p: list(t) for p, t in PROFILES.items()},
            },
            do_simplify,
            [J.notes_file(p, PRIMARY) for p in PROFILES],
            detail=opts.style,
        )
        notes = {p: load_notes(job.path(J.notes_file(p, PRIMARY))) for p in PROFILES}

        lyrics_params = {
            "enabled": opts.lyrics,
            "artist": opts.artist,
            "title": opts.title,
            "file": str(opts.lyrics_file) if opts.lyrics_file else None,
            "offset": opts.lyrics_offset,
            "align": opts.align,
        }
        self.stage("lyrics", lyrics_params, self._do_lyrics, [J.LYRICS_JSON], detail="lrclib")
        lyrics_info = json.loads(job.path(J.LYRICS_JSON).read_text())

        count_in = (
            CountIn(bars=opts.count_in, note=opts.count_in_note) if opts.count_in > 0 else None
        )
        seqs = {p: build_sequence(notes[p], beats, count_in=count_in) for p in PROFILES}
        layout = seqs[PRIMARY].layout
        song_lines = [(t, text) for t, text in lyrics_info.get("lines_song_time", [])]
        lyric_lines = [(t + layout.shift, text) for t, text in song_lines]
        # The markers the project starts with: the countdown, then the lines found.
        countdown = (
            countdown_lines(layout.count_in_start, count_in.bars, layout.meter, 60.0 / layout.bpm)
            if count_in and song_lines
            else []
        )
        initial_markers = countdown + lyric_lines

        out_dir = self._resolve_output_dir()
        stem = job.file_stem
        outputs = {
            "lrc": out_dir / f"{stem}.lrc",
            "render": out_dir / f"{stem}.wav",
            "midi": job.path(J.OUTPUT) / f"{stem}.mid",
            "midi_daring": job.path(J.OUTPUT) / f"{stem} (daring).mid",
        }
        midi_files = {PRIMARY: outputs["midi"], "daring": outputs["midi_daring"]}
        output_params = {
            "count_in": opts.count_in,
            "count_in_note": opts.count_in_note,
            "tempo": "constant",
            "format": OUTPUT_FORMAT,
            "output_dir": str(out_dir),
        }
        fx_chain = load_fx_template()
        rpp_path = job.project_path
        duration = duration_seconds(job.path(J.ORIGINAL))

        def do_output() -> dict:
            out_dir.mkdir(parents=True, exist_ok=True)
            for profile, seq in seqs.items():
                write_midi(
                    midi_files[profile], seq, track_name=f"{stem} {profile}", lyrics=lyric_lines
                )
            # The project is ours until Reaper saves it; from then on it is the source and only
            # its lyrics can be swapped (backline lyrics), or the whole thing with --overwrite.
            kept = rpp_path.exists() and not is_generated(rpp_path) and not opts.overwrite
            if not kept:
                write_rpp(
                    rpp_path,
                    # The practice mix: real drums muted (unmute to compare), MIDI drums centred,
                    # the song quiet on the right so the drummer can hear it without it
                    # dominating; muted so the render is drums only.
                    stem=AudioTrack(
                        name=f"{stem} (drums)",
                        file=J.DRUMS,
                        duration=duration,
                        position=layout.shift,
                        pan=-1.0,
                        volume_db=STEM_VOLUME_DB,
                        muted=True,
                    ),
                    midi=[
                        MidiTrack(
                            name=f"{stem} (MIDI {profile})",
                            item_name=f"{stem} {profile}",
                            seq=seq,
                            pan=0.0,
                            muted=profile != PRIMARY,
                            fx_chain=fx_chain,
                        )
                        for profile, seq in seqs.items()
                    ],
                    original=AudioTrack(
                        name=f"{stem} (original)",
                        file=J.ORIGINAL,
                        duration=duration,
                        position=layout.shift,
                        pan=ORIGINAL_PAN,
                        volume_db=ORIGINAL_VOLUME_DB,
                        muted=True,
                    ),
                    markers=initial_markers,
                    render=RenderSettings(directory=str(out_dir), pattern=stem),
                )
            elif self.replace_lyrics and initial_markers:  # a miss keeps what is there
                # newline="" keeps Reaper's CRLF; Path.read_text() would normalise it away.
                with rpp_path.open(encoding="utf-8", newline="") as f:
                    text = f.read()
                with rpp_path.open("w", encoding="utf-8", newline="") as f:
                    f.write(replace_markers(text, initial_markers))
            markers = read_markers(rpp_path.read_text(encoding="utf-8"))
            self._write_lrc(outputs["lrc"], markers, lyrics_info, layout.shift + duration)
            removed = _tidy_renders(outputs["render"])
            write_record(
                out_dir,
                self._song_record(outputs, rpp_path, layout, lyrics_info, grid_info, markers, kept),
            )
            return {
                "shift": layout.shift,
                "count_in_seconds": layout.count_in_seconds,
                "project": rpp_path.name,
                "project_kept": kept,
                "lyrics_replaced": bool(kept and self.replace_lyrics and initial_markers),
                "renders_removed": removed,
                "lyric_lines": len(markers),
                "output_dir": str(out_dir),
                "fx_chain": fx_chain is not None,
                "detail": f"{len(markers)} lines"
                + (" from the saved project" if kept else "")
                + (" + .mid + .rpp" if not kept else " + .mid"),
            }

        self.stage(
            "output",
            output_params,
            do_output,
            [outputs["midi"], outputs["midi_daring"], job.project_path],
            detail="midi + lrc + rpp",
        )
        info = job.stage_info("output")

        return RunResult(
            job=job,
            beats=beats,
            layout=layout,
            counts={p: count_by_class(n) for p, n in notes.items()},
            lyrics=lyrics_info,
            project_path=job.dir / info.get("project", job.project_path.name),
            output_dir=out_dir,
            outputs=outputs,
            fx_chain=bool(info.get("fx_chain")),
            project_kept=bool(info.get("project_kept")),
            lyrics_replaced=bool(info.get("lyrics_replaced")),
            lyric_lines=int(info.get("lyric_lines", 0)),
            renders_removed=list(info.get("renders_removed", [])),
            deduped=self.deduped is not None,
            timings=self.timings,
        )

    def _resolve_output_dir(self) -> Path:
        """--to wins; else tracks/<set>/<NN - Title> from the config; else the job's output/.

        The result is saved into the job's options so later reruns land in the same folder
        even if other songs were added to the set in between.
        """
        job, opts, cfg = self.job, self.opts, self.config
        self.deduped: Path | None = None
        existing = find_by_hash(cfg.tracks, job.manifest["hash"]) if cfg.tracks else None
        if opts.deliver_to:
            out = job.output_dir(opts.deliver_to)
        elif existing:
            # Already imported from this very source: keep its folder, whatever the set/number.
            out = existing
            self.deduped = existing
        elif cfg.tracks and (opts.set_name or cfg.default_set):
            out = song_folder(
                cfg.tracks, opts.set_name or cfg.default_set, job.file_stem, opts.number
            )
        else:
            out = job.output_dir()
        if out != job.output_dir():
            opts.deliver_to = out
            job.manifest["options"]["deliver_to"] = str(out)
            job.save()
        return out

    def _song_record(
        self, outputs, rpp_path, layout, lyrics_info, grid_info, markers, project_kept
    ) -> dict[str, Any]:
        """What goes into the song folder's backline.json."""
        from backline import __version__

        job = self.job
        source: dict[str, Any] = {"kind": "file", "path": job.manifest["source"]}
        try:
            source = json.loads(job.path(J.SOURCE_JSON).read_text())
        except (OSError, ValueError):
            pass
        source.pop("ingested_at", None)
        lyrics = lyrics_info.get("lyrics") or {}
        return {
            "backline": __version__,
            "hash": job.manifest["hash"],
            "song": {"artist": job.artist, "title": job.title},
            "source": source,
            "job": str(job.dir),
            "project": rpp_path.name,
            "files": {
                "lyrics": outputs["lrc"].name if markers else None,
                "drums": outputs["render"].name,
            },
            "timeline": {
                "count_in_bars": self.opts.count_in,
                "count_in_seconds": layout.count_in_seconds,
                "song_starts_at": layout.shift,
                "bpm": layout.bpm,
                "meter": layout.meter,
            },
            "grid": asdict(grid_info),
            # Where the .lrc lines come from: the project once Reaper has saved it, else the
            # lookup that seeded its markers.
            "lyrics": (
                {"source": "project", "lines": len(markers)}
                if project_kept
                else {
                    "source": lyrics.get("source"),
                    "lines": len(markers),
                    "offset": lyrics.get("offset", 0.0),
                    "on_vocal": lyrics.get("confidence"),
                }
                if lyrics_info.get("found")
                else {
                    "found": False,
                    "reason": lyrics_info.get("reason") or lyrics_info.get("error"),
                }
            ),
        }

    def _do_lyrics(self) -> dict:
        """Find lyrics, check their timing against the vocal stem, write lyrics.{json,txt}.

        The .lrc files are written by the output stage, which knows the count-in shift.
        Never raises: a miss is recorded in lyrics.json so the stage is cached like any other.
        """
        from backline.audio import duration_seconds
        from backline.lyrics import (
            Hints,
            estimate_offset,
            lyrics_from_file,
            lyrics_from_lrclib,
            lyrics_to_dict,
            plain_text,
            vocal_activity,
        )

        job, opts = self.job, self.opts
        job.path(J.LYRICS_TXT).unlink(missing_ok=True)
        payload: dict[str, Any] = {"found": False, "lines_song_time": []}
        if not opts.lyrics:
            payload["reason"] = "disabled"
            self._write_lyrics_json(payload)
            return {"detail": "skipped"}

        duration = duration_seconds(job.path(J.ORIGINAL))
        hints = Hints(
            artist=opts.artist or job.artist,
            title=opts.title or job.title,
            duration=duration,
        )
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
            self._write_lyrics_json(payload)
            return {"detail": "[red]lookup failed[/]"}

        if lyrics is None:
            payload["reason"] = "no match on lrclib"
            self._write_lyrics_json(payload)
            return {"detail": "[yellow]not found[/]"}

        if lyrics.synced:
            if opts.lyrics_offset is not None:
                lyrics.offset = opts.lyrics_offset
                payload["offset"] = {"applied": lyrics.offset, "source": "manual"}
            elif job.path(J.VOCALS).exists():
                activity = vocal_activity(job.path(J.VOCALS))
                est = estimate_offset(lyrics, activity)
                lyrics.offset = est.applied
                lyrics.confidence = est.agreement_at_applied
                payload["offset"] = {
                    "applied": est.applied,
                    "suggested": est.suggested,
                    "agreement_at_zero": est.agreement_at_zero,
                    "agreement_at_applied": est.agreement_at_applied,
                    "source": "vocal stem (another edit)" if est.gross else "vocal stem",
                }
                if est.gross:
                    payload["offset"]["gross"] = est.gross
                # A sync made against another edit of the song cannot be fixed by a constant
                # shift (a longer intro here, a cut solo there): time the lines from the
                # recording itself.
                last = max(ln.time for ln in lyrics.timed)
                reason = (
                    "requested"
                    if opts.align
                    else "sync runs past the end of this recording"
                    if last > duration + 3.0
                    else "sync is for another edit"
                    if est.gross
                    else None
                )
                if reason:
                    self._align_lyrics(lyrics, activity, duration, reason, payload)

        job.path(J.LYRICS_TXT).write_text(plain_text(lyrics), encoding="utf-8")
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
        self._write_lyrics_json(payload)

        who = f"{lyrics.meta.get('artist', '?')} – {lyrics.meta.get('title', '?')}"
        kind = f"{len(lyrics.timed)} lines" if lyrics.synced else "text only"
        return {"detail": f"{who} · {kind}", "source": lyrics.source}

    def _align_lyrics(self, lyrics, activity, duration, reason, payload) -> None:
        from backline.align import realign, summary, transcribe_words, whisper_available
        from backline.lyrics import Line, estimate_offset

        job = self.job
        if not whisper_available():
            payload["offset"]["note"] = (
                f"{reason}; mlx-whisper is not installed, so only a constant shift was applied"
            )
            return
        self.reporter.stage("lyrics", "whisper on the vocal stem")
        words = transcribe_words(job.path(J.VOCALS), job.path(J.WORDS))
        timed = lyrics.timed
        aligned = realign([ln.time for ln in timed], [ln.text for ln in timed], words, duration)
        kept = [
            Line(t, ln.text) for t, ln in zip(aligned.times, timed, strict=True) if t is not None
        ]
        lyrics.lines = kept
        lyrics.offset = 0.0
        check = estimate_offset(lyrics, activity)
        lyrics.confidence = check.agreement_at_zero
        payload["offset"] = {
            "source": "whisper alignment",
            "reason": reason,
            "applied": 0.0,
            "agreement_at_applied": check.agreement_at_zero,
            "constant_shift_was": payload["offset"].get("applied", 0.0),
            **summary(aligned, [ln.time for ln in timed]),
        }

    def _write_lyrics_json(self, payload: dict[str, Any]) -> None:
        from backline.lyrics import save_lyrics_json

        save_lyrics_json(self.job.path(J.LYRICS_JSON), payload)

    def _write_lrc(self, lrc_path, markers, lyrics_info, total_seconds):
        """`<stem>.lrc` in the song folder: the project's markers, on the project timeline."""
        from backline.lyrics import format_lrc

        if not markers:
            lrc_path.unlink(missing_ok=True)
            return
        meta = lyrics_info.get("lyrics", {}).get("meta", {})
        base = lyrics_info.get("header") or {
            "ar": meta.get("artist") or self.job.artist or "",
            "ti": meta.get("title") or self.job.title,
            "al": meta.get("album", ""),
        }
        header = dict(base) | {
            "length": f"{int(total_seconds // 60):02d}:{int(total_seconds % 60):02d}",
            "re": "backline",
        }
        lrc_path.write_text(format_lrc(markers, header=header), encoding="utf-8")
