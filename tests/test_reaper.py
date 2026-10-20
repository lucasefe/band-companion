import re
from pathlib import Path

import pytest

from drums2midi.midi_out import PPQ, CountIn, build_sequence
from drums2midi.reaper import AudioTrack, MidiTrack, write_rpp
from drums2midi.simplify import Note


@pytest.fixture
def project(loop_beats, tmp_path: Path) -> str:
    notes = [Note(0.0, "kick", 36, 100, 0.9), Note(0.6, "snare", 38, 90, 0.8)]
    seq = build_sequence(notes, loop_beats, count_in=CountIn(bars=2, note=37))
    daring = build_sequence(notes + [Note(0.3, "tom", 45, 50, 0.1)], loop_beats, count_in=None)
    path = tmp_path / "project.RPP"
    write_rpp(
        path,
        stem=AudioTrack("drums", "drums.wav", 9.6, seq.layout.shift, pan=-1, volume_db=-6),
        midi=[
            MidiTrack("Song 'A' (MIDI)", 'Song "A"', seq, pan=1),
            MidiTrack("daring", "daring", daring, pan=1, muted=True),
        ],
        original=AudioTrack("orig", "original.wav", 9.6, seq.layout.shift, muted=True),
    )
    return path.read_text()


def test_project_structure(project: str):
    assert project.startswith("<REAPER_PROJECT 0.1")
    assert project.rstrip().endswith(">")
    assert "  TEMPO 100 4 4\n" in project
    assert project.count("  <TRACK {") == 4
    assert project.count("<SOURCE WAVE") == 2
    assert project.count("<SOURCE MIDI") == 2
    # every block opened is closed (same indentation)
    opens = len(re.findall(r"^\s*<", project, re.M))
    closes = len(re.findall(r"^\s*>$", project, re.M))
    assert opens == closes


def test_audio_tracks_are_positioned_and_panned(project: str):
    stem = project.split("<TRACK")[1]
    assert 'NAME "drums"' in stem
    assert "VOLPAN 0.5011872336 -1 -1 -1 1" in stem  # -6 dB, hard left
    assert "MUTESOLO 0 0 0" in stem
    assert "POSITION 4.8" in stem
    assert 'FILE "drums.wav"' in stem

    original = project.split("<TRACK")[4]
    assert "MUTESOLO 1 0 0" in original
    assert "VOLPAN 1 0 -1 -1 1" in original


def test_secondary_midi_track_is_muted(project: str):
    daring = project.split("<TRACK")[3]
    assert 'NAME "daring"' in daring
    assert "MUTESOLO 1 0 0" in daring
    assert daring.count(" 99 ") == 3, "two notes plus the extra tom"


def test_midi_track_mirrors_the_sequence(project: str):
    midi = project.split("<TRACK")[2]
    assert "VOLPAN 1 1 -1 -1 1" in midi  # hard right
    assert "MUTESOLO 0 0 0" in midi
    assert "NAME \"Song 'A' (MIDI)\"" in midi
    assert "NAME \"Song 'A'\"" in midi, "double quotes are not allowed inside names"
    assert "POSITION 0\n" in midi
    assert f"HASDATA 1 {PPQ} QN" in midi

    events = re.findall(r"^\s+E (\d+) ([0-9a-f]{2}) ([0-9a-f]{2}) ([0-9a-f]{2})$", midi, re.M)
    ons = [e for e in events if e[1] == "99"]
    offs = [e for e in events if e[1] == "89"]
    assert len(ons) == 8 + 2 and len(offs) == 8 + 2
    assert events[0] == ("0", "99", "25", "6e"), "first click: side stick, velocity 110"
    assert events[-1][1:] == ("b0", "7b", "00"), "ends with all-notes-off"

    total_ticks = sum(int(e[0]) for e in events)
    assert total_ticks % (4 * PPQ) == 0, "source ends on a bar line"
    assert "IGNTEMPO 0 100 4 4" in midi
