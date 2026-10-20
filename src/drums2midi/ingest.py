from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from drums2midi.audio import SAMPLE_RATE

AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".flac",
    ".aiff",
    ".aif",
    ".ogg",
    ".opus",
    ".wma",
    ".mp4",
}


class IngestError(RuntimeError):
    pass


def is_url(source: str) -> bool:
    parsed = urlparse(source)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def title_for_file(path: Path) -> str:
    return path.stem


def ingest_file(src: Path, dest: Path) -> None:
    """Convert any supported audio file to 44.1 kHz stereo float WAV at `dest`."""
    if not src.is_file():
        raise IngestError(f"file not found: {src}")
    if src.suffix.lower() not in AUDIO_EXTENSIONS:
        raise IngestError(
            f"unsupported file type {src.suffix!r}; expected one of "
            + ", ".join(sorted(AUDIO_EXTENSIONS))
        )
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise IngestError("ffmpeg not found on PATH (run `mise install`)")

    tmp = dest.with_suffix(".tmp.wav")
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "2",
        "-ar",
        str(SAMPLE_RATE),
        "-c:a",
        "pcm_f32le",
        str(tmp),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise IngestError(f"ffmpeg failed:\n{result.stderr.strip()}")
    tmp.replace(dest)
