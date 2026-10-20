from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from backline.devices import resolve_device

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


# --- a usable quarter-note grid --------------------------------------------------------

DROP_BELOW = 0.7  # an interval this short is an extra (double-time) beat
FILL_ABOVE = 1.7  # an interval this long hides skipped beats; 1.3–1.7 is a real slow-down
PLAUSIBLE_BPM = (60.0, 170.0)  # a quarter note outside this is the wrong tempo octave


def _quarter_note(times: np.ndarray, downs: np.ndarray, meter: int) -> float:
    """The song's quarter-note length. The median interval is a good start, but when the tracker
    ran in double time for most of the song it is an eighth note, and when it halved, a half
    note; the downbeat spacing can tell, except that in those songs the downbeats are usually
    wrong by the same factor. So every hint is only taken when it lands on a plausible tempo."""
    q = float(np.median(np.diff(times)))
    lo, hi = PLAUSIBLE_BPM
    while 60.0 / q > hi:
        q *= 2
    while 60.0 / q < lo:
        q /= 2
    if len(downs) >= 2:
        beats_per_bar = float(np.median(np.diff(downs))) / q
        if beats_per_bar > 1.5 * meter and 60.0 / (2 * q) >= lo:
            q *= 2
        elif beats_per_bar < 0.75 * meter and 60.0 / (q / 2) <= hi:
            q /= 2
    return q


@dataclass
class GridStats:
    bpm: float
    dropped: int
    inserted: int


def regularize(beats: Beats) -> tuple[Beats, GridStats]:
    """Beat trackers flip to double time or skip beats in loose passages. For a metronome every
    quarter note matters, so: pick the quarter-note length (the median interval, corrected by
    the bar length the downbeats imply), drop the extra beats of double-time runs, keeping the
    ones in phase with what came before, and fill runs of clearly missing beats evenly.
    Genuine slow-downs (intervals up to 1.7×) are left as they are."""
    times = np.asarray(beats.beats, dtype=float)
    if len(times) < 4:
        return beats, GridStats(beats.bpm, 0, 0)
    downs = np.asarray(beats.downbeats, dtype=float)
    quarter = _quarter_note(times, downs, beats.meter)

    kept: list[float] = [float(times[0])]
    dropped = inserted = 0
    for t in times[1:]:
        gap = t - kept[-1]
        if gap < DROP_BELOW * quarter:
            dropped += 1
            continue
        ratio = gap / quarter
        if ratio >= FILL_ABOVE:
            n = int(round(ratio))
            for k in range(1, n):
                kept.append(kept[-1] + (t - kept[-1]) / (n - k + 1))
                inserted += 1
        kept.append(float(t))

    grid = np.asarray(kept)
    snapped = []
    for d in downs:
        i = int(np.argmin(np.abs(grid - d)))
        if abs(grid[i] - d) <= 0.25 * quarter:
            snapped.append(float(grid[i]))
    return (
        Beats(
            beats=[round(x, 4) for x in kept],
            downbeats=sorted(set(round(x, 4) for x in snapped)),
            bpm=round(60.0 / quarter, 2),
            meter=beats.meter,
            source=beats.source + "+regularized",
        ),
        GridStats(bpm=round(60.0 / quarter, 2), dropped=dropped, inserted=inserted),
    )
