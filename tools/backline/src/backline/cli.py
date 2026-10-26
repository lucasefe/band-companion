from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import warnings
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn
from rich.table import Column, Table

from backline import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console(stderr=True)


class Quality(StrEnum):
    fast = "fast"
    best = "best"


class Style(StrEnum):
    raw = "raw"
    detailed = "detailed"
    essence = "essence"


class Device(StrEnum):
    auto = "auto"
    mps = "mps"
    cuda = "cuda"
    cpu = "cpu"


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show library warnings."),
) -> None:
    """Extract a simplified drum MIDI track from an audio file or YouTube link."""
    if not verbose:
        warnings.simplefilter("ignore")
        # huggingface_hub resets its logger level on import (which happens lazily, later),
        # so the env var is the only setting that sticks.
        os.environ.setdefault("HF_HUB_VERBOSITY", "error")


@app.command()
def add(
    source: str = typer.Argument(..., help="Audio file (wav, mp3, m4a, flac, aiff, ogg) or URL."),
    set_name: str | None = typer.Option(
        None, "--set", help="Set (tab) to add the song to; default from backline.toml."
    ),
    number: int | None = typer.Option(
        None, "--number", "-n", help="Position in the set; default: next free number."
    ),
    to: Path | None = typer.Option(
        None, "--to", help="Explicit song folder instead of tracks/<set>/<NN - Title>."
    ),
    jobs: Path | None = typer.Option(
        None,
        "--jobs",
        help="Where to keep input/, work/ and the project; default from backline.toml.",
    ),
    quality: Quality = typer.Option(
        Quality.fast, help="Demucs model: fast=htdemucs, best=htdemucs_ft (~4x slower)."
    ),
    style: Style = typer.Option(Style.raw, help="How much to simplify the transcription."),
    quantize: int = typer.Option(
        16, help="Quantization grid for the simplified styles: 8, 16, 12, 24 (planned)."
    ),
    device: Device = typer.Option(Device.auto, help="Compute device for Demucs and Beat This!."),
    grid: str = typer.Option(
        "auto",
        help="Beat grid: auto (straight when the song is played to a click), straight, tracked.",
    ),
    bpm: float | None = typer.Option(None, help="Force a straight grid at this tempo."),
    count_in: int | None = typer.Option(
        None,
        help="Bars of count-in before the first downbeat; 0 disables. Default from backline.toml.",
    ),
    count_in_note: int | None = typer.Option(None, help="MIDI note for the count-in click."),
    keep_other: bool = typer.Option(False, help="Also save work/no_drums.wav."),
    force: bool = typer.Option(False, help="Ignore cached artifacts and rerun every stage."),
    lyrics: bool = typer.Option(True, "--lyrics/--no-lyrics", help="Look up lyrics on lrclib.net."),
    artist: str | None = typer.Option(None, help="Artist name (overrides tags / YouTube title)."),
    title: str | None = typer.Option(None, help="Song title (overrides tags / YouTube title)."),
    lyrics_file: Path | None = typer.Option(
        None, "--lyrics-file", help="Use this .lrc/.txt instead of looking lyrics up."
    ),
    lyrics_offset: float | None = typer.Option(
        None, help="Shift lyrics by this many seconds instead of measuring against the vocals."
    ),
    align: bool = typer.Option(
        False, help="Time the lyric lines from the recording (Whisper), not from the lookup."
    ),
) -> None:
    """Add a song: analyse SOURCE and write the Reaper project (drums as MIDI, lyrics as
    markers) that renders into the song's folder. From then on the project is the source."""
    from backline.config import load_config
    from backline.pipeline import RunOptions
    from backline.pipeline import run as run_pipeline

    cfg = load_config()
    opts = RunOptions(
        out_root=(jobs or cfg.jobs).expanduser(),
        deliver_to=to,
        set_name=set_name,
        number=number,
        quality=quality.value,
        style=style.value,
        quantize=quantize,
        device=device.value,
        grid=grid,
        bpm=bpm,
        count_in=cfg.count_in_bars if count_in is None else count_in,
        count_in_note=cfg.count_in_note if count_in_note is None else count_in_note,
        keep_other=keep_other,
        force=force,
        lyrics=lyrics,
        artist=artist,
        title=title,
        lyrics_file=lyrics_file,
        lyrics_offset=lyrics_offset,
        align=align,
    )
    _execute(lambda reporter: run_pipeline(source, opts, reporter, cfg))


@app.command()
def resimplify(
    job_dir: Path = typer.Argument(..., help="An existing job folder."),
    style: Style | None = typer.Option(None),
    quantize: int | None = typer.Option(None),
    count_in: int | None = typer.Option(None),
    count_in_note: int | None = typer.Option(None),
    to: Path | None = typer.Option(None, "--to", help="Song folder for the .lrc and the render."),
    grid: str | None = typer.Option(None, help="auto | straight | tracked"),
    bpm: float | None = typer.Option(None, help="Force a straight grid at this tempo."),
    overwrite: bool = typer.Option(
        False, help="Regenerate the project even if Reaper has saved it (your edits are lost)."
    ),
) -> None:
    """Regenerate notes, MIDI and the Reaper project from cached analysis (fast).

    A project Reaper has saved is kept (it is the source); the new MIDI lands in output/ for
    you to import, unless --overwrite."""
    from backline.pipeline import resimplify as resimplify_pipeline

    _execute(
        lambda reporter: resimplify_pipeline(
            job_dir.expanduser(),
            reporter,
            style=style.value if style else None,
            quantize=quantize,
            count_in=count_in,
            count_in_note=count_in_note,
            deliver_to=to,
            grid=grid,
            bpm=bpm,
            overwrite=overwrite or None,
        )
    )


@app.command()
def export(
    job_dir: Path | None = typer.Argument(
        None, help="A job folder; without one, every song under tracks/ with a job."
    ),
) -> None:
    """Update the song folder from the project as saved in Reaper: every marker becomes a
    line of the .lrc, and the newest render becomes <Title>.wav. Run it after editing in
    Reaper; `mise run sync-audio` runs it for every song first."""
    from backline.pipeline import export as export_pipeline
    from backline.pipeline import export_all

    if job_dir is not None:
        _execute(lambda reporter: export_pipeline(job_dir.expanduser(), reporter))
        return
    results = export_all()
    if not results:
        console.print("[dim]no song folders with a job under tracks/[/]")
        return
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column(overflow="fold")
    failed = 0
    for folder, result in results:
        if isinstance(result, Exception):
            failed += 1
            table.add_row(escape(folder.name), f"[red]{escape(str(result))}[/]")
            continue
        render = result.outputs["render"]
        if not render.exists():
            state = "[yellow]not rendered[/]"
        elif render.stat().st_mtime < result.project_path.stat().st_mtime:
            state = "[yellow]render older than the project[/]"
        else:
            state = "rendered"
        lines = f"{result.lyric_lines} lines" if result.lyric_lines else "[yellow]no lyrics[/]"
        tidy = (
            f"  [dim](kept the newest render; removed {', '.join(result.renders_removed)})[/]"
            if result.renders_removed
            else ""
        )
        table.add_row(escape(folder.name), f"{lines} · {state}{tidy}")
    console.print(table)
    if failed:
        raise typer.Exit(code=1)


@app.command()
def rename(
    job_dir: Path = typer.Argument(..., help="An existing job folder."),
    title: str | None = typer.Option(None, help="The song's title."),
    artist: str | None = typer.Option(None, help="The artist."),
) -> None:
    """Fix a song's title/artist: renames the job, the project, the song folder and its files
    (rendered audio included) and regenerates the outputs under the new name."""
    from backline.pipeline import rename as rename_pipeline

    if title is None and artist is None:
        console.print("[red]error:[/] give --title and/or --artist")
        raise typer.Exit(code=2)
    _execute(
        lambda reporter: rename_pipeline(
            job_dir.expanduser(), title=title, artist=artist, reporter=reporter
        )
    )


@app.command(name="lyrics")
def lyrics_cmd(
    job_dir: Path = typer.Argument(..., help="An existing job folder."),
    artist: str | None = typer.Option(None, help="Artist name for the lookup."),
    title: str | None = typer.Option(None, help="Song title for the lookup."),
    lyrics_file: Path | None = typer.Option(None, "--lyrics-file", help="Use this .lrc/.txt."),
    lyrics_offset: float | None = typer.Option(
        None, "--offset", help="Shift lyrics by this many seconds (skips the vocal-stem check)."
    ),
    align: bool = typer.Option(
        False, help="Time the lines from the recording (Whisper) even if the sync looks fine."
    ),
    replace: bool = typer.Option(
        False,
        help="Swap the markers of a project Reaper has saved for the lines found "
        "(your lyric edits in Reaper are lost). Close the project in Reaper first.",
    ),
) -> None:
    """Look the lyrics up again (with hints). A project that is still ours is regenerated with
    the result; one Reaper has saved keeps its markers unless --replace. To update the .lrc
    from the markers you edited in Reaper, use `backline export`."""
    from backline.pipeline import redo_lyrics

    result = _execute(
        lambda reporter: redo_lyrics(
            job_dir.expanduser(),
            reporter,
            replace=replace,
            lyrics=True,
            artist=artist,
            title=title,
            lyrics_file=lyrics_file,
            lyrics_offset=lyrics_offset,
            align=align or None,
        )
    )
    if result.project_kept and not result.lyrics_replaced:
        found = len(result.lyrics.get("lines_song_time", []))
        if found:
            console.print(
                f"[yellow]The project was saved by Reaper, so its {result.lyric_lines} markers "
                f"were kept; the lookup found {found} lines.[/] Run again with [bold]--replace[/] "
                "to swap them in (close the project in Reaper first), or edit the markers in "
                "Reaper and `backline export`."
            )


@app.command(name="fx-template")
def fx_template(
    project: Path | None = typer.Argument(
        None, help="A Reaper project (.RPP or .rpp-bak) whose MIDI track has your drum instrument."
    ),
    track: str = typer.Option("MIDI", help="Pick the first track whose name contains this."),
    clear: bool = typer.Option(False, help="Remove the stored template."),
) -> None:
    """Store the FX chain (drum VSTi) to pre-load on the MIDI tracks of generated projects."""
    from backline.reaper import FX_TEMPLATE_PATH, extract_fx_chain

    if clear:
        FX_TEMPLATE_PATH.unlink(missing_ok=True)
        console.print(f"removed {escape(str(FX_TEMPLATE_PATH))}")
        return
    if project is None:
        if FX_TEMPLATE_PATH.exists():
            first = FX_TEMPLATE_PATH.read_text().splitlines()
            plugin = next((ln.strip() for ln in first if ln.lstrip().startswith("<")), "?")
            console.print(f"template: {escape(str(FX_TEMPLATE_PATH))}\n  {escape(plugin[:100])}")
        else:
            console.print("no template stored; pass a Reaper project to extract one")
        return
    chain = extract_fx_chain(
        project.expanduser().read_text(encoding="utf-8", errors="replace"), track_contains=track
    )
    if not chain:
        console.print(
            f"[red]no FX chain found[/] on a track containing {track!r} in {escape(str(project))}"
        )
        raise typer.Exit(code=1)
    FX_TEMPLATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FX_TEMPLATE_PATH.write_text(chain, encoding="utf-8")
    plugin = next((ln.strip() for ln in chain.splitlines() if ln.lstrip().startswith("<")), "?")
    console.print(
        f"stored {escape(str(FX_TEMPLATE_PATH))}\n  {escape(plugin[:100])}\n"
        "New projects (and `resimplify`) will load it on the MIDI tracks."
    )


def _execute(fn):
    reporter = RichReporter()
    try:
        with reporter:
            result = fn(reporter)
    except KeyboardInterrupt:
        console.print("[yellow]cancelled[/]")
        raise typer.Exit(code=130) from None
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]error:[/] {escape(str(e))}")
        raise typer.Exit(code=1) from None
    _print_summary(result)
    return result


class RichReporter:
    def __init__(self) -> None:
        self.bar = Progress(
            SpinnerColumn(finished_text="[green]✓[/]"),
            TextColumn("{task.description}", table_column=Column(no_wrap=True, min_width=11)),
            BarColumn(bar_width=24),
            TextColumn(
                "{task.fields[detail]}", table_column=Column(no_wrap=True, overflow="ellipsis")
            ),
            TimeElapsedColumn(),
            console=console,
        )
        self.task: TaskID | None = None

    def __enter__(self) -> RichReporter:
        self.bar.start()
        return self

    def __exit__(self, *exc) -> None:
        self.bar.stop()

    def stage(self, name: str, detail: str = "", determinate: bool = False) -> None:
        self.task = self.bar.add_task(
            f"{name:<11}", total=1.0 if determinate else None, detail=detail
        )

    def progress(self, fraction: float) -> None:
        if self.task is not None:
            self.bar.update(self.task, completed=fraction)

    def finish(self, detail: str = "") -> None:
        if self.task is not None:
            self.bar.update(self.task, total=1.0, completed=1.0, detail=detail)

    def skipped(self, name: str, detail: str = "") -> None:
        task = self.bar.add_task(f"{name:<11}", total=1.0, detail=f"[dim]{detail}[/]")
        self.bar.update(task, completed=1.0)  # update() is what marks a task finished


def _print_summary(result) -> None:
    job, beats, layout = result.job, result.beats, result.layout
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column(overflow="fold")
    song = f"{job.artist} – {job.title}" if job.artist else job.title
    table.add_row("song", escape(song))
    grid = job.stage_info("tempo").get("grid") or {}
    fit = ""
    if grid.get("residual_p95_ms") is not None:
        fit = f"; fit p95 {grid['residual_p95_ms']:g} ms, max {grid['residual_max_ms']:g} ms"
    note = f" [dim]({grid['reason']})[/]" if grid.get("reason") else ""
    table.add_row(
        "tempo",
        f"{beats.bpm:g} bpm, {beats.meter}/4, first downbeat at {beats.first_downbeat:.2f}s "
        f"— {grid.get('mode', '?')} grid{fit}{note}",
    )
    for profile, counts in result.counts.items():
        table.add_row(profile, "  ".join(f"{k} {v}" for k, v in counts.items()))
    table.add_row("lyrics", _lyrics_summary(result.lyrics))
    if layout.count_in_seconds:
        table.add_row(
            "count-in",
            f"{layout.count_in_seconds:.2f}s; the song starts at {layout.shift:.3f}s",
        )
    table.add_row("job", escape(str(job.dir)))
    project_note = ""
    if result.project_kept:
        project_note = "  [dim](saved by Reaper: kept as the source; new MIDI in output/)[/]"
    elif not result.fx_chain:
        project_note = "  [dim](no drum VSTi template yet; see `backline fx-template`)[/]"
    table.add_row("project", escape(str(result.project_path)) + project_note)
    folder_note = (
        "  [dim](already imported from this source; folder reused)[/]" if result.deduped else ""
    )
    table.add_row("song folder", escape(str(result.output_dir)) + folder_note)
    if result.lyric_lines:
        origin = "the project's markers" if result.project_kept else "the lookup"
        table.add_row(
            "  written",
            f"{escape(result.outputs['lrc'].name)}  ({result.lyric_lines} lines from {origin})",
        )
    else:
        table.add_row("  written", "[yellow]no .lrc: the project has no markers[/]")
    render = result.outputs["render"]
    if result.renders_removed:
        table.add_row(
            "  renders",
            f"kept the newest as {escape(render.name)}; removed "
            + escape(", ".join(result.renders_removed)),
        )
    if not render.exists():
        table.add_row("  to render", escape(render.name))
    elif render.stat().st_mtime < result.project_path.stat().st_mtime:
        table.add_row(
            "  rendered",
            f"{escape(render.name)}  [yellow](older than the project; render again?)[/]",
        )
    else:
        table.add_row("  rendered", escape(render.name))
    console.print()
    console.print(table)
    console.print()
    if render.exists():
        console.print(
            "[bold]Next:[/] edit in Reaper (drums; lyrics are the markers), save, "
            "render → `backline export`; then `mise run sync-audio`."
        )
    else:
        console.print(
            "[bold]Next:[/] open the project, File → Render (pre-filled) → "
            f"[bold]{escape(render.name)}[/] completes the song folder; then `mise run sync-audio`."
            + ("" if result.lyric_lines else "  [yellow](no lyrics: add markers in Reaper)[/]")
        )


def _lyrics_summary(info: dict) -> str:
    if not info.get("found"):
        reason = info.get("error") or info.get("reason") or "not found"
        hints = info.get("hints") or {}
        looked = " / ".join(str(v) for v in (hints.get("artist"), hints.get("title")) if v)
        return f"[yellow]{escape(reason)}[/]" + (
            f"  (looked for: {escape(looked)})" if looked else ""
        )
    ly = info["lyrics"]
    meta = ly.get("meta", {})
    who = f"{meta.get('artist', '?')} – {meta.get('title', '?')}"
    n = len(info.get("lines_song_time", []))
    parts = [
        escape(who),
        f"{n} timed lines" if ly.get("synced") else "plain text only",
        ly["source"],
    ]
    off = info.get("offset") or {}
    if off.get("source") == "manual":
        parts.append(f"offset {off['applied']:+.2f}s (manual)")
    elif off.get("source") == "whisper alignment":
        parts.append(
            f"[yellow]timed from the recording[/] ({escape(off['reason'])}): "
            f"{off['lines_heard']}/{off['lines']} lines heard"
            + (f", {off['lines_dropped']} past the end" if off.get("lines_dropped") else "")
            + f", shifts {off.get('shift_min', 0):+.1f}…{off.get('shift_max', 0):+.1f}s"
        )
        if off.get("agreement_at_applied") is not None:
            parts.append(f"on-vocal {off['agreement_at_applied']:.0%}")
    elif off:
        applied, suggested = off.get("applied", 0.0), off.get("suggested", 0.0)
        if applied:
            parts.append(f"[yellow]offset {applied:+.2f}s applied[/]")
        elif abs(suggested) >= 0.3:
            parts.append(f"offset 0 (vocals suggest {suggested:+.2f}s)")
        if off.get("agreement_at_applied") is not None:
            parts.append(f"on-vocal {off['agreement_at_applied']:.0%}")
        if off.get("note"):
            parts.append(f"[yellow]{escape(off['note'])}[/]")
    return " · ".join(parts)


def _whisper_status() -> tuple[bool, str]:
    from backline.align import WHISPER_MODEL

    ok, version = _module_version("mlx_whisper")
    if not ok:
        return False, "not installed (Apple Silicon only) — lyrics synced to another edit stay off"
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    cached = (hub / ("models--" + WHISPER_MODEL.replace("/", "--"))).exists()
    state = "cached" if cached else "downloads on first use (~1.6 GB)"
    return True, f"{version}; {WHISPER_MODEL} {state}"


def _binary_version(name: str, *args: str) -> tuple[bool, str]:
    path = shutil.which(name)
    if not path:
        return False, "not found on PATH"
    try:
        out = subprocess.run([path, *args], capture_output=True, text=True, timeout=10)
        first = (out.stdout or out.stderr).strip().splitlines()[0]
    except Exception as e:  # noqa: BLE001
        return False, f"{path} ({e})"
    return True, f"{first}  [{path}]"


def _module_version(name: str) -> tuple[bool, str]:
    try:
        mod = importlib.import_module(name)
    except Exception as e:  # noqa: BLE001
        return False, f"import failed: {e}"
    return True, getattr(mod, "__version__", "ok")


def _torch_device() -> tuple[bool, str]:
    try:
        from backline.devices import resolve_device

        return True, resolve_device("auto")
    except Exception as e:  # noqa: BLE001
        return False, f"import failed: {e}"


@app.command()
def doctor() -> None:
    """Check that ffmpeg, yt-dlp, PyTorch and the ML packages are available."""
    checks: list[tuple[str, bool, str]] = [
        ("python", True, f"{sys.version.split()[0]}  [{sys.executable}]"),
        ("backline", True, __version__),
        ("ffmpeg", *_binary_version("ffmpeg", "-version")),
        ("yt-dlp", *_binary_version("yt-dlp", "--version")),
        ("torch", *_module_version("torch")),
        ("device", *_torch_device()),
        ("demucs", *_module_version("demucs")),
        ("adtof_pytorch", *_module_version("adtof_pytorch")),
        ("beat_this", *_module_version("beat_this")),
        ("librosa", *_module_version("librosa")),
        ("mido", *_module_version("mido")),
        ("mlx_whisper", *_whisper_status()),
    ]

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_column(overflow="fold")
    for name, ok, detail in checks:
        table.add_row(name, "[green]✓[/]" if ok else "[red]✗[/]", escape(detail))
    console.print(table)

    if not all(ok for _, ok, _ in checks):
        raise typer.Exit(code=1)
