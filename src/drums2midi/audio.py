from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 44100


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Return float32 samples shaped [frames, channels] and the sample rate."""
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    return data, sr


def write_wav(path: Path, data: np.ndarray, sr: int) -> None:
    """Write float32 samples shaped [frames, channels]."""
    sf.write(path, data, sr, subtype="FLOAT")


def duration_seconds(path: Path) -> float:
    info = sf.info(path)
    return info.frames / info.samplerate
