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


def test_markers_are_written_at_project_level(loop_beats, tmp_path: Path):
    notes = [Note(0.0, "kick", 36, 100, 0.9)]
    seq = build_sequence(notes, loop_beats, count_in=CountIn(bars=1))
    path = tmp_path / "project.RPP"
    write_rpp(
        path,
        stem=AudioTrack("drums", "drums.wav", 9.6, seq.layout.shift),
        midi=[MidiTrack("m", "m", seq)],
        markers=[(2.4 + 17.49, 'Tal vez te "olvides"'), (-0.5, "clamped")],
    )
    text = path.read_text()
    head = text.split("<TRACK")[0]
    assert "  MARKER 1 19.89 \"Tal vez te 'olvides'\" 0\n" in head
    assert '  MARKER 2 0 "clamped" 0\n' in head


SAVED_BY_REAPER = """<REAPER_PROJECT 0.1 "7.82/macOS-arm64" 1792499948 0
  TEMPO 120 4 4 0
  <TRACK {AAA}
    NAME "Song (drums)"
    VOLPAN 0.35 -1 -1 -1 1
    <ITEM
      POSITION 2.54
    >
  >
  <TRACK {BBB}
    NAME "Song (MIDI conservative)"
    VOLPAN 1 0 -1 -1 1
    <FXCHAIN
      BYPASS 0 0 0
      <AU "AUi: EZdrummer 3 (Toontrack)" "Toontrack: EZdrummer 3" "" 1 2 3
        AAAA
        BBBB
      >
      FLOATPOS 0 0 0 0
      FXID {68395ED7-854B-4342-A6B0-F89AB1DB2E63}
      WAK 0 0
    >
    <ITEM
      POSITION 0
      <SOURCE MIDI
        HASDATA 1 480 QN
        E 0 99 24 75
      >
    >
  >
>
"""


def test_extract_fx_chain_from_the_midi_track():
    from drums2midi.reaper import extract_fx_chain

    chain = extract_fx_chain(SAVED_BY_REAPER, track_contains="MIDI")
    assert chain is not None
    lines = chain.splitlines()
    assert lines[0] == "    BYPASS 0 0 0"
    assert lines[1].startswith('    <AU "AUi: EZdrummer 3 (Toontrack)"')
    assert lines[-1] == "    WAK 0 0"
    assert "<SOURCE MIDI" not in chain and "<ITEM" not in chain
    assert extract_fx_chain(SAVED_BY_REAPER, track_contains="drums") is None


def test_is_generated_distinguishes_our_files_from_reaper_saves(tmp_path: Path):
    from drums2midi.reaper import HEADER, is_generated

    ours = tmp_path / "a.RPP"
    ours.write_text(HEADER + "\n>\n")
    theirs = tmp_path / "b.RPP"
    theirs.write_text(SAVED_BY_REAPER)
    assert is_generated(ours)
    assert not is_generated(theirs)
    assert not is_generated(tmp_path / "missing.RPP")


def test_fx_chain_and_render_settings_are_written(loop_beats, tmp_path: Path):
    from drums2midi.reaper import RenderSettings, extract_fx_chain, load_fx_template

    template = tmp_path / "midi-track.RfxChain"
    template.write_text(extract_fx_chain(SAVED_BY_REAPER))
    chain = load_fx_template(template)
    seq = build_sequence([Note(0.0, "kick", 36, 100, 0.9)], loop_beats, count_in=None)
    path = tmp_path / "project.RPP"
    write_rpp(
        path,
        stem=AudioTrack("drums", "drums.wav", 9.6, 0.0, muted=True),
        midi=[
            MidiTrack("a", "a", seq, fx_chain=chain),
            MidiTrack("b", "b", seq, fx_chain=chain, muted=True),
        ],
        render=RenderSettings(directory="/tmp/job", pattern="Song (practice)"),
    )
    text = path.read_text()
    assert text.count("<FXCHAIN") == 2 and text.count('<AU "AUi: EZdrummer 3') == 2
    fxids = re.findall(r"FXID (\{[0-9A-F-]+\})", text)
    assert len(fxids) == 2 and fxids[0] != fxids[1], "each track gets its own FX instance id"
    assert '  RENDER_FILE "/tmp/job"\n  RENDER_PATTERN "Song (practice)"\n' in text
    assert "  RENDER_FMT 0 2 44100\n" in text and "    ZXZhdxgAAQ==\n" in text
    opens = len(re.findall(r"^\s*<", text, re.M))
    closes = len(re.findall(r"^\s*>$", text, re.M))
    assert opens == closes


def test_load_fx_template_accepts_a_wrapped_block(tmp_path: Path):
    from drums2midi.reaper import load_fx_template

    p = tmp_path / "t.RfxChain"
    p.write_text("<FXCHAIN\n  BYPASS 0 0 0\n  <AU x\n    yy\n  >\n>\n")
    assert load_fx_template(p) == "    BYPASS 0 0 0\n    <AU x\n      yy\n    >\n"
    assert load_fx_template(tmp_path / "none") is None


def test_project_target_never_overwrites_a_reaper_save(tmp_path: Path):
    from drums2midi.job import Job
    from drums2midi.pipeline import project_target
    from drums2midi.reaper import HEADER

    job = Job.create(tmp_path, "Song", "/x.wav", "abcd1234")
    assert project_target(job) == job.path("project.RPP")
    job.path("project.RPP").write_text(HEADER + "\n>\n")
    assert project_target(job) == job.path("project.RPP"), "our own file is fair game"
    job.path("project.RPP").write_text(SAVED_BY_REAPER)
    assert project_target(job) == job.path("project.new.RPP")
