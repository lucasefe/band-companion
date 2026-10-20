from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from drums2midi.devices import resolve_device

BEAT_THIS_MODELS = {"final": "final0", "small": "small0"}


class TempoError(RuntimeError):
    pass


@dataclass
class Beats:
    beats: list[float]
    downbeats: list[float]
    bpm: float
    meter: int
    source: str

    @property
    def first_downbeat(self) -> float:
        return self.downbeats[0] if self.downbeats else self.beats[0]

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=1))

    @classmethod
    def load(cls, path: Path) -> Beats:
        return cls(**json.loads(path.read_text()))


def detect_beats(original_wav: Path, *, device: str = "auto", model: str = "final") -> Beats:
    """Beat This! beat/downbeat tracking on the full mix."""
    from beat_this.inference import File2Beats

    checkpoint = BEAT_THIS_MODELS[model]
    dev = resolve_device(device)
    try:
        beats, downbeats = File2Beats(checkpoint_path=checkpoint, device=dev, dbn=False)(
            str(original_wav)
        )
    except Exception as e:  # noqa: BLE001
        if dev == "cpu":
            raise
        print(f"beat_this on {dev} failed ({type(e).__name__}: {e}); retrying on cpu", flush=True)
        beats, downbeats = File2Beats(checkpoint_path=checkpoint, device="cpu", dbn=False)(
            str(original_wav)
        )

    beats = np.asarray(beats, dtype=float)
    downbeats = np.asarray(downbeats, dtype=float)
    if len(beats) < 4:
        raise TempoError("could not find a beat; pass --bpm to set the tempo manually")

    bpm = float(60.0 / np.median(np.diff(beats)))
    return Beats(
        beats=[round(float(b), 4) for b in beats],
        downbeats=[round(float(d), 4) for d in downbeats],
        bpm=round(bpm, 2),
        meter=_estimate_meter(beats, downbeats),
        source=f"beat_this/{checkpoint}",
    )


def _estimate_meter(beats: np.ndarray, downbeats: np.ndarray) -> int:
    if len(downbeats) < 2:
        return 4
    idx = np.searchsorted(beats, downbeats)
    counts = np.diff(idx)
    counts = counts[(counts >= 2) & (counts <= 12)]
    if len(counts) == 0:
        return 4
    return int(np.bincount(counts).argmax())
