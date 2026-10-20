from __future__ import annotations

import importlib
import logging
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
from rich.table import Table

from drums2midi import __version__
from drums2midi.job import DEFAULT_ROOT

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
        logging.getLogger("huggingface_hub").setLevel(logging.ERROR)


@app.command()
def run(
    source: str = typer.Argument(..., help="Audio file (wav, mp3, m4a, flac, aiff, ogg) or URL."),
    out: Path = typer.Option(DEFAULT_ROOT, "--out", "-o", help="Root folder for job outputs."),
    quality: Quality = typer.Option(
        Quality.fast, help="Demucs model: fast=htdemucs, best=htdemucs_ft (~4x slower)."
    ),
    style: Style = typer.Option(Style.raw, help="How much to simplify the transcription."),
    grid: int = typer.Option(
        16, help="Quantization grid: 8, 16, 12 (triplet 8ths), 24 (triplet 16ths)."
    ),
    device: Device = typer.Option(Device.auto, help="Compute device for Demucs and Beat This!."),
    count_in: int = typer.Option(2, help="Bars of count-in before the first downbeat; 0 disables."),
    count_in_note: int = typer.Option(
        37, help="MIDI note for the count-in click (37 = GM side stick)."
    ),
    keep_other: bool = typer.Option(False, help="Also save no_drums.wav."),
    force: bool = typer.Option(False, help="Ignore cached artifacts and rerun every stage."),
) -> None:
    """Analyse SOURCE and write drums.mid into the job folder."""
    from drums2midi.pipeline import RunOptions
    from drums2midi.pipeline import run as run_pipeline

    opts = RunOptions(
        out_root=out.expanduser(),
        quality=quality.value,
        style=style.value,
        grid=grid,
        device=device.value,
        count_in=count_in,
        count_in_note=count_in_note,
        keep_other=keep_other,
        force=force,
    )
    _execute(lambda reporter: run_pipeline(source, opts, reporter))


@app.command()
def resimplify(
    job_dir: Path = typer.Argument(..., help="An existing job folder."),
    style: Style = typer.Option(Style.raw),
    grid: int = typer.Option(16),
    count_in: int = typer.Option(2),
    count_in_note: int = typer.Option(37),
) -> None:
    """Regenerate notes.json and drums.mid from cached analysis (fast)."""
    from drums2midi.pipeline import RunOptions
    from drums2midi.pipeline import resimplify as resimplify_pipeline

    opts = RunOptions(style=style.value, grid=grid, count_in=count_in, count_in_note=count_in_note)
    _execute(lambda reporter: resimplify_pipeline(job_dir.expanduser(), opts, reporter))


def _execute(fn) -> None:
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


class RichReporter:
    def __init__(self) -> None:
        self.bar = Progress(
            SpinnerColumn(finished_text="[green]✓[/]"),
            TextColumn("{task.description}"),
            BarColumn(bar_width=24),
            TextColumn("{task.fields[detail]}"),
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
    counts = "  ".join(f"{k} {v}" for k, v in result.counts.items())
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column(overflow="fold")
    table.add_row("title", escape(job.title))
    table.add_row(
        "tempo",
        f"{beats.bpm:g} bpm, {beats.meter}/4, first downbeat at {beats.first_downbeat:.2f}s",
    )
    table.add_row("hits", counts)
    if layout.count_in_seconds:
        table.add_row(
            "count-in",
            f"{layout.count_in_seconds:.2f}s; "
            f"audio starts at {layout.shift:.3f}s in the DAW timeline",
        )
    table.add_row("midi", escape(str(result.midi_path)))
    table.add_row("reaper", escape(str(result.rpp_path)))
    table.add_row("folder", escape(str(job.dir)))
    console.print()
    console.print(table)


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
        from drums2midi.devices import resolve_device

        return True, resolve_device("auto")
    except Exception as e:  # noqa: BLE001
        return False, f"import failed: {e}"


@app.command()
def doctor() -> None:
    """Check that ffmpeg, yt-dlp, PyTorch and the ML packages are available."""
    checks: list[tuple[str, bool, str]] = [
        ("python", True, f"{sys.version.split()[0]}  [{sys.executable}]"),
        ("drums2midi", True, __version__),
        ("ffmpeg", *_binary_version("ffmpeg", "-version")),
        ("yt-dlp", *_binary_version("yt-dlp", "--version")),
        ("torch", *_module_version("torch")),
        ("device", *_torch_device()),
        ("demucs", *_module_version("demucs")),
        ("adtof_pytorch", *_module_version("adtof_pytorch")),
        ("beat_this", *_module_version("beat_this")),
        ("librosa", *_module_version("librosa")),
        ("mido", *_module_version("mido")),
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
