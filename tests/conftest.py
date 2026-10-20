from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from fixtures.make_loop import make_loop

from drums2midi.tempo import Beats

warnings.simplefilter("ignore")

LOOP_BPM = 100.0
LOOP_BEATS = 16


@pytest.fixture(scope="session")
def loop_wav(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_loop(tmp_path_factory.mktemp("fixtures") / "loop.wav", LOOP_BPM, LOOP_BEATS)


@pytest.fixture
def loop_beats() -> Beats:
    """Ground-truth beats for the loop, so tests don't depend on the beat tracker."""
    spb = 60.0 / LOOP_BPM
    beats = [round(i * spb, 4) for i in range(LOOP_BEATS)]
    return Beats(beats=beats, downbeats=beats[::4], bpm=LOOP_BPM, meter=4, source="fixture")
