from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path.home() / "Music" / "Drums2MIDI"
MANIFEST = "manifest.json"

# Artifacts each stage produces; a stage is cached when all of them exist
# and the manifest says it ran with the same parameters.
ARTIFACTS: dict[str, tuple[str, ...]] = {
    "ingest": ("original.wav",),
    "separate": ("drums.wav", "vocals.wav"),
    "transcribe": ("activations.npy",),
    "tempo": ("beats.json",),
    "lyrics": ("lyrics.json",),  # .lrc/.txt only exist when lyrics were found
    "simplify": ("notes.json", "notes-daring.json"),
    "output": ("drums.mid", "drums-daring.mid", "project.RPP"),
}


def source_hash(src: str | Path) -> str:
    """Short content hash for local files, URL hash for links."""
    h = hashlib.sha1()
    p = Path(src)
    if p.is_file():
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    else:
        h.update(str(src).encode())
    return h.hexdigest()[:8]


def sanitize_title(title: str) -> str:
    title = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", title)
    title = re.sub(r"\s+", " ", title).strip(" .-")
    return title[:80] or "untitled"


@dataclass
class Job:
    dir: Path
    manifest: dict[str, Any]

    @classmethod
    def create(cls, root: Path, title: str, source: str, hash: str) -> Job:
        dir = root / f"{sanitize_title(title)} ({hash})"
        dir.mkdir(parents=True, exist_ok=True)
        manifest_path = dir / MANIFEST
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
        else:
            manifest = {
                "title": title,
                "source": source,
                "hash": hash,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "stages": {},
            }
        job = cls(dir, manifest)
        job.save()
        return job

    @classmethod
    def open(cls, dir: Path) -> Job:
        manifest_path = dir / MANIFEST
        if not manifest_path.exists():
            raise FileNotFoundError(f"{dir} is not a drums2midi job folder (no {MANIFEST})")
        return cls(dir, json.loads(manifest_path.read_text()))

    @property
    def title(self) -> str:
        return self.manifest["title"]

    def path(self, name: str) -> Path:
        return self.dir / name

    def is_done(self, stage: str, params: dict[str, Any] | None = None) -> bool:
        if not all(self.path(a).exists() for a in ARTIFACTS[stage]):
            return False
        record = self.manifest["stages"].get(stage)
        return record is not None and record.get("params", {}) == (params or {})

    def mark_done(self, stage: str, params: dict[str, Any] | None = None, **info: Any) -> None:
        self.manifest["stages"][stage] = {
            "params": params or {},
            "done_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            **info,
        }
        self.save()

    def stage_info(self, stage: str) -> dict[str, Any]:
        return self.manifest["stages"].get(stage, {})

    def save(self) -> None:
        (self.dir / MANIFEST).write_text(json.dumps(self.manifest, indent=2, ensure_ascii=False))
