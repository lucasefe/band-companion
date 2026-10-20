from pathlib import Path

import numpy as np
import pytest

from backline.tempo import Beats, detect_beats


@pytest.mark.slow
def test_beat_this_on_loop(loop_wav: Path, tmp_path: Path):
    beats = detect_beats(loop_wav, device="cpu")
    assert abs(beats.bpm - 100) <= 1
    assert beats.meter == 4
    assert abs(beats.first_downbeat) < 0.1
    assert 15 <= len(beats.beats) <= 18

    beats.save(tmp_path / "beats.json")
    assert Beats.load(tmp_path / "beats.json") == beats


def test_first_downbeat_falls_back_to_first_beat():
    b = Beats(beats=[0.5, 1.0, 1.5, 2.0], downbeats=[], bpm=120, meter=4, source="x")
    assert b.first_downbeat == 0.5


def _grid(times, downbeats, bpm=120.0, meter=4):
    return Beats(
        beats=[round(t, 4) for t in times], downbeats=downbeats, bpm=bpm, meter=meter, source="x"
    )


def test_regularize_drops_double_time_runs_in_phase():
    from backline.tempo import regularize

    q = 0.5
    steady = [i * q for i in range(8)]  # 0 … 3.5
    doubled = [4.0 + k * q / 2 for k in range(16)]  # 4.0 … 7.75 at eighth notes
    after = [8.0 + i * q for i in range(8)]
    beats = _grid(steady + doubled + after, downbeats=[0.0, 2.0, 4.0, 6.0, 8.0, 10.0])
    grid, stats = regularize(beats)
    assert stats.dropped == 8 and stats.inserted == 0
    assert grid.beats == pytest.approx([i * q for i in range(24)]), "quarter notes, in phase"
    assert grid.downbeats == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    assert grid.bpm == 120


def test_regularize_fills_skipped_beats_but_keeps_slow_downs():
    from backline.tempo import regularize

    times = [0.0, 0.5, 1.0, 1.5, 2.5, 3.0, 3.5, 4.0, 4.75, 5.25, 5.75]  # a 2q gap, then a 1.5q beat
    grid, stats = regularize(_grid(times, downbeats=[0.0, 2.0]))
    assert stats.inserted == 1 and stats.dropped == 0
    assert 2.0 in grid.beats, "the skipped beat is filled at the midpoint"
    assert 4.75 in grid.beats and 4.375 not in grid.beats, "a 1.5x interval is left alone"


def test_regularize_uses_downbeats_to_pick_the_quarter_note():
    from backline.tempo import regularize

    # The tracker ran in double time for the whole song: median interval is an eighth note,
    # but downbeats are 8 "beats" apart, which says a bar is 4 quarters of twice that length.
    eighth = 0.25
    times = [i * eighth for i in range(64)]
    downbeats = [i * 2.0 for i in range(8)]
    grid, stats = regularize(_grid(times, downbeats=downbeats, bpm=240))
    assert grid.bpm == 120
    assert grid.beats == pytest.approx([i * 0.5 for i in range(32)])
    assert stats.dropped == 32


def test_regularize_ignores_double_time_downbeats_when_they_imply_an_absurd_tempo():
    from backline.tempo import regularize

    # Like "Don't Get Me Wrong": 43 % of the beats and all the downbeats are in double time.
    q = 0.58
    times = [i * q for i in range(12)] + [12 * q + k * q / 2 for k in range(20)]
    downbeats = [i * 2 * q for i in range(12)]  # half-bars: the tracker's bars are wrong too
    grid, stats = regularize(_grid(times, downbeats=downbeats, bpm=103))
    assert grid.bpm == pytest.approx(60 / q, abs=0.1), "103 bpm, not 207"
    assert stats.inserted == 0 and stats.dropped == 10


def _stem_with_drums_from(path, beats_times, start_at, sr=44100):
    """A drum stem: noise bursts on every beat from `start_at` on, silence before."""
    import soundfile as sf

    rng = np.random.default_rng(3)
    y = np.zeros(int((beats_times[-1] + 1.0) * sr))
    for t in beats_times:
        if t < start_at:
            continue
        i = int(t * sr)
        y[i : i + 3000] += rng.normal(0, 0.3, 3000) * np.exp(-np.arange(3000) / 700)
    sf.write(path, np.stack([y, y], 1), sr)
    return path


def test_choose_grid_goes_straight_through_a_strummed_intro(tmp_path):
    from backline.tempo import choose_grid

    period = 60 / 85.3
    true = [0.37 + k * period for k in range(120)]  # the song, dead straight, starts mid-bar
    drums_from = true[40]
    # the tracker: garbage in the intro (strums), near-perfect once the drums play
    rng = np.random.default_rng(0)
    intro = sorted(rng.uniform(0, drums_from - 0.3, 25))
    body = [t + rng.normal(0, 0.006) for t in true[40:]]
    tracked = intro + body
    downbeats = [true[k] for k in range(40, 120, 4)]
    beats = Beats(
        beats=[round(t, 4) for t in tracked],
        downbeats=[round(d, 4) for d in downbeats],
        bpm=85.7,
        meter=4,
        source="x",
    )
    stem = _stem_with_drums_from(tmp_path / "drums.wav", true, drums_from)

    grid, info = choose_grid(beats, stem, duration=true[-1] + 1.0)
    assert (
        info.mode == "straight" and info.residual_p95_ms is not None and info.residual_p95_ms < 20
    )
    assert abs(info.bpm - 85.3) < 0.2
    gb = np.array(grid.beats)
    assert np.allclose(np.diff(gb), period, atol=1e-3), "perfectly regular, intro included"
    assert abs(gb[0] - 0.37) < 0.02, "extrapolated back to the first beat after 0"
    assert all(any(abs(t - d) < 0.02 for d in grid.downbeats) for t in true[40:60:4]), (
        "bar lines phased to the drummed downbeats"
    )
    assert grid.first_downbeat == pytest.approx(0.37, abs=0.02)


def test_choose_grid_falls_back_when_the_tempo_drifts(tmp_path):
    from backline.tempo import choose_grid

    # a human drummer speeding up: 100 → 112 bpm over the song
    t, times = 0.0, []
    for k in range(160):
        times.append(t)
        t += 60 / (100 + 12 * k / 160)
    beats = Beats(
        beats=[round(x, 4) for x in times],
        downbeats=[round(times[k], 4) for k in range(0, 160, 4)],
        bpm=106,
        meter=4,
        source="x",
    )
    stem = _stem_with_drums_from(tmp_path / "drums.wav", times, 0.0)
    grid, info = choose_grid(beats, stem, duration=times[-1] + 1)
    assert info.mode == "tracked" and "too loose" in (info.reason or "")
    gb = np.array(grid.beats)
    assert len(gb) == len(times)
    assert np.abs(gb - np.array(times)).max() < 0.012, "follows the drift"
    assert np.all(np.diff(gb) > 0) and np.diff(gb)[-1] < np.diff(gb)[0], "still speeding up"


def test_smooth_removes_frame_jitter_but_keeps_drift():
    from backline.tempo import smooth

    rng = np.random.default_rng(1)
    t, true = 0.0, []
    for k in range(80):
        true.append(t)
        t += 60 / (100 + 10 * k / 80)
    jittered = np.round(np.array(true) + rng.uniform(-0.005, 0.005, 80), 2)  # 10 ms frames
    beats = Beats(beats=list(jittered), downbeats=list(jittered[::4]), bpm=105, meter=4, source="x")
    out = np.array(smooth(beats).beats)
    assert np.abs(out - np.array(true)).mean() < np.abs(jittered - np.array(true)).mean()

    def jitter(x):  # beat-to-beat change of the interval
        return np.std(np.diff(np.diff(x)))

    assert jitter(out) < 0.3 * jitter(jittered), "far less jitter"
    assert np.diff(out)[-1] < np.diff(out)[0], "the accelerando is still there"


def test_choose_grid_manual_bpm(tmp_path):
    from backline.tempo import choose_grid

    period = 0.5
    times = [1.0 + k * period for k in range(60)]
    beats = Beats(beats=times, downbeats=times[::4], bpm=120, meter=4, source="x")
    stem = _stem_with_drums_from(tmp_path / "drums.wav", times, 0.0)
    grid, info = choose_grid(beats, stem, duration=32.0, bpm=121.0)
    assert info.mode == "manual" and info.bpm == 121.0
    assert np.allclose(np.diff(grid.beats), 60 / 121.0, atol=1e-3)
    assert grid.beats[0] < 60 / 121.0, "extrapolated back to the start of the audio"

    grid, _ = choose_grid(beats, stem, duration=32.0, bpm=120.0)
    assert np.abs(np.array(grid.beats) - 1.0).min() < 0.01, "phase anchored on the tracked beats"
    assert 1.0 in grid.downbeats
    with pytest.raises(ValueError):
        choose_grid(beats, stem, duration=32.0, mode="sometimes")
