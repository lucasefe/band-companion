"""`backline.json` in each song folder: where the song came from and how it was built.

It is committed with the lyrics, so a fresh checkout still knows the origin of every track, and
`backline add` uses it to recognise a song it has already imported (same URL or same file
content) instead of creating a second numbered folder.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

RECORD_NAME = "backline.json"


def find_by_hash(tracks: Path, hash: str) -> Path | None:
    """The song folder whose record was built from a source with this hash, if any."""
    if not tracks.is_dir():
        return None
    for record in sorted(tracks.glob(f"*/*/{RECORD_NAME}")):
        try:
            if json.loads(record.read_text(encoding="utf-8")).get("hash") == hash:
                return record.parent
        except (OSError, ValueError):
            continue
    return None


def read_record(folder: Path) -> dict[str, Any] | None:
    path = folder / RECORD_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_record(folder: Path, record: dict[str, Any]) -> Path:
    path = folder / RECORD_NAME
    record = {**record, "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
