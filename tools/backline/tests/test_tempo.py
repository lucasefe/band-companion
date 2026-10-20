from pathlib import Path

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
