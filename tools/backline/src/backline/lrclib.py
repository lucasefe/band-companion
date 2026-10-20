"""Client for lrclib.net, an open database of plain and time-synced lyrics."""

from __future__ import annotations

import difflib
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

import requests

from backline import __version__

API = "https://lrclib.net/api"
USER_AGENT = f"backline/{__version__} (https://github.com/lucasefe/band-companion)"
TIMEOUT = 15
RETRIES = 5


class LrclibError(RuntimeError):
    pass


@dataclass
class Candidate:
    id: int
    artist: str
    title: str
    album: str
    duration: float
    synced: str | None
    plain: str | None
    instrumental: bool
    score: float = 0.0

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Candidate:
        return cls(
            id=d["id"],
            artist=d.get("artistName") or "",
            title=d.get("trackName") or d.get("name") or "",
            album=d.get("albumName") or "",
            duration=float(d.get("duration") or 0),
            synced=d.get("syncedLyrics") or None,
            plain=d.get("plainLyrics") or None,
            instrumental=bool(d.get("instrumental")),
        )

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "artist": self.artist,
            "title": self.title,
            "album": self.album,
            "duration": self.duration,
            "synced": self.synced is not None,
            "score": round(self.score, 3),
        }


def search(
    *, artist: str | None = None, title: str | None = None, query: str | None = None
) -> list[Candidate]:
    params: dict[str, str] = {}
    if query:
        params["q"] = query
    else:
        if title:
            params["track_name"] = title
        if artist:
            params["artist_name"] = artist
    if not params:
        return []
    data = _get("/search", params)
    if not isinstance(data, list):
        return []
    return [Candidate.from_api(d) for d in data]


def _get(path: str, params: dict[str, str]) -> Any:
    delay = 1.0
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(
                API + path, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
            )
            if r.status_code == 404:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                raise LrclibError(f"HTTP {r.status_code}: {r.text[:120]}")
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, LrclibError, ValueError) as e:
            last = e
            if attempt < RETRIES - 1:
                time.sleep(delay)
                delay = min(delay * 2, 8.0)
    raise LrclibError(f"lrclib.net unavailable after {RETRIES} attempts: {last}")


# --- ranking ---------------------------------------------------------------

_NOISE = re.compile(
    r"\s*[\(\[][^\)\]]*(feat|ft\.|official|video|audio|lyric|remaster|live|version|edit|hd|4k)"
    r"[^\)\]]*[\)\]]",
    re.I,
)
_FEAT = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+.*$", re.I)
_PUNCT = re.compile(r"[^\w\s]")


def normalize(s: str) -> str:
    """Accent/case/punctuation-insensitive form of a title or artist."""
    s = _NOISE.sub("", s)
    s = _FEAT.sub("", s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _PUNCT.sub(" ", s.lower())
    return " ".join(s.split())


def similarity(a: str, b: str) -> float:
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    # Containment handles "fotografia" vs "fotografia (feat. nelly furtado)" leftovers.
    if a in b or b in a:
        ratio = max(ratio, 0.9)
    return ratio


def rank(
    candidates: list[Candidate],
    *,
    title: str,
    artist: str | None,
    duration: float | None,
) -> list[Candidate]:
    """Score candidates by title/artist similarity, duration closeness and synced availability."""
    for c in candidates:
        title_sim = similarity(c.title, title)
        artist_sim = similarity(c.artist, artist) if artist else 0.5
        if duration and c.duration:
            delta = abs(c.duration - duration)
            dur = max(0.0, 1.0 - delta / 10.0)  # 1.0 at 0 s, 0.7 at 3 s, 0 at ≥10 s
        else:
            dur = 0.5
        score = 0.45 * title_sim + 0.25 * artist_sim + 0.20 * dur
        if c.synced:
            score += 0.10
        if c.instrumental or not (c.synced or c.plain):
            score -= 0.5
        if title_sim < 0.6:
            score -= 0.5
        c.score = score
    return sorted(candidates, key=lambda c: c.score, reverse=True)


ACCEPT_SCORE = 0.6
