"""The reference track: the original song on the same timeline as the drums render and the
lyrics — count-in clicks first (synthesized, no instrument needed), then the song."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from backline.midi_out import CountIn, Layout

CLICK_SECONDS = 0.05
CLICK_HZ = 1000.0
ACCENT_HZ = 1500.0
CLICK_LEVEL = 0.5
ACCENT_LEVEL = 0.7


def click(
    sr: int, *, accent: bool = False, pitch: float | None = None, level: float | None = None
) -> np.ndarray:
    """A short decaying tone; by default brighter and louder on the downbeat."""
    n = int(CLICK_SECONDS * sr)
    t = np.arange(n) / sr
    hz = pitch if pitch is not None else (ACCENT_HZ if accent else CLICK_HZ)
    amp = level if level is not None else (ACCENT_LEVEL if accent else CLICK_LEVEL)
    return (np.sin(2 * np.pi * hz * t) * np.exp(-t * 90) * amp).astype(np.float32)


def write_reference(
    original_wav: Path,
    dest_wav: Path,
    layout: Layout,
    count_in: CountIn | None,
) -> float:
    """Write `dest_wav` (16-bit, original's rate/channels) and return its length in seconds."""
    audio, sr = sf.read(original_wav, dtype="float32", always_2d=True)
    frames, channels = audio.shape
    shift = int(round(layout.shift * sr))
    out = np.zeros((shift + frames, channels), dtype=np.float32)
    out[shift:] = audio

    if count_in:
        spb = 60.0 / layout.bpm
        for k in range(count_in.bars * layout.meter):
            start = int(round((layout.count_in_start + k * spb) * sr))
            burst = click(sr, accent=k % layout.meter == 0)
            end = min(start + len(burst), len(out))
            if start < 0 or start >= len(out):
                continue
            out[start:end] += burst[: end - start, None]

    np.clip(out, -1.0, 1.0, out=out)
    sf.write(dest_wav, out, sr, subtype="PCM_16")
    return len(out) / sr
