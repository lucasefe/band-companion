import json
from pathlib import Path

import pytest

from backline import job as J
from backline.config import Config
from backline.pipeline import RunOptions, resimplify, run


def _config(tmp_path: Path, tracks: Path | None = None, default_set: str | None = None) -> Config:
    return Config(
        path=None,
        jobs=tmp_path / "jobs",
        tracks=tracks,
        default_set=default_set,
        count_in_bars=2,
        count_in_note=37,
    )


@pytest.mark.slow
def test_full_pipeline_on_loop(loop_wav: Path, tmp_path: Path):
    cfg = _config(tmp_path)
    opts = RunOptions(out_root=cfg.jobs, device="cpu", lyrics=False)
    result = run(str(loop_wav), opts, config=cfg)
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
        "output/loop.reference.wav",
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
    assert result.output_dir == job.dir / "output", "no tracks folder configured → output/"
    assert result.outputs["render"] == job.dir / "output" / "loop.wav"
    rpp = result.project_path.read_text()
    assert 'FILE "work/drums.wav"' in rpp and 'FILE "input/original.wav"' in rpp
    assert f'RENDER_FILE "{job.dir / "output"}"' in rpp and 'RENDER_PATTERN "loop"' in rpp
    original_track = rpp.split("<TRACK")[4]
    assert "MUTESOLO 1 0 0" in original_track, "original is muted so the render is drums only"

    again = run(str(loop_wav), opts, config=cfg)
    assert again.timings == {}, "second run is fully cached"

    deliver = tmp_path / "tracks" / "Set" / "03 - Loop"
    re = resimplify(job.dir, config=cfg, count_in=0, deliver_to=deliver)
    assert set(re.timings) == {"output"}, "saved options are reused; only count_in/--to changed"
    assert re.layout.shift == 0
    assert re.output_dir == deliver and deliver.is_dir()
    assert (deliver / "loop.reference.wav").exists()
    assert re.outputs["midi"] == job.dir / "output" / "loop.mid", "MIDI stays in the job"
    assert f'RENDER_FILE "{deliver}"' in result.project_path.read_text()

    again = resimplify(job.dir, config=cfg)
    assert again.timings == {} and again.output_dir == deliver, "the folder sticks"


@pytest.mark.slow
def test_song_folder_comes_from_the_config(loop_wav: Path, tmp_path: Path):
    tracks = tmp_path / "tracks"
    (tracks / "Gig" / "01 - Opener").mkdir(parents=True)
    cfg = _config(tmp_path, tracks=tracks, default_set="Gig")
    result = run(
        str(loop_wav), RunOptions(out_root=cfg.jobs, device="cpu", lyrics=False), config=cfg
    )
    assert result.output_dir == tracks / "Gig" / "02 - loop"
    assert (result.output_dir / "loop.reference.wav").exists()
    assert result.job.manifest["options"]["deliver_to"] == str(result.output_dir)
    assert not result.deduped

    record = json.loads((result.output_dir / "backline.json").read_text())
    assert record["hash"] == result.job.manifest["hash"]
    assert record["song"] == {"artist": None, "title": "loop"}
    assert record["source"]["kind"] == "file"
    assert record["source"]["path"] == str(loop_wav.resolve())
    assert record["files"] == {
        "lyrics": None,
        "reference": "loop.reference.wav",
        "drums": "loop.wav",
    }
    assert record["timeline"]["count_in_bars"] == 2 and record["timeline"]["bpm"] == 100
    assert record["lyrics"] == {"found": False, "reason": "disabled"}

    # The same source again, even under another title and set, is recognised and reused.
    again = run(
        str(loop_wav),
        RunOptions(
            out_root=cfg.jobs, device="cpu", lyrics=False, title="Renamed", set_name="Other"
        ),
        config=cfg,
    )
    assert again.deduped
    assert again.output_dir == tracks / "Gig" / "02 - loop"
    assert not (tracks / "Other").exists()
    assert again.job.dir == result.job.dir, "the hash identifies the job"
    assert set(again.timings) <= {"lyrics", "output"}, "a new --title only re-triggers the lyrics"
