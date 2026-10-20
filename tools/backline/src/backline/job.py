from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path.home() / "Music" / "Backline" / "jobs"

# Job folder layout. `input/` is what we started from, `work/` is intermediate and disposable,
# `output/` is a Band Companion song folder (one .lrc, one .wav rendered by Reaper, the MIDI).
INPUT, WORK, OUTPUT = "input", "work", "output"
MANIFEST = f"{WORK}/manifest.json"
ORIGINAL = f"{INPUT}/original.wav"
SOURCE_JSON = f"{INPUT}/source.json"
DRUMS = f"{WORK}/drums.wav"
VOCALS = f"{WORK}/vocals.wav"
NO_DRUMS = f"{WORK}/no_drums.wav"
ACTIVATIONS = f"{WORK}/activations.npy"
BEATS = f"{WORK}/beats.json"
LYRICS_JSON = f"{WORK}/lyrics.json"
LYRICS_TXT = f"{WORK}/lyrics.txt"
LYRICS_ORIGINAL = f"{WORK}/lyrics-original.lrc"
METRONOME = f"{WORK}/metronome.wav"


def notes_file(profile: str, primary: str) -> str:
    return f"{WORK}/notes.json" if profile == primary else f"{WORK}/notes-{profile}.json"


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


def sanitize_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .-")
    return name[:80] or "untitled"


@dataclass
class Job:
    dir: Path
    manifest: dict[str, Any]

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        title: str,
        artist: str | None,
        source: str,
        hash: str,
    ) -> Job:
        name = f"{artist} - {title}" if artist else title
        dir = cls.find(root, hash) or root / f"{sanitize_name(name)} ({hash})"
        for sub in (INPUT, WORK, OUTPUT):
            (dir / sub).mkdir(parents=True, exist_ok=True)
        manifest_path = dir / MANIFEST
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
        else:
            manifest = {
                "song": {"artist": artist, "title": title},
                "source": source,
                "hash": hash,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "stages": {},
            }
        job = cls(dir, manifest)
        job.save()
        return job

    @classmethod
    def find(cls, root: Path, hash: str) -> Path | None:
        """An existing job for this source, whatever title it was created under."""
        if not root.is_dir():
            return None
        for candidate in sorted(root.glob(f"* ({hash})")):
            if (candidate / MANIFEST).is_file():
                return candidate
        return None

    @classmethod
    def open(cls, dir: Path) -> Job:
        manifest_path = dir / MANIFEST
        if not manifest_path.exists():
            raise FileNotFoundError(f"{dir} is not a backline job folder (no {MANIFEST})")
        return cls(dir, json.loads(manifest_path.read_text()))

    @property
    def title(self) -> str:
        return self.manifest["song"]["title"]

    @property
    def artist(self) -> str | None:
        return self.manifest["song"].get("artist")

    @property
    def file_stem(self) -> str:
        """Base name of the deliverables: <stem>.RPP / .lrc / .wav / .mid."""
        return sanitize_name(self.title)

    @property
    def project_path(self) -> Path:
        return self.dir / f"{self.file_stem}.RPP"

    def path(self, rel: str) -> Path:
        return self.dir / rel

    def output_dir(self, deliver_to: Path | None = None) -> Path:
        return deliver_to.expanduser().resolve() if deliver_to else self.dir / OUTPUT

    def is_done(self, stage: str, params: dict[str, Any], artifacts: list[Path]) -> bool:
        if not all(a.exists() for a in artifacts):
            return False
        record = self.manifest["stages"].get(stage)
        return record is not None and record.get("params", {}) == params

    def mark_done(self, stage: str, params: dict[str, Any], **info: Any) -> None:
        self.manifest["stages"][stage] = {
            "params": params,
            "done_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            **info,
        }
        self.save()

    def stage_info(self, stage: str) -> dict[str, Any]:
        return self.manifest["stages"].get(stage, {})

    def save(self) -> None:
        (self.dir / MANIFEST).parent.mkdir(parents=True, exist_ok=True)
        (self.dir / MANIFEST).write_text(json.dumps(self.manifest, indent=2, ensure_ascii=False))
