import json
from pathlib import Path

from backline.catalog import RECORD_NAME, find_by_hash, read_record, write_record


def test_record_round_trip_and_lookup(tmp_path: Path):
    tracks = tmp_path / "tracks"
    a = tracks / "Set" / "01 - A"
    b = tracks / "Set" / "02 - B"
    a.mkdir(parents=True)
    b.mkdir()
    write_record(a, {"hash": "aaaa1111", "song": {"title": "A"}})
    write_record(b, {"hash": "bbbb2222", "song": {"title": "B"}})
    (tracks / "Set" / "03 - broken").mkdir()
    (tracks / "Set" / "03 - broken" / RECORD_NAME).write_text("not json")

    assert find_by_hash(tracks, "bbbb2222") == b
    assert find_by_hash(tracks, "cccc3333") is None
    assert find_by_hash(tmp_path / "missing", "aaaa1111") is None
    record = read_record(a)
    assert record and record["hash"] == "aaaa1111" and "updated" in record
    assert read_record(tracks / "Set" / "03 - broken") is None
    assert json.loads((a / RECORD_NAME).read_text())["song"] == {"title": "A"}
