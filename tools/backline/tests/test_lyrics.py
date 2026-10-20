from pathlib import Path

import numpy as np
import pytest

from backline import lrclib
from backline.lyrics import (
    FRAME_RATE,
    Hints,
    Line,
    Lyrics,
    countdown_lines,
    estimate_offset,
    format_lrc,
    hints_from_youtube,
    lyrics_from_lrclib,
    parse_lrc,
    plain_text,
    split_artist_title,
)

LRC = """[ar:Cristian Castro]
[ti:No Podrás]
[00:17.491]Tal vez te olvides de mi
[00:25.63]Pero esta vez aprendí
[00:33.861] 
[01:07.2]Si estoy llorando
[02:00.00][03:00.00]Repeated chorus line
Credits without a timestamp
"""


def test_parse_lrc_handles_formats_blank_lines_and_repeats():
    lines = parse_lrc(LRC)
    timed = [(ln.time, ln.text) for ln in lines if ln.time is not None]
    assert timed[0] == (pytest.approx(17.491), "Tal vez te olvides de mi")
    assert timed[1] == (pytest.approx(25.63), "Pero esta vez aprendí")
    assert timed[2] == (pytest.approx(33.861), "")
    assert timed[3] == (pytest.approx(67.2), "Si estoy llorando")
    assert [t for t, text in timed if text == "Repeated chorus line"] == [120.0, 180.0]
    untimed = [ln.text for ln in lines if ln.time is None]
    assert untimed == ["Credits without a timestamp"], "header tags are not lyrics"


def test_format_lrc_sorts_clamps_and_round_trips():
    ly = Lyrics(parse_lrc(LRC), source="file:x", synced=True, offset=1.5)
    lines = [(ln.time + ly.offset, ln.text) for ln in ly.timed] + [(-0.3, "pickup")]
    out = format_lrc(lines, header={"ar": "Cristian Castro", "ti": "No Podrás", "al": ""})
    assert out.startswith("[ar:Cristian Castro]\n[ti:No Podrás]\n[00:00.00]pickup\n")
    assert "[00:18.99]Tal vez te olvides de mi" in out
    assert "[00:35.36]" not in out, "blank timed lines are not in `timed`"
    assert "[01:08.70]Si estoy llorando" in out
    assert parse_lrc(out)[1].time == pytest.approx(18.99)
    assert "Credits without a timestamp" in plain_text(ly)


def test_countdown_lines_tick_once_per_click():
    lines = countdown_lines(0.0, bars=2, meter=4, seconds_per_beat=0.5)
    assert [t for t, _ in lines] == [i * 0.5 for i in range(8)]
    assert [text for _, text in lines] == ["1", "2", "3", "4", "1", "2", "3", "4"]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Juanes - Fotografia ft. Nelly Furtado", ("Juanes", "Fotografia")),
        ("01 - No podrás", (None, "No podrás")),
        ("03. Artist – Title (Official Video)", ("Artist", "Title")),
        ("Song Title [Official Audio]", (None, "Song Title")),
        ("NoPodras", (None, "NoPodras")),
    ],
)
def test_split_artist_title(name, expected):
    assert split_artist_title(name) == expected


def test_hints_from_youtube_prefers_track_fields_then_title_then_channel():
    meta = {"title": "Juanes - Fotografia ft. Nelly Furtado", "channel": "Juanes"}
    h = hints_from_youtube(meta, 244.0)
    assert (h.artist, h.title, h.duration) == ("Juanes", "Fotografia", 244.0)
    meta = {"title": "Some Upload", "channel": "A Channel", "track": "Real Title", "artist": "Real"}
    h = hints_from_youtube(meta, 100.0)
    assert (h.artist, h.title) == ("Real", "Real Title")


def test_normalize_and_similarity():
    assert lrclib.normalize("Fotografía (feat. Nelly Furtado)") == "fotografia"
    assert lrclib.normalize("No Podrás") == lrclib.normalize("no podras")
    assert lrclib.similarity("Fotografia", "Fotografía") == 1.0
    assert lrclib.similarity("Fotografia", "La Camisa Negra") < 0.5


def _cand(id, title, artist, duration, synced=True, album="x", instrumental=False):
    return lrclib.Candidate(
        id, artist, title, album, duration, "[00:01.00]x" if synced else None, "x", instrumental
    )


def test_rank_prefers_synced_and_closest_duration():
    cands = [
        _cand(1, "Fotografia", "Juanes", 300.0),
        _cand(2, "Fotografia", "Juanes", 241.0),
        _cand(3, "Fotografia", "Juanes", 240.5, synced=False),
        _cand(4, "La Camisa Negra", "Juanes", 241.0),
        _cand(5, "Fotografia", "Juanes", 241.0, instrumental=True),
    ]
    ranked = lrclib.rank(cands, title="Fotografia", artist="Juanes", duration=241.0)
    assert [c.id for c in ranked][:2] == [2, 3]
    assert ranked[0].score >= lrclib.ACCEPT_SCORE
    assert ranked[-1].id in (4, 5)
    assert next(c for c in ranked if c.id == 4).score < lrclib.ACCEPT_SCORE


def test_lyrics_from_lrclib_uses_search_and_falls_back_to_title_only(monkeypatch):
    calls = []

    def fake_search(*, artist=None, title=None, query=None):
        calls.append((artist, title, query))
        if artist:
            return []
        return [_cand(7, "No Podrás", "Cristian Castro", 243.0)]

    monkeypatch.setattr(lrclib, "search", fake_search)
    found, considered = lyrics_from_lrclib(Hints(artist="Wrong", title="No podras", duration=243))
    assert found is not None and found.source == "lrclib:7" and found.synced
    assert calls == [("Wrong", "No podras", None), (None, None, "Wrong No podras")]
    assert considered and considered[0]["id"] == 7


def test_lyrics_from_lrclib_tries_title_only_when_artist_is_wrong(monkeypatch):
    calls = []

    def fake_search(*, artist=None, title=None, query=None):
        calls.append((artist, title, query))
        return [_cand(7, "No Podrás", "Cristian Castro", 243.0)] if (title and not artist) else []

    monkeypatch.setattr(lrclib, "search", fake_search)
    found, _ = lyrics_from_lrclib(Hints(artist="Wrong", title="No podras", duration=243))
    assert found is not None and found.source == "lrclib:7"
    assert calls[-1] == (None, "No podras", None)


def test_lyrics_from_lrclib_returns_none_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(lrclib, "search", lambda **kw: [_cand(1, "Other Song", "X", 100.0)])
    found, considered = lyrics_from_lrclib(Hints(title="No podras", duration=243))
    assert found is None
    assert considered[0]["id"] == 1


def _activity_with_phrases(starts: list[float], length=60.0, phrase=2.0) -> np.ndarray:
    act = np.zeros(int(length * FRAME_RATE))
    for s in starts:
        act[int(s * FRAME_RATE) : int((s + phrase) * FRAME_RATE)] = 1.0
    return act


def test_estimate_offset_ignores_small_shifts_but_catches_gross_ones():
    phrases = [10.0, 14.0, 18.0, 22.0, 30.0, 34.0, 38.0, 42.0]
    act = _activity_with_phrases(phrases)

    def lyr(shift):
        return Lyrics([Line(t - shift, "la") for t in phrases], "x", True)

    small = estimate_offset(lyr(0.2), act)  # lines tagged 0.2 s early: convention, leave alone
    assert small.applied == 0.0
    assert small.suggested == pytest.approx(0.2, abs=0.03)
    assert small.agreement_at_zero == 1.0

    gross = estimate_offset(lyr(2.3), act)  # lyrics for a cut with a 2.3 s shorter intro
    assert gross.applied == pytest.approx(2.3, abs=0.03)
    assert gross.agreement_at_applied == 1.0

    none = estimate_offset(Lyrics([Line(1.0, "x")], "x", True), act)
    assert none.applied == 0.0


def test_lyrics_from_file_round_trip(tmp_path: Path):
    from backline.lyrics import lyrics_from_file

    p = tmp_path / "song.lrc"
    p.write_text(LRC, encoding="utf-8")
    ly = lyrics_from_file(p)
    assert ly.synced and ly.source == "file:song.lrc"
    assert len(ly.timed) == 5  # the blank timed line is excluded from `timed`
