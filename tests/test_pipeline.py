import json
from pathlib import Path

import pytest

from drums2midi import job as J
from drums2midi.pipeline import RunOptions, resimplify, run


@pytest.mark.slow
def test_full_pipeline_on_loop(loop_wav: Path, tmp_path: Path):
    opts = RunOptions(out_root=tmp_path / "jobs", device="cpu", lyrics=False)
    result = run(str(loop_wav), opts)
    job = result.job

    assert job.dir.parent == tmp_path / "jobs"
    assert job.dir.name.startswith("loop (")
    for rel in (
        J.ORIGINAL,
        J.SOURCE_JSON,
        "input/source.wav",
        J.DRUMS,
        J.VOCALS,
        J.ACTIVATIONS,
        J.BEATS,
        J.LYRICS_JSON,
        "work/notes.json",
        "work/notes-daring.json",
        "output/loop.mid",
        "output/loop (daring).mid",
        "loop.RPP",
    ):
        assert job.path(rel).exists(), rel
    root_entries = sorted(p.name for p in job.dir.iterdir())
    assert root_entries == ["input", "loop.RPP", "output", "work"], "nothing loose in the root"
    assert json.loads(job.path(J.SOURCE_JSON).read_text())["kind"] == "file"

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
    assert result.lyrics == {"found": False, "lines_song_time": [], "reason": "disabled"}
    assert result.project_path == job.dir / "loop.RPP"
    assert result.outputs["render"] == job.dir / "output" / "loop.wav"
    rpp = result.project_path.read_text()
    assert 'FILE "work/drums.wav"' in rpp and 'FILE "input/original.wav"' in rpp
    assert f'RENDER_FILE "{job.dir / "output"}"' in rpp and 'RENDER_PATTERN "loop"' in rpp

    again = run(str(loop_wav), opts)
    assert again.timings == {}, "second run is fully cached"

    deliver = tmp_path / "tracks" / "Set" / "03 - Loop"
    re = resimplify(job.dir, count_in=0, deliver_to=deliver)
    assert set(re.timings) == {"output"}, "saved options are reused; only count_in/--to changed"
    assert re.layout.shift == 0
    assert re.output_dir == deliver and deliver.is_dir()
    assert re.outputs["midi"] == job.dir / "output" / "loop.mid", "MIDI stays in the job"
    assert f'RENDER_FILE "{deliver}"' in result.project_path.read_text()
