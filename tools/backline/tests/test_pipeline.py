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
        metronome="gaps",
        metronome_min_bars=1,
        metronome_silence_db=-30.0,
        metronome_volume_db=-17.0,
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
        J.METRONOME,
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
    tracks = rpp.split("<TRACK")
    assert (
        'NAME "loop (metronome)"' in tracks[1] and "VOLPAN 0.1412537545 -1 -1 -1 1" in tracks[1]
    ), "click hard left at -17 dB"
    assert 'FILE "work/metronome.wav"' in tracks[1]
    assert 'NAME "loop (MIDI conservative)"' in tracks[3] and "VOLPAN 1 0 -1 -1 1" in tracks[3], (
        "drums centred"
    )
    assert "MUTESOLO 1 0 0" in tracks[5], "original is muted so the render is click + drums only"
    record = json.loads((result.output_dir / "backline.json").read_text())
    assert record["metronome"]["mode"] == "gaps"
    assert isinstance(record["metronome"]["gaps"], list)

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


@pytest.mark.slow
def test_rename_moves_everything_and_keeps_the_render(loop_wav: Path, tmp_path: Path):
    from backline.pipeline import rename

    tracks = tmp_path / "tracks"
    cfg = _config(tmp_path, tracks=tracks, default_set="Gig")
    result = run(
        str(loop_wav), RunOptions(out_root=cfg.jobs, device="cpu", lyrics=False), config=cfg
    )
    folder = result.output_dir
    assert folder == tracks / "Gig" / "01 - loop"
    (folder / "loop.wav").write_bytes(b"RIFF-fake-render")  # as if rendered from Reaper
    old_job = result.job.dir

    renamed = rename(old_job, title="Loop Song", artist="The Fixtures", config=cfg)
    job = renamed.job
    hash_part = old_job.name.split("(")[1]
    assert job.dir == cfg.jobs / f"The Fixtures - Loop Song ({hash_part}"
    assert not old_job.exists()
    assert job.project_path == job.dir / "Loop Song.RPP" and job.project_path.exists()
    assert not (job.dir / "loop.RPP").exists()

    new_folder = tracks / "Gig" / "01 - Loop Song"
    assert renamed.output_dir == new_folder and not folder.exists()
    assert (new_folder / "Loop Song.wav").read_bytes() == b"RIFF-fake-render", "render kept"
    assert (new_folder / "Loop Song.reference.wav").exists()
    assert not (new_folder / "loop.reference.wav").exists()
    assert (job.dir / "output" / "Loop Song.mid").exists()
    record = json.loads((new_folder / "backline.json").read_text())
    assert record["song"] == {"artist": "The Fixtures", "title": "Loop Song"}
    assert 'RENDER_PATTERN "Loop Song"' in job.project_path.read_text()
    assert set(renamed.timings) == {"output"}
