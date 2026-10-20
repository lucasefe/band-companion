from pathlib import Path

from backline import job as J
from backline.job import Job, sanitize_name, source_hash


def test_sanitize_name():
    assert sanitize_name('A/B: "C"?  ') == "A-B- -C"
    assert sanitize_name("   ") == "untitled"
    assert len(sanitize_name("x" * 200)) == 80
    assert sanitize_name("Fotografía") == "Fotografía", "accents are kept"


def test_source_hash_is_content_based(tmp_path: Path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    assert source_hash(a) == source_hash(b)
    assert len(source_hash(a)) == 8
    assert source_hash("https://youtu.be/x") != source_hash("https://youtu.be/y")


def test_job_layout_and_names(tmp_path: Path):
    job = Job.create(tmp_path, title="Fotografia", artist="Juanes", source="u", hash="abcd1234")
    assert job.dir == tmp_path / "Juanes - Fotografia (abcd1234)"
    assert all((job.dir / sub).is_dir() for sub in (J.INPUT, J.WORK, J.OUTPUT))
    assert (job.dir / J.MANIFEST).exists()
    assert job.file_stem == "Fotografia"
    assert job.project_path == job.dir / "Fotografia.RPP"
    assert job.output_dir() == job.dir / "output"
    assert job.output_dir(tmp_path / "songs" / "02 - Foto") == tmp_path / "songs" / "02 - Foto"
    assert job.path(J.ORIGINAL) == job.dir / "input" / "original.wav"

    solo = Job.create(tmp_path, title="NoPodras", artist=None, source="f", hash="00000000")
    assert solo.dir.name == "NoPodras (00000000)"

    same = Job.create(tmp_path, title="Other Title", artist="X", source="f", hash="00000000")
    assert same.dir == solo.dir, "the hash identifies the job, not the title"
    assert same.title == "NoPodras"


def test_job_caching_round_trip(tmp_path: Path):
    job = Job.create(tmp_path, title="Song", artist=None, source="/x/song.wav", hash="abcd1234")
    beats = job.path(J.BEATS)
    assert not job.is_done("tempo", {}, [beats])

    beats.write_text("{}")
    assert not job.is_done("tempo", {}, [beats]), "artifact alone is not enough"

    job.mark_done("tempo", {"model": "final"}, bpm=120)
    assert job.is_done("tempo", {"model": "final"}, [beats])
    assert not job.is_done("tempo", {"model": "small"}, [beats]), "different params invalidate"
    assert not job.is_done("tempo", {"model": "final"}, [beats, job.path("work/missing")])

    reopened = Job.open(job.dir)
    assert reopened.stage_info("tempo")["bpm"] == 120
    assert reopened.title == "Song" and reopened.artist is None
