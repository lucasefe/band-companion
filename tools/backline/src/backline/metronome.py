"""The metronome track: one plain click on every quarter note of the song's real beat grid, on
the output timeline. Rendered from Reaper hard left, with the MIDI drums hard right, so the two
can be routed separately.

Mode "gaps" restricts the clicks to the stretches where the drums drop out (found from the drum
stem); "always" is the default.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from backline.midi_out import CountIn, Layout
from backline.reference import click
from backline.tempo import Beats, beat_levels_db

MODES = ("gaps", "always", "off")

# One sound for every beat: the plain (non-accent) count-in click, so it matches the reference.
CLICK_LEVEL = 0.5


@dataclass
class Gap:
    """A stretch of beats without drums, in beat indices of `Beats.beats` (end exclusive)."""

    start: int
    end: int

    def contains(self, beat: int) -> bool:
        return self.start <= beat < self.end


def detect_gaps(
    drums_wav: Path,
    beats: Beats,
    *,
    min_bars: int = 1,
    silence_db: float = -30.0,
) -> list[Gap]:
    """Beats where the drum stem is `silence_db` below its typical level, for at least `min_bars`.

    Gaps are snapped outwards to bar boundaries so the click always starts on a 1.
    """
    if len(beats.beats) < 2:
        return []
    db = beat_levels_db(drums_wav, beats)
    typical = float(np.percentile(db, 75))  # what a beat with drums sounds like in this song
    quiet = db < typical + silence_db

    bar_starts = _bar_starts(beats)
    min_beats = max(1, min_bars * beats.meter)
    gaps: list[Gap] = []
    i = 0
    while i < len(quiet):
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < len(quiet) and quiet[j]:
            j += 1
        if j - i >= min_beats:
            start = _snap_down(bar_starts, i)
            end = _snap_up(bar_starts, j, len(quiet))
            if gaps and gaps[-1].end >= start:
                gaps[-1] = Gap(gaps[-1].start, end)
            else:
                gaps.append(Gap(start, end))
        i = j
    return gaps


def write_metronome(
    dest_wav: Path,
    layout: Layout,
    beats: Beats,
    gaps: list[Gap],
    *,
    mode: str = "gaps",
    count_in: CountIn | None = None,
    sr: int = 44100,
    tail_seconds: float = 0.0,
) -> float:
    """Mono 16-bit click track on the output timeline. Returns its length in seconds."""
    if mode not in MODES:
        raise ValueError(f"metronome mode must be one of {MODES}, got {mode!r}")
    times = np.asarray(beats.beats, dtype=float) + layout.shift
    end = (times[-1] if len(times) else layout.shift) + 60.0 / beats.bpm + tail_seconds
    out = np.zeros(int(np.ceil(end * sr)) + sr, dtype=np.float32)

    def put(
        seconds: float, *, accent: bool, level: float | None, pitch: float | None = None
    ) -> None:
        burst = click(sr, accent=accent, pitch=pitch, level=level)
        start = int(round(seconds * sr))
        stop = min(start + len(burst), len(out))
        if 0 <= start < len(out):
            out[start:stop] += burst[: stop - start]

    if count_in:
        spb = 60.0 / layout.bpm
        for k in range(count_in.bars * layout.meter):
            # The same clicks as the reference track, so the two count-ins sound alike.
            put(layout.count_in_start + k * spb, accent=k % layout.meter == 0, level=None)

    if mode != "off":
        for i, t in enumerate(times):
            if count_in and t < layout.first_downbeat - 1e-3:
                continue  # pickup beats: the count-in already clicks there
            if mode == "gaps" and not any(g.contains(i) for g in gaps):
                continue
            put(t, accent=False, level=CLICK_LEVEL)

    np.clip(out, -1.0, 1.0, out=out)
    sf.write(dest_wav, out, sr, subtype="PCM_16")
    return len(out) / sr


def gaps_in_seconds(gaps: list[Gap], beats: Beats, shift: float) -> list[dict[str, float | int]]:
    """For backline.json: where the metronome fills in, on the output timeline."""
    times = np.asarray(beats.beats, dtype=float)
    spb = 60.0 / beats.bpm
    bar_starts = _bar_starts(beats)
    result = []
    for g in gaps:
        start = float(times[g.start]) + shift
        stop = float(times[g.end]) + shift if g.end < len(times) else float(times[-1]) + spb + shift
        first_bar = int(np.searchsorted(bar_starts, g.start, side="right"))
        result.append(
            {
                "start": round(start, 3),
                "end": round(stop, 3),
                "bars": round((g.end - g.start) / beats.meter, 2),
                "bar": first_bar,  # 1-based bar number within the song
            }
        )
    return result


# --- helpers ---------------------------------------------------------------------------


def _bar_starts(beats: Beats) -> np.ndarray:
    """Indices into `beats.beats` that are downbeats; falls back to every `meter` beats."""
    times = np.asarray(beats.beats, dtype=float)
    if beats.downbeats:
        idx = np.searchsorted(times, np.asarray(beats.downbeats, dtype=float))
        idx = np.unique(np.clip(idx, 0, len(times) - 1))
        if len(idx) >= 2:
            return idx
    first = 0
    return np.arange(first, len(times), max(1, beats.meter))


def _snap_down(bar_starts: np.ndarray, i: int) -> int:
    k = int(np.searchsorted(bar_starts, i, side="right")) - 1
    return int(bar_starts[k]) if k >= 0 else 0


def _snap_up(bar_starts: np.ndarray, j: int, n: int) -> int:
    k = int(np.searchsorted(bar_starts, j, side="left"))
    return int(bar_starts[k]) if k < len(bar_starts) else n
