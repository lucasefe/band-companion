from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from drums2midi import lrclib


@dataclass
class Line:
    time: float | None  # seconds in song time; None for untimed text
    text: str


@dataclass
class Lyrics:
    lines: list[Line]
    source: str  # "file:<name>" | "lrclib:<id>"
    synced: bool
    meta: dict[str, Any] = field(default_factory=dict)
    offset: float = 0.0  # seconds added to the source timing to match our audio
    confidence: float | None = None  # 0..1 agreement with the vocal stem, if measured

    @property
    def timed(self) -> list[Line]:
        return [ln for ln in self.lines if ln.time is not None and ln.text.strip()]

    def shifted(self, seconds: float) -> list[Line]:
        return [Line(ln.time + seconds, ln.text) for ln in self.timed]


@dataclass
class Hints:
    """Everything we know about which song this is."""

    artist: str | None = None
    title: str | None = None
    album: str | None = None
    duration: float | None = None

    def candidates(self) -> list[tuple[str | None, str]]:
        """(artist, title) pairs to try, most specific first."""
        out: list[tuple[str | None, str]] = []
        if self.title:
            if self.artist:
                out.append((self.artist, self.title))
            out.append((None, self.title))
        return out


# --- LRC ---------------------------------------------------------------------

_TIMESTAMP = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")


def parse_lrc(text: str) -> list[Line]:
    lines: list[Line] = []
    for raw in text.splitlines():
        stamps = _TIMESTAMP.findall(raw)
        body = _TIMESTAMP.sub("", raw).strip()
        if not stamps:
            if body and not re.match(r"^\[\w+:.*\]$", raw.strip()):
                lines.append(Line(None, body))
            continue
        for mm, ss, frac in stamps:
            ms = int(frac.ljust(3, "0")) if frac else 0
            lines.append(Line(int(mm) * 60 + int(ss) + ms / 1000, body))
    lines.sort(key=lambda ln: (ln.time is None, ln.time or 0))
    return lines


def format_lrc(lyrics: Lyrics, *, header: dict[str, str] | None = None) -> str:
    out = [f"[{k}:{v}]" for k, v in (header or {}).items() if v]
    for ln in lyrics.timed:
        t = ln.time + lyrics.offset
        if t < 0:
            t = 0.0
        out.append(f"[{int(t // 60):02d}:{t % 60:05.2f}]{ln.text}")
    return "\n".join(out) + "\n"


def plain_text(lyrics: Lyrics) -> str:
    return "\n".join(ln.text for ln in lyrics.lines if ln.text.strip()) + "\n"


# --- identification ------------------------------------------------------------

_TRACK_NO = re.compile(r"^\s*\d{1,3}\s*[-._)]\s*")
_VIDEO_NOISE = re.compile(
    r"\s*[\(\[][^\)\]]*(official|video|audio|lyric|visualizer|hd|4k|remaster|live)[^\)\]]*[\)\]]",
    re.I,
)
_FEAT = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+.*$", re.I)


def split_artist_title(name: str) -> tuple[str | None, str]:
    """'Juanes - Fotografia ft. Nelly Furtado (Official Video)' → ('Juanes', 'Fotografia')."""
    s = _VIDEO_NOISE.sub("", _TRACK_NO.sub("", name)).strip()
    s = re.sub(r"\s*\|.*$", "", s)
    artist: str | None = None
    for sep in (" - ", " – ", " — ", " | "):
        if sep in s:
            artist, s = (p.strip() for p in s.split(sep, 1))
            break
    title = _FEAT.sub("", s).strip(" -–—")
    return artist or None, title


def hints_from_file(path: Path, duration: float) -> Hints:
    """Embedded tags first, then the file name."""
    tags = _ffprobe_tags(path)
    artist, title = split_artist_title(path.stem)
    return Hints(
        artist=tags.get("artist") or tags.get("album_artist") or artist,
        title=tags.get("title") or title,
        album=tags.get("album"),
        duration=duration,
    )


def hints_from_youtube(meta: dict[str, Any], duration: float) -> Hints:
    """yt-dlp metadata: 'track'/'artist' exist on auto-generated uploads, else parse the title."""
    artist, title = split_artist_title(meta.get("title") or "")
    return Hints(
        artist=meta.get("artist") or artist or meta.get("channel") or meta.get("uploader"),
        title=meta.get("track") or title,
        album=meta.get("album"),
        duration=duration,
    )


def _ffprobe_tags(path: Path) -> dict[str, str]:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format_tags",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        tags = json.loads(out.stdout or "{}").get("format", {}).get("tags", {})
    except Exception:  # noqa: BLE001
        return {}
    return {k.lower(): v for k, v in tags.items()}


# --- lookup ----------------------------------------------------------------------


def lyrics_from_file(path: Path) -> Lyrics:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = parse_lrc(text)
    synced = any(ln.time is not None for ln in lines)
    return Lyrics(lines=lines, source=f"file:{path.name}", synced=synced)


def lyrics_from_lrclib(hints: Hints) -> tuple[Lyrics | None, list[dict[str, Any]]]:
    """Best lrclib match for the hints, plus a summary of everything considered."""
    considered: list[dict[str, Any]] = []
    for artist, title in hints.candidates():
        found = lrclib.search(artist=artist, title=title)
        if not found and artist:
            found = lrclib.search(query=f"{artist} {title}")
        ranked = lrclib.rank(found, title=title, artist=hints.artist, duration=hints.duration)
        considered += [c.summary() for c in ranked[:5]]
        best = ranked[0] if ranked else None
        if best and best.score >= lrclib.ACCEPT_SCORE:
            if best.synced:
                lines, synced = parse_lrc(best.synced), True
            else:
                lines, synced = [Line(None, t) for t in best.plain.splitlines()], False
            meta = {
                "artist": best.artist,
                "title": best.title,
                "album": best.album,
                "duration": best.duration,
                "score": round(best.score, 3),
            }
            return Lyrics(lines, f"lrclib:{best.id}", synced, meta), considered
    return None, considered


# --- timing check against the vocal stem -----------------------------------------

FRAME_RATE = 100
MAX_OFFSET = 6.0
# LRC lines are conventionally tagged a little before the word lands, and energy envelopes lag
# true onsets slightly, so sub-half-second disagreements are normal. Only a gross mismatch
# (wrong edition, trimmed intro) is corrected automatically.
APPLY_MIN_SHIFT = 0.5
APPLY_MIN_GAIN = 0.10


@dataclass
class OffsetEstimate:
    suggested: float  # shift with the strongest onset contrast
    applied: float  # 0.0 unless the suggestion is a gross mismatch
    agreement_at_zero: float  # fraction of lines starting on sung audio, unshifted
    agreement_at_applied: float


def vocal_activity(vocals_wav: Path) -> np.ndarray:
    """Smoothed, normalised loudness of the vocal stem at FRAME_RATE frames per second (0..1)."""
    import soundfile as sf

    y, sr = sf.read(vocals_wav, dtype="float32", always_2d=True)
    y = y.mean(axis=1)
    hop = sr // FRAME_RATE
    n = len(y) // hop
    frames = y[: n * hop].reshape(n, hop)
    db = 20 * np.log10(np.sqrt((frames**2).mean(axis=1) + 1e-10))
    k = 15  # ~150 ms
    db = np.convolve(np.pad(db, k // 2, mode="edge"), np.ones(k) / k, mode="valid")
    lo, hi = np.percentile(db, 20), np.percentile(db, 98)
    return np.clip((db - lo) / max(hi - lo, 1e-6), 0, 1)


def estimate_offset(lyrics: Lyrics, activity: np.ndarray) -> OffsetEstimate:
    """Constant shift that best aligns line starts with vocal onsets in the stem."""
    starts = np.array([ln.time for ln in lyrics.timed])
    if len(starts) < 4:
        return OffsetEstimate(0.0, 0.0, 0.0, 0.0)

    win = int(0.3 * FRAME_RATE)

    def contrast(shift: float) -> float:
        """Mean (loudness after − loudness before) at the line starts: peaks at true onsets."""
        idx = np.round((starts + shift) * FRAME_RATE).astype(int)
        idx = idx[(idx >= win) & (idx < len(activity) - win)]
        if len(idx) < 4:
            return -1.0
        post = np.array([activity[i : i + win].mean() for i in idx])
        pre = np.array([activity[i - win : i].mean() for i in idx])
        return float((post - pre).mean())

    def agreement(shift: float) -> float:
        idx = np.round((starts + shift) * FRAME_RATE).astype(int)
        a, b = int(0.15 * FRAME_RATE), int(0.35 * FRAME_RATE)
        idx = idx[(idx >= a) & (idx < len(activity) - b)]
        if len(idx) < 4:
            return 0.0
        return float(np.mean([activity[i - a : i + b].max() > 0.35 for i in idx]))

    shifts = np.arange(-MAX_OFFSET, MAX_OFFSET + 1e-9, 0.02)
    scores = np.array([contrast(s) for s in shifts])
    suggested = float(shifts[int(scores.argmax())])
    gain = float(scores.max() - contrast(0.0))
    applied = suggested if abs(suggested) >= APPLY_MIN_SHIFT and gain >= APPLY_MIN_GAIN else 0.0
    return OffsetEstimate(
        suggested=round(suggested, 2),
        applied=round(applied, 2),
        agreement_at_zero=round(agreement(0.0), 3),
        agreement_at_applied=round(agreement(applied), 3),
    )


# --- persistence ---------------------------------------------------------------------


def save_lyrics_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False))


def lyrics_to_dict(lyrics: Lyrics) -> dict[str, Any]:
    d = asdict(lyrics)
    return d


def lyrics_from_dict(d: dict[str, Any]) -> Lyrics:
    return Lyrics(
        lines=[Line(**ln) for ln in d["lines"]],
        source=d["source"],
        synced=d["synced"],
        meta=d.get("meta", {}),
        offset=d.get("offset", 0.0),
        confidence=d.get("confidence"),
    )
