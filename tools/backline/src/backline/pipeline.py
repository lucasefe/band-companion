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
OUTPUT_FORMAT = 8  # bump when the output stage writes different files/content for the same inputs


@dataclass
class RunOptions:
    out_root: Path = DEFAULT_ROOT
    deliver_to: Path | None = None  # explicit song folder for .lrc, reference and the render
    set_name: str | None = None  # else tracks/<set>/<NN - Title> from backline.toml
    number: int | None = None
    quality: str = "fast"
    style: str = "raw"
    grid: int = 16
    device: str = "auto"
    tempo_model: str = "final"
    count_in: int = 2
    count_in_note: int = 37
    metronome: str = "gaps"  # gaps | always | off
    metronome_min_bars: int = 1
    metronome_silence_db: float = -30.0
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
    layout: Any
    counts: dict[str, dict[str, int]]  # profile -> class -> hits
    lyrics: dict[str, Any]  # contents of lyrics.json
    project_path: Path
    output_dir: Path
    outputs: dict[str, Path]  # lrc / reference / midi / render target
    fx_chain: bool
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
    **overrides: Any,
) -> RunResult:
    """Force the lyrics stage (and therefore the outputs) with the saved options plus overrides."""
    job = Job.open(job_dir)
    opts = options_from_job(job, **overrides)
    return _Runner(job, opts, reporter or NullReporter(), rerun={"lyrics"}, config=config).process()


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

    # Project files keep working under the new name; the output stage refreshes their contents.
    for suffix in (".RPP", ".new.RPP"):
        old_path = job.dir / f"{old_stem}{suffix}"
        if old_path.exists() and new_stem != old_stem:
            old_path.rename(job.dir / f"{new_stem}{suffix}")

    # Song folder: keep its number, rename the folder and the deliverables inside it.
    saved = job.manifest.get("options", {})
    folder = Path(saved["deliver_to"]) if saved.get("deliver_to") else None
    if folder and folder.is_dir() and new_stem != old_stem:
        for ext in (".lrc", ".wav", ".reference.wav", ".mid"):
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
    saved = job.manifest.get("options", {})
    kwargs = {k: v for k, v in saved.items() if k in RunOptions.__dataclass_fields__}
    kwargs.update({k: v for k, v in overrides.items() if v is not None})
    for key in ("out_root", "deliver_to", "lyrics_file"):
        if kwargs.get(key):
            kwargs[key] = Path(kwargs[key])
    kwargs["force"] = bool(overrides.get("force", False))
    return RunOptions(**kwargs)


def project_target(job: Job) -> Path:
    """Where to write the Reaper project: never over a file Reaper has saved (user edits)."""
    from backline.reaper import is_generated

    main = job.project_path
    if main.exists() and not is_generated(main):
        return main.with_name(f"{job.file_stem}.new.RPP")
    return main


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
        config: Config | None = None,
    ) -> None:
        self.job = job
        self.opts = opts
        self.reporter = reporter
        self.rerun = rerun or set()
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
        from backline.metronome import detect_gaps, gaps_in_seconds, write_metronome
        from backline.midi_out import CountIn, build_sequence, write_midi
        from backline.reaper import (
            ORIGINAL_PAN,
            ORIGINAL_VOLUME_DB,
            STEM_VOLUME_DB,
            AudioTrack,
            MidiTrack,
            RenderSettings,
            load_fx_template,
            write_rpp,
        )
        from backline.reference import write_reference
        from backline.separate import MODELS, separate
        from backline.simplify import (
            PROFILES,
            count_by_class,
            load_notes,
            save_notes,
            simplify,
        )
        from backline.tempo import Beats, detect_beats
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
            beats = detect_beats(job.path(J.ORIGINAL), device=opts.device, model=opts.tempo_model)
            beats.save(job.path(J.BEATS))
            return {"bpm": beats.bpm, "meter": beats.meter}

        self.stage("tempo", {"model": opts.tempo_model}, do_tempo, [J.BEATS], detail="beat_this")
        beats = Beats.load(job.path(J.BEATS))

        def do_simplify() -> dict:
            activations = load_activations(job.path(J.ACTIVATIONS))
            counts = {}
            for profile, thresholds in PROFILES.items():
                notes = simplify(
                    activations, beats, style=opts.style, grid=opts.grid, thresholds=thresholds
                )
                save_notes(
                    job.path(J.notes_file(profile, PRIMARY)),
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

        out_dir = self._resolve_output_dir()
        stem = job.file_stem
        outputs = {
            "lrc": out_dir / f"{stem}.lrc",
            "reference": out_dir / f"{stem}.reference.wav",
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
            "metronome": [opts.metronome, opts.metronome_min_bars, opts.metronome_silence_db],
        }
        fx_chain = load_fx_template()
        gaps = (
            detect_gaps(
                job.path(J.DRUMS),
                beats,
                min_bars=opts.metronome_min_bars,
                silence_db=opts.metronome_silence_db,
            )
            if opts.metronome != "off"
            else []
        )
        gap_info = gaps_in_seconds(gaps, beats, layout.shift)

        def do_output() -> dict:
            out_dir.mkdir(parents=True, exist_ok=True)
            for profile, seq in seqs.items():
                write_midi(
                    midi_files[profile], seq, track_name=f"{stem} {profile}", lyrics=lyric_lines
                )
            self._write_lrc_files(
                outputs["lrc"], lyrics_info, song_lines, lyric_lines, layout, count_in
            )
            write_reference(job.path(J.ORIGINAL), outputs["reference"], layout, count_in)
            duration = duration_seconds(job.path(J.ORIGINAL))
            write_metronome(
                job.path(J.METRONOME),
                layout,
                beats,
                gaps,
                mode=opts.metronome,
                count_in=count_in,
                tail_seconds=max(0.0, layout.shift + duration - (beats.beats[-1] + layout.shift)),
            )
            rpp_path = project_target(job)
            write_rpp(
                rpp_path,
                # The practice mix: real drums muted (unmute to compare), MIDI drums centred,
                # the song quiet on the right so the drummer can hear it without it dominating.
                stem=AudioTrack(
                    name=f"{stem} (drums)",
                    file=J.DRUMS,
                    duration=duration,
                    position=layout.shift,
                    pan=-1.0,
                    volume_db=STEM_VOLUME_DB,
                    muted=True,
                ),
                # The render is the band's track: metronome hard left, MIDI drums hard right,
                # so the two can be routed separately (click to in-ears, drums to the PA).
                metronome=AudioTrack(
                    name=f"{stem} (metronome)",
                    file=J.METRONOME,
                    duration=layout.shift + duration,
                    position=0.0,
                    pan=-1.0,
                ),
                midi=[
                    MidiTrack(
                        name=f"{stem} (MIDI {profile})",
                        item_name=f"{stem} {profile}",
                        seq=seq,
                        pan=1.0,
                        muted=profile != PRIMARY,
                        fx_chain=fx_chain,
                    )
                    for profile, seq in seqs.items()
                ],
                # Muted so the render is drums only; the app mixes the reference in live.
                original=AudioTrack(
                    name=f"{stem} (original)",
                    file=J.ORIGINAL,
                    duration=duration,
                    position=layout.shift,
                    pan=ORIGINAL_PAN,
                    volume_db=ORIGINAL_VOLUME_DB,
                    muted=True,
                ),
                markers=lyric_lines,
                render=RenderSettings(directory=str(out_dir), pattern=stem),
            )
            write_record(
                out_dir, self._song_record(outputs, rpp_path, layout, lyrics_info, gap_info)
            )
            return {
                "shift": layout.shift,
                "count_in_seconds": layout.count_in_seconds,
                "project": rpp_path.name,
                "output_dir": str(out_dir),
                "fx_chain": fx_chain is not None,
            }

        self.stage(
            "output",
            output_params,
            do_output,
            [
                outputs["midi"],
                outputs["midi_daring"],
                outputs["reference"],
                J.METRONOME,
                job.project_path,
            ],
            detail="midi + lrc + reference + metronome + rpp",
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

    def _song_record(self, outputs, rpp_path, layout, lyrics_info, gaps) -> dict[str, Any]:
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
                "lyrics": outputs["lrc"].name if lyrics_info.get("found") else None,
                "reference": outputs["reference"].name,
                "drums": outputs["render"].name,
            },
            "timeline": {
                "count_in_bars": self.opts.count_in,
                "count_in_seconds": layout.count_in_seconds,
                "song_starts_at": layout.shift,
                "bpm": layout.bpm,
                "meter": layout.meter,
            },
            "metronome": {"mode": self.opts.metronome, "gaps": gaps},
            "lyrics": {
                "source": lyrics.get("source"),
                "offset": lyrics.get("offset", 0.0),
                "on_vocal": lyrics.get("confidence"),
            }
            if lyrics_info.get("found")
            else {"found": False, "reason": lyrics_info.get("reason") or lyrics_info.get("error")},
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
                est = estimate_offset(lyrics, vocal_activity(job.path(J.VOCALS)))
                lyrics.offset = est.applied
                lyrics.confidence = est.agreement_at_applied
                payload["offset"] = {
                    "applied": est.applied,
                    "suggested": est.suggested,
                    "agreement_at_zero": est.agreement_at_zero,
                    "agreement_at_applied": est.agreement_at_applied,
                    "source": "vocal stem",
                }

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

    def _write_lyrics_json(self, payload: dict[str, Any]) -> None:
        from backline.lyrics import save_lyrics_json

        save_lyrics_json(self.job.path(J.LYRICS_JSON), payload)

    def _write_lrc_files(self, lrc_path, lyrics_info, song_lines, lyric_lines, layout, count_in):
        """`<stem>.lrc` on the count-in timeline (with a visible countdown) in the output folder;
        `work/lyrics-original.lrc` in song time for the untouched original."""
        from backline.audio import duration_seconds
        from backline.lyrics import countdown_lines, format_lrc

        job = self.job
        lrc_path.unlink(missing_ok=True)
        job.path(J.LYRICS_ORIGINAL).unlink(missing_ok=True)
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
                "re": "backline",
            }

        duration = duration_seconds(job.path(J.ORIGINAL))
        countdown = (
            countdown_lines(layout.count_in_start, count_in.bars, layout.meter, 60.0 / layout.bpm)
            if count_in
            else []
        )
        lrc_path.write_text(
            format_lrc(countdown + lyric_lines, header=header(layout.shift + duration)),
            encoding="utf-8",
        )
        job.path(J.LYRICS_ORIGINAL).write_text(
            format_lrc(song_lines, header=header(duration)), encoding="utf-8"
        )
