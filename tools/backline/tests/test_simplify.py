from pathlib import Path

import numpy as np

from drums2midi.simplify import (
    GM_NOTES,
    count_by_class,
    load_notes,
    pick_hits,
    save_notes,
    simplify,
)
from drums2midi.transcribe import CLASSES, FPS


def synthetic_activations(hits: dict[str, list[tuple[float, float]]], seconds: float = 4.0):
    """Build [frames, 5] activations with a bump of the given height at each (time, height)."""
    act = np.zeros((int(seconds * FPS), len(CLASSES)), dtype=np.float32)
    for cls, events in hits.items():
        col = CLASSES.index(cls)
        for t, height in events:
            f = int(round(t * FPS))
            act[f - 1 : f + 2, col] = np.maximum(act[f - 1 : f + 2, col], height * 0.6)
            act[f, col] = height
    return act


def test_pick_hits_returns_times_and_confidence():
    act = synthetic_activations({"kick": [(0.5, 0.9), (1.5, 0.4)], "snare": [(1.0, 0.8)]})
    hits = pick_hits(act)
    assert [(h.cls, round(h.time, 2)) for h in hits] == [
        ("kick", 0.5),
        ("snare", 1.0),
        ("kick", 1.5),
    ]
    assert hits[0].confidence > hits[2].confidence


def test_raw_style_maps_to_gm_and_scales_velocity(loop_beats, tmp_path: Path):
    act = synthetic_activations({"kick": [(0.5, 1.0), (1.5, 0.3)], "hihat": [(1.0, 0.6)]})
    notes = simplify(act, loop_beats, style="raw")

    assert [n.note for n in notes] == [GM_NOTES["kick"], GM_NOTES["hihat"], GM_NOTES["kick"]]
    assert notes[0].velocity == 127
    assert notes[2].velocity < notes[0].velocity
    assert 1 <= notes[2].velocity <= 127
    assert count_by_class(notes) == {"kick": 2, "snare": 0, "tom": 0, "hihat": 1, "cymbal": 0}

    save_notes(tmp_path / "notes.json", notes, {"style": "raw"})
    assert load_notes(tmp_path / "notes.json") == notes
