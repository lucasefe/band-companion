"""backline.toml: found by walking up from the working directory, else the repo root."""

from __future__ import annotations

import re
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "backline.toml"
REPO_ROOT = Path(__file__).resolve().parents[4]  # tools/backline/src/backline/config.py → repo


@dataclass
class Config:
    path: Path | None  # the file that was read, if any
    jobs: Path
    tracks: Path | None  # the app's song folders; None when there is no repo around
    default_set: str | None
    count_in_bars: int
    count_in_note: int
    metronome: str  # always | gaps | off
    metronome_min_bars: int
    metronome_silence_db: float


def find_config(start: Path | None = None) -> Path | None:
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        candidate = folder / CONFIG_NAME
        if candidate.is_file():
            return candidate
    fallback = REPO_ROOT / CONFIG_NAME
    return fallback if fallback.is_file() else None


def load_config(path: Path | None = None) -> Config:
    path = path or find_config()
    data = tomllib.loads(path.read_text()) if path else {}
    base = path.parent if path else Path.cwd()
    paths = data.get("paths", {})
    tracks_raw = paths.get("tracks")
    count_in = data.get("count_in", {})
    metronome = data.get("metronome", {})
    return Config(
        path=path,
        jobs=_resolve(paths.get("jobs", "~/Music/Backline/jobs"), base),
        tracks=_resolve(tracks_raw, base) if tracks_raw else None,
        default_set=data.get("tracks", {}).get("default_set"),
        count_in_bars=int(count_in.get("bars", 2)),
        count_in_note=int(count_in.get("note", 37)),
        metronome=str(metronome.get("mode", "always")),
        metronome_min_bars=int(metronome.get("min_gap_bars", 1)),
        metronome_silence_db=float(metronome.get("silence_db", -30)),
    )


def _resolve(raw: str, base: Path) -> Path:
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


# --- song folders: tracks/<set>/<NN - Title>/ ---------------------------------------

_NUMBERED = re.compile(r"^\s*(\d{1,3})\s*[-._)]\s*(.*?)\s*$")


def song_folder(tracks: Path, set_name: str, title: str, number: int | None = None) -> Path:
    """The folder for a song in a set.

    Reuses an existing folder with the same title (any number) so re-running does not create
    a sibling; otherwise takes `number` or the next free one.
    """
    set_dir = tracks / set_name
    existing: dict[int, Path] = {}
    if set_dir.is_dir():
        for child in set_dir.iterdir():
            if not child.is_dir():
                continue
            m = _NUMBERED.match(child.name)
            if m and _key(m.group(2)) == _key(title) and number is None:
                return child
            if m:
                existing[int(m.group(1))] = child
    if number is None:
        number = max(existing, default=0) + 1
    if (
        number in existing
        and existing[number].name.casefold() != f"{number:02d} - {title}".casefold()
    ):
        raise FileExistsError(
            f"{existing[number].name!r} already uses number {number} in {set_name!r}; "
            "pick another --number"
        )
    return set_dir / f"{number:02d} - {title}"


def _key(s: str) -> str:
    """Accent- and case-insensitive comparison key ("Fotografía" == "fotografia")."""
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
