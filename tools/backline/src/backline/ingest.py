from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backline.audio import SAMPLE_RATE

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
    ".webm",
    ".mka",
    ".mkv",
}

YOUTUBE_META_KEYS = (
    "id",
    "title",
    "track",
    "artist",
    "album",
    "channel",
    "uploader",
    "duration",
    "webpage_url",
    "upload_date",
)


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
    _convert(src, dest)


# --- URLs (yt-dlp) ----------------------------------------------------------------


def probe_url(url: str) -> dict[str, Any]:
    """Metadata only, no download. Raises IngestError when yt-dlp can't resolve the URL."""
    out = subprocess.run(
        [_yt_dlp(), "--dump-json", "--skip-download", "--no-playlist", "--no-warnings", url],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if out.returncode != 0 or not out.stdout.strip():
        raise IngestError(f"yt-dlp could not read {url}:\n{out.stderr.strip()[-800:]}")
    info = json.loads(out.stdout.strip().splitlines()[-1])
    return {k: info.get(k) for k in YOUTUBE_META_KEYS}


_PERCENT = re.compile(r"download:\s*([\d.]+)%")


def ingest_url(
    url: str,
    dest: Path,
    workdir: Path,
    *,
    progress: Callable[[float], None] | None = None,
) -> Path:
    """Download the best audio stream to `workdir/source.<ext>` and convert it to `dest`."""
    for old in workdir.glob("source.*"):
        old.unlink()
    cmd = [
        _yt_dlp(),
        "-f",
        "bestaudio/best",
        "--no-playlist",
        "--no-warnings",
        "--newline",
        "--progress-template",
        "download:%(progress._percent_str)s",
        "-o",
        str(workdir / "source.%(ext)s"),
        url,
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    tail: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        tail = (tail + [line.rstrip()])[-20:]
        m = _PERCENT.search(line)
        if m and progress:
            progress(min(float(m.group(1)) / 100, 0.99))
    if proc.wait() != 0:
        raise IngestError("yt-dlp download failed:\n" + "\n".join(tail))

    downloaded = sorted(workdir.glob("source.*"))
    if not downloaded:
        raise IngestError("yt-dlp reported success but no file was written")
    _convert(downloaded[0], dest)
    if progress:
        progress(1.0)
    return downloaded[0]


# --- shared ----------------------------------------------------------------------------


def _convert(src: Path, dest: Path) -> None:
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


def _yt_dlp() -> str:
    path = shutil.which("yt-dlp")
    if not path:
        raise IngestError("yt-dlp not found on PATH (run `mise install`)")
    return path
