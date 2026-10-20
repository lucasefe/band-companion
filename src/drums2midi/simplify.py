from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from drums2midi.tempo import Beats
from drums2midi.transcribe import CLASSES, DEFAULT_THRESHOLDS, FPS

STYLES = ("raw", "detailed", "essence")
GRIDS = (8, 16, 12, 24)

# ADTOF merges all toms and all cymbals and has no open hi-hat class.
GM_NOTES = {"kick": 36, "snare": 38, "tom": 45, "hihat": 42, "cymbal": 49}

# Peak-picking thresholds per class (kick, snare, tom, hihat, cymbal).
# "conservative" is ADTOF's tuning (few false positives); "daring" trades precision for recall,
# most aggressively on toms and cymbals, where the conservative setting misses the most.
PROFILES: dict[str, tuple[float, ...]] = {
    "conservative": DEFAULT_THRESHOLDS,
    "daring": (0.12, 0.10, 0.08, 0.12, 0.08),
}


@dataclass
class Hit:
    """A raw ADTOF peak."""

    time: float
    cls: str
    confidence: float


@dataclass
class Note:
    """A drum note in song time (seconds from the start of original.wav)."""

    time: float
    cls: str
    note: int
    velocity: int
    confidence: float


def pick_hits(
    activations: np.ndarray, thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS
) -> list[Hit]:
    from adtof_pytorch import LABELS_5, PeakPicker

    picker = PeakPicker(thresholds=list(thresholds), fps=FPS)
    peaks = picker.pick(activations[None])[0]  # {adtof_label: [times]}
    hits: list[Hit] = []
    for i, label in enumerate(LABELS_5):
        for t in peaks.get(label, []):
            frame = int(round(t * FPS))
            lo, hi = max(frame - 1, 0), min(frame + 2, len(activations))
            hits.append(Hit(float(t), CLASSES[i], float(activations[lo:hi, i].max())))
    hits.sort(key=lambda h: h.time)
    return hits


def simplify(
    activations: np.ndarray,
    beats: Beats,
    *,
    style: str = "raw",
    grid: int = 16,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> list[Note]:
    if style == "raw":
        return _raw(pick_hits(activations, thresholds), thresholds)
    raise NotImplementedError(f"style {style!r} is not implemented yet")


def _raw(hits: list[Hit], thresholds: tuple[float, ...]) -> list[Note]:
    notes = []
    for h in hits:
        threshold = thresholds[CLASSES.index(h.cls)]
        velocity = _velocity_from_confidence(h.confidence, threshold)
        notes.append(
            Note(round(h.time, 4), h.cls, GM_NOTES[h.cls], velocity, round(h.confidence, 3))
        )
    return notes


def _velocity_from_confidence(
    confidence: float, threshold: float, lo: int = 48, hi: int = 127
) -> int:
    x = (confidence - threshold) / max(1.0 - threshold, 1e-6)
    return int(np.clip(round(lo + x * (hi - lo)), 1, 127))


def count_by_class(notes: list[Note]) -> dict[str, int]:
    counts = Counter(n.cls for n in notes)
    return {cls: counts.get(cls, 0) for cls in CLASSES}


def save_notes(path: Path, notes: list[Note], meta: dict[str, Any]) -> None:
    payload = {"meta": meta, "counts": count_by_class(notes), "notes": [asdict(n) for n in notes]}
    path.write_text(json.dumps(payload, indent=1))


def load_notes(path: Path) -> list[Note]:
    return [Note(**n) for n in json.loads(path.read_text())["notes"]]
