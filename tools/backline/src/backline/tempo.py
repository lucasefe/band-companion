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


# --- the grid a song is actually played to ------------------------------------------------

GRID_MODES = ("auto", "straight", "tracked")
# The tracker places beats on a 10 ms frame grid, so ±10 ms of residual is its own noise; a
# straight fit is accepted when 95 % of confident beats are within 35 ms and none beyond 60 ms.
STRAIGHT_P95_MS = 35.0
STRAIGHT_MAX_MS = 60.0
SMOOTH_WINDOW = 17  # beats; a local line through them removes the frame jitter, keeps the drift
MIN_CONFIDENT = 16
ACTIVE_BELOW_PEAK_DB = 20.0  # a beat with drums: within this of the song's loud drum beats


@dataclass
class GridInfo:
    mode: str  # straight | tracked | manual
    bpm: float
    confident_beats: int
    residual_p95_ms: float | None = None
    residual_max_ms: float | None = None
    dropped: int = 0
    inserted: int = 0
    reason: str | None = None  # why a straight grid was not used


def beat_levels_db(audio_wav: Path, beats: Beats) -> np.ndarray:
    """RMS level per beat of `audio_wav` (the drum stem), in dB."""
    import soundfile as sf

    audio, sr = sf.read(audio_wav, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    times = np.asarray(beats.beats, dtype=float)
    if len(times) < 2:
        return np.zeros(len(times))
    edges = np.append(times, times[-1] + float(np.median(np.diff(times))))
    idx = np.clip(np.round(edges * sr).astype(int), 0, len(mono))
    rms = np.array(
        [
            np.sqrt(np.mean(mono[a:b] ** 2)) if b > a else 0.0
            for a, b in zip(idx[:-1], idx[1:], strict=True)
        ]
    )
    return 20 * np.log10(rms + 1e-9)


def confident_mask(beats: Beats, levels_db: np.ndarray) -> np.ndarray:
    """Beats the tracker can be trusted on: drums are playing and the neighbouring intervals are
    regular. (Strummed intros and breaks are where trackers follow the wrong thing.)"""
    times = np.asarray(beats.beats, dtype=float)
    if len(times) < 3:
        return np.zeros(len(times), dtype=bool)
    active = levels_db > np.percentile(levels_db, 75) - ACTIVE_BELOW_PEAK_DB
    d = np.diff(times)
    ok = np.abs(d / np.median(d) - 1) < 0.12
    regular = np.r_[ok, True] & np.r_[True, ok]
    return active & regular


def choose_grid(
    beats: Beats,
    drums_wav: Path,
    duration: float,
    *,
    mode: str = "auto",
    bpm: float | None = None,
) -> tuple[Beats, GridInfo]:
    """The beat grid everything else uses.

    Studio recordings are played to a click: one tempo fits every drummed beat within a few
    milliseconds, and that line, extrapolated, is also right through the intro and the breaks
    where the tracker loses its way. When the fit is not tight (a drifting or tempo-changing
    recording) the cleaned tracked grid is used instead.
    """
    if mode not in GRID_MODES:
        raise ValueError(f"grid mode must be one of {GRID_MODES}, got {mode!r}")
    tracked, stats = regularize(beats)
    levels = beat_levels_db(drums_wav, tracked)
    conf = confident_mask(tracked, levels)
    times = np.asarray(tracked.beats, dtype=float)
    k = np.arange(len(times))

    if bpm:
        period = 60.0 / bpm
        anchor = times[conf] - k[conf] * period if conf.any() else times[:1]
        t0 = float(np.median(anchor))
        grid = _straight(t0, period, duration, tracked, conf)
        return grid, GridInfo("manual", round(bpm, 2), int(conf.sum()))

    if mode == "tracked":
        return smooth(tracked), GridInfo(
            "tracked",
            tracked.bpm,
            int(conf.sum()),
            None,
            None,
            stats.dropped,
            stats.inserted,
            "requested",
        )

    if conf.sum() < MIN_CONFIDENT:
        reason = f"only {int(conf.sum())} confident beats"
        if mode == "straight":
            period = 60.0 / tracked.bpm
            t0 = float(np.median(times - k * period))
            return _straight(t0, period, duration, tracked, conf), GridInfo(
                "straight", tracked.bpm, int(conf.sum()), reason=reason
            )
        return smooth(tracked), GridInfo(
            "tracked",
            tracked.bpm,
            int(conf.sum()),
            None,
            None,
            stats.dropped,
            stats.inserted,
            reason,
        )

    a = np.c_[np.ones(int(conf.sum())), k[conf]]
    t0, period = np.linalg.lstsq(a, times[conf], rcond=None)[0]
    residual = np.abs(times[conf] - (t0 + period * k[conf])) * 1000
    p95, worst = float(np.percentile(residual, 95)), float(residual.max())
    fitted_bpm = round(60.0 / period, 2)
    tight = p95 <= STRAIGHT_P95_MS and worst <= STRAIGHT_MAX_MS
    if tight or mode == "straight":
        grid = _straight(float(t0), float(period), duration, tracked, conf)
        return grid, GridInfo(
            "straight",
            fitted_bpm,
            int(conf.sum()),
            round(p95, 1),
            round(worst, 1),
            reason=None if tight else "forced",
        )
    return smooth(tracked), GridInfo(
        "tracked",
        tracked.bpm,
        int(conf.sum()),
        round(p95, 1),
        round(worst, 1),
        stats.dropped,
        stats.inserted,
        f"straight fit too loose (p95 {p95:.0f} ms, max {worst:.0f} ms)",
    )


def _straight(t0: float, period: float, duration: float, tracked: Beats, conf: np.ndarray) -> Beats:
    """A regular grid at `period` through `t0`, covering the song, with bar lines phased to the
    tracker's downbeats where it was confident."""
    meter = max(1, tracked.meter)
    k_min = int(np.ceil((0.0 - t0) / period))
    k_max = int(np.floor((duration - t0) / period))
    ks = np.arange(k_min, k_max + 1)
    times = t0 + ks * period

    votes: dict[int, int] = {}
    tracked_times = np.asarray(tracked.beats, dtype=float)
    confident_downbeats = [
        d for d in tracked.downbeats if conf[int(np.argmin(np.abs(tracked_times - d)))]
    ] or list(tracked.downbeats)
    for d in confident_downbeats:
        kd = int(round((d - t0) / period))
        if abs(t0 + kd * period - d) <= 0.25 * period:
            votes[kd % meter] = votes.get(kd % meter, 0) + 1
    phase = max(votes, key=votes.get) if votes else (k_min % meter)
    downbeats = [float(t) for kk, t in zip(ks, times, strict=True) if kk % meter == phase]
    return Beats(
        beats=[round(float(t), 4) for t in times],
        downbeats=[round(t, 4) for t in downbeats],
        bpm=round(60.0 / period, 2),
        meter=meter,
        source=tracked.source.replace("+regularized", "") + "+straight",
    )


def smooth(beats: Beats, window: int = SMOOTH_WINDOW) -> Beats:
    """Each beat replaced by a local quadratic fit through its neighbours: the tempo drift of a
    human recording survives (a quadratic follows accelerandos without bias), the tracker's
    10 ms frame jitter does not."""
    times = np.asarray(beats.beats, dtype=float)
    n = len(times)
    if n < window:
        return beats
    half = window // 2
    out = np.empty(n)
    k = np.arange(n, dtype=float)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out[i] = float(np.polyval(np.polyfit(k[lo:hi], times[lo:hi], 2), i))
    downbeats = []
    for d in beats.downbeats:
        i = int(np.argmin(np.abs(times - d)))
        downbeats.append(round(float(out[i]), 4))
    return Beats(
        beats=[round(float(t), 4) for t in out],
        downbeats=sorted(set(downbeats)),
        bpm=beats.bpm,
        meter=beats.meter,
        source=beats.source + "+smoothed",
    )
