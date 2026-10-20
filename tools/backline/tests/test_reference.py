from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backline.midi_out import CountIn, plan_layout
from backline.reference import CLICK_SECONDS, write_reference


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt((x**2).mean()))


def test_reference_is_on_the_count_in_timeline(loop_beats, loop_wav: Path, tmp_path: Path):
    count_in = CountIn(bars=2)
    layout = plan_layout(loop_beats, count_in)  # loop: downbeat at 0 → shift = 4.8 s
    dest = tmp_path / "loop.reference.wav"
    seconds = write_reference(loop_wav, dest, layout, count_in)

    audio, sr = sf.read(dest, dtype="float32", always_2d=True)
    info = sf.info(dest)
    assert info.subtype == "PCM_16" and info.channels == 2 and sr == 44100
    assert seconds == pytest.approx(4.8 + 9.6, abs=0.01)

    original, _ = sf.read(loop_wav, dtype="float32", always_2d=True)
    shift = int(round(layout.shift * sr))
    assert np.allclose(audio[shift : shift + 4410], original[:4410], atol=2 / 32768), (
        "the song starts exactly at shift, sample-aligned"
    )

    spb = 60.0 / loop_beats.bpm
    click = int(CLICK_SECONDS * sr)
    for k in range(8):
        start = int(round(k * spb * sr))
        assert _rms(audio[start : start + click]) > 0.1, f"click {k} present"
        between = audio[start + click + 100 : start + int(spb * sr) - 100]
        assert _rms(between) < 0.01, f"silence between clicks {k}"
    assert _rms(audio[:click]) > _rms(audio[int(spb * sr) : int(spb * sr) + click]), (
        "beat 1 accented"
    )


def test_reference_without_count_in_is_the_original(loop_beats, loop_wav: Path, tmp_path: Path):
    layout = plan_layout(loop_beats, None)
    dest = tmp_path / "ref.wav"
    seconds = write_reference(loop_wav, dest, layout, None)
    audio, _ = sf.read(dest, dtype="float32", always_2d=True)
    original, _ = sf.read(loop_wav, dtype="float32", always_2d=True)
    assert seconds == pytest.approx(9.6, abs=0.01)
    assert np.allclose(audio, original, atol=2 / 32768)
