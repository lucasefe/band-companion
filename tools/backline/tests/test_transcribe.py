from pathlib import Path

import numpy as np

from drums2midi.simplify import pick_hits
from drums2midi.transcribe import CLASSES, FPS, load_activations, transcribe


def test_adtof_finds_the_loop_pattern(loop_wav: Path, tmp_path: Path):
    activations = transcribe(loop_wav, tmp_path / "activations.npy")

    assert activations.shape[1] == len(CLASSES)
    assert activations.dtype == np.float32
    assert abs(activations.shape[0] / FPS - 9.6) < 0.5, "100 fps over a 9.6 s loop"
    assert np.array_equal(load_activations(tmp_path / "activations.npy"), activations)

    hits = pick_hits(activations)
    counts = {cls: sum(h.cls == cls for h in hits) for cls in CLASSES}
    assert counts["kick"] == 8
    assert counts["snare"] == 8
    assert 30 <= counts["hihat"] <= 34
    assert counts["tom"] + counts["cymbal"] <= 2

    kicks = sorted(h.time for h in hits if h.cls == "kick")
    expected = [i * 0.6 for i in range(0, 16, 2)]
    # The very first kick sits at t=0 with no audio before it, so ADTOF places it late.
    assert np.allclose(kicks[1:], expected[1:], atol=0.03)
    assert all(0 < h.confidence <= 1 for h in hits)
