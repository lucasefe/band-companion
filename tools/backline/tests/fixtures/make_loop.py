"""Synthesize a drum loop with known hits: kicks on 1 & 3, snares on 2 & 4, 8th-note hats."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100


def make_loop(path: Path, bpm: float = 100.0, beats: int = 16) -> Path:
    beat = 60.0 / bpm
    duration = beat * beats
    t = np.arange(int(SR * duration)) / SR
    y = np.zeros_like(t)
    rng = np.random.default_rng(0)

    def add(start: float, sig: np.ndarray) -> None:
        i = int(start * SR)
        n = min(len(sig), len(y) - i)
        y[i : i + n] += sig[:n]

    tt = np.arange(int(0.3 * SR)) / SR
    kick = np.sin(2 * np.pi * (50 + 100 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 12)
    snare = rng.normal(0, 1, len(tt)) * np.exp(-tt * 20) * 0.5
    snare += np.sin(2 * np.pi * 180 * tt) * np.exp(-tt * 25) * 0.4
    hat = rng.normal(0, 1, int(0.05 * SR))
    hat = np.diff(hat, prepend=0) * np.exp(-np.arange(len(hat)) / SR * 80) * 0.15

    for b in range(beats):
        s = b * beat
        if b % 4 in (0, 2):
            add(s, kick)
        if b % 4 in (1, 3):
            add(s, snare)
        add(s, hat)
        add(s + beat / 2, hat)

    bass = 0.2 * np.sin(2 * np.pi * 55 * t) * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * t / beat)))
    sf.write(path, np.stack([y + bass] * 2, 1) * 0.6, SR)
    return path


if __name__ == "__main__":
    out = Path(__file__).with_name("loop.wav")
    print(make_loop(out))
