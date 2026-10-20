from pathlib import Path

import pytest

from drums2midi.pipeline import RunOptions, resimplify, run


@pytest.mark.slow
def test_full_pipeline_on_loop(loop_wav: Path, tmp_path: Path):
    opts = RunOptions(out_root=tmp_path, device="cpu", lyrics=False)
    result = run(str(loop_wav), opts)

    for name in (
        "original.wav",
        "drums.wav",
        "vocals.wav",
        "activations.npy",
        "lyrics.json",
        "beats.json",
        "notes.json",
        "notes-daring.json",
        "drums.mid",
        "drums-daring.mid",
        "project.RPP",
    ):
        assert result.job.path(name).exists(), name
    conservative, daring = result.counts["conservative"], result.counts["daring"]
    assert conservative["kick"] >= 6
    assert conservative["snare"] >= 6
    assert conservative["hihat"] >= 20
    assert all(daring[c] >= conservative[c] for c in conservative), "daring is a superset"
    assert abs(result.beats.bpm - 100) <= 1
    assert set(result.timings) == {
        "ingest",
        "separate",
        "transcribe",
        "tempo",
        "lyrics",
        "simplify",
        "output",
    }

    again = run(str(loop_wav), opts)
    assert again.timings == {}, "second run is fully cached"

    assert result.lyrics == {"found": False, "lines_song_time": [], "reason": "disabled"}

    re = resimplify(result.job.dir, count_in=0)
    assert set(re.timings) == {"output"}, "saved options are reused; only count_in changed"
    assert re.layout.shift == 0
