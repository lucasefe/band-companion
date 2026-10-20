from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backline.metronome import Gap, detect_gaps, gaps_in_seconds, write_metronome
from backline.midi_out import CountIn, plan_layout
from backline.reference import CLICK_SECONDS
from backline.tempo import Beats

SR = 44100
BPM = 120.0
SPB = 60.0 / BPM


def _beats(n_beats: int) -> Beats:
    times = [round(i * SPB, 4) for i in range(n_beats)]
    return Beats(beats=times, downbeats=times[::4], bpm=BPM, meter=4, source="fixture")


def _drum_stem(path: Path, n_beats: int, silent_beats: set[int]) -> Path:
    """Noise bursts on every beat except the silent ones (bars without drums)."""
    rng = np.random.default_rng(1)
    y = np.zeros(int(n_beats * SPB * SR) + SR)
    for b in range(n_beats):
        if b in silent_beats:
            continue
        i = int(b * SPB * SR)
        y[i : i + 4000] += rng.normal(0, 0.3, 4000) * np.exp(-np.arange(4000) / 800)
    y += rng.normal(0, 0.0005, len(y))  # bleed
    sf.write(path, np.stack([y, y], 1), SR)
    return path


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt((x**2).mean()))


def test_detect_gaps_finds_bars_without_drums_and_snaps_to_bars(tmp_path: Path):
    beats = _beats(32)  # 8 bars
    # drums on bars 1–2, silent bars 3–4 (beats 8..15), drums 5–6, a 2-beat hiccup in bar 7, drums 8
    silent = set(range(8, 16)) | {26, 27}
    stem = _drum_stem(tmp_path / "drums.wav", 32, silent)

    gaps = detect_gaps(stem, beats, min_bars=1)
    assert gaps == [Gap(8, 16)], "the 2-beat hiccup is shorter than a bar and ignored"

    gaps = detect_gaps(stem, beats, min_bars=2)
    assert gaps == [Gap(8, 16)]

    # a silent run that starts mid-bar is widened to the bar it starts in
    stem2 = _drum_stem(tmp_path / "drums2.wav", 32, set(range(10, 16)))
    assert detect_gaps(stem2, beats, min_bars=1) == [Gap(8, 16)]

    info = gaps_in_seconds([Gap(8, 16)], beats, shift=4.0)
    assert info == [{"start": 8.0, "end": 12.0, "bars": 2.0, "bar": 3}]


def test_detect_gaps_intro_before_the_drums_enter(tmp_path: Path):
    beats = _beats(16)
    stem = _drum_stem(tmp_path / "drums.wav", 16, set(range(0, 8)))
    assert detect_gaps(stem, beats) == [Gap(0, 8)]


def test_metronome_gaps_mode_clicks_only_in_gaps_and_count_in(tmp_path: Path):
    beats = _beats(16)
    count_in = CountIn(bars=1)
    layout = plan_layout(beats, count_in)  # first downbeat at 0 → shift = 2.0 s
    dest = tmp_path / "metronome.wav"
    write_metronome(dest, layout, beats, [Gap(8, 12)], mode="gaps", count_in=count_in)

    y, sr = sf.read(dest, dtype="float32")
    assert y.ndim == 1 and sr == SR and sf.info(dest).subtype == "PCM_16"
    n = int(CLICK_SECONDS * sr)

    def level_at(seconds: float) -> float:
        i = int(round(seconds * sr))
        return _rms(y[i : i + n])

    for k in range(4):  # count-in clicks
        assert level_at(k * SPB) > 0.1
    for b in range(16):  # song beats: only 8..11 click
        t = layout.shift + b * SPB
        if 8 <= b < 12:
            assert level_at(t) > 0.1, f"beat {b} should click"
        else:
            assert level_at(t) < 0.005, f"beat {b} should be silent"
    assert level_at(layout.shift + 8 * SPB) > level_at(layout.shift + 9 * SPB), "beat 1 is accented"


def test_metronome_always_and_off(tmp_path: Path):
    beats = _beats(8)
    layout = plan_layout(beats, None)
    n = int(CLICK_SECONDS * SR)

    write_metronome(tmp_path / "always.wav", layout, beats, [Gap(4, 8)], mode="always")
    y, _ = sf.read(tmp_path / "always.wav", dtype="float32")
    steady = _rms(y[int(1 * SPB * SR) : int(1 * SPB * SR) + n])
    in_gap = _rms(y[int(5 * SPB * SR) : int(5 * SPB * SR) + n])
    assert steady > 0.05 and in_gap > steady, "gap clicks are louder than steady ones"

    write_metronome(tmp_path / "off.wav", layout, beats, [Gap(4, 8)], mode="off")
    y, _ = sf.read(tmp_path / "off.wav", dtype="float32")
    assert float(np.abs(y).max()) == 0.0

    with pytest.raises(ValueError):
        write_metronome(tmp_path / "x.wav", layout, beats, [], mode="sometimes")
