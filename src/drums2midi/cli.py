from __future__ import annotations

import importlib
import shutil
import subprocess
import sys

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from drums2midi import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console(stderr=True)


@app.callback()
def main() -> None:
    """Extract a simplified drum MIDI track from an audio file or YouTube link."""


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
        import torch
    except Exception as e:  # noqa: BLE001
        return False, f"import failed: {e}"
    if torch.backends.mps.is_available():
        return True, "mps (Apple Silicon GPU)"
    if torch.cuda.is_available():
        return True, f"cuda ({torch.cuda.get_device_name(0)})"
    return True, "cpu only"


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
