from pathlib import Path

from drums2midi.job import Job, sanitize_title, source_hash


def test_sanitize_title():
    assert sanitize_title('A/B: "C"?  ') == "A-B- -C"
    assert sanitize_title("   ") == "untitled"
    assert len(sanitize_title("x" * 200)) == 80


def test_source_hash_is_content_based(tmp_path: Path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    assert source_hash(a) == source_hash(b)
    assert len(source_hash(a)) == 8
    assert source_hash("https://youtu.be/x") != source_hash("https://youtu.be/y")


def test_job_caching_round_trip(tmp_path: Path):
    job = Job.create(tmp_path, "Song", "/x/song.wav", "abcd1234")
    assert job.dir == tmp_path / "Song (abcd1234)"
    assert not job.is_done("tempo")

    job.path("beats.json").write_text("{}")
    assert not job.is_done("tempo"), "artifact alone is not enough"

    job.mark_done("tempo", {"model": "final"}, bpm=120)
    assert job.is_done("tempo", {"model": "final"})
    assert not job.is_done("tempo", {"model": "small"}), "different params invalidate"

    reopened = Job.open(job.dir)
    assert reopened.stage_info("tempo")["bpm"] == 120
    assert reopened.title == "Song"
