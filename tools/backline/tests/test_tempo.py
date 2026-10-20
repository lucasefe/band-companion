from pathlib import Path

import pytest

from drums2midi.tempo import Beats, detect_beats


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
