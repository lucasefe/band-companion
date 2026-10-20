from pathlib import Path

import mido
import pytest

from drums2midi.midi_out import (
    NOTE_OFF,
    NOTE_ON,
    PPQ,
    CountIn,
    TempoMap,
    build_sequence,
    plan_layout,
    write_midi,
)
from drums2midi.simplify import Note
from drums2midi.tempo import Beats


def note_ons(path: Path) -> list[tuple[int, int, int]]:
    """(absolute tick, note, velocity) for every note_on."""
    midi = mido.MidiFile(path)
    out, tick = [], 0
    for msg in midi.tracks[0]:
        tick += msg.time
        if msg.type == "note_on":
            out.append((tick, msg.note, msg.velocity))
    return out


def test_tempo_map_constant():
    tm = TempoMap.constant(120)
    assert tm.tick(0) == 0
    assert tm.tick(0.5) == PPQ
    assert tm.tick(2.0) == 4 * PPQ
    assert tm.seconds(4 * PPQ) == pytest.approx(2.0)
    assert tm.tempo_events() == [(0, 500_000)]


def test_layout_count_in_ends_on_first_downbeat(loop_beats):
    layout = plan_layout(loop_beats, CountIn(bars=2))
    assert layout.count_in_seconds == pytest.approx(4.8)  # 8 beats at 100 bpm
    assert layout.shift == pytest.approx(4.8)  # loop's downbeat is at 0
    assert layout.count_in_start == 0
    assert layout.first_downbeat == pytest.approx(4.8)


def test_layout_with_pickup_before_downbeat():
    beats = Beats(beats=[0.42, 0.94, 1.46, 1.96], downbeats=[1.46], bpm=120, meter=4, source="x")
    layout = plan_layout(beats, CountIn(bars=2))
    assert layout.count_in_seconds == pytest.approx(4.0)
    assert layout.shift == pytest.approx(4.0 - 1.46)
    assert layout.first_downbeat == pytest.approx(4.0)


def test_layout_long_intro_does_not_go_negative():
    beats = Beats(beats=[10.0, 10.5, 11.0, 11.5], downbeats=[10.0], bpm=120, meter=4, source="x")
    layout = plan_layout(beats, CountIn(bars=2))
    assert layout.shift == 0
    assert layout.count_in_start == pytest.approx(6.0)


def test_layout_without_count_in(loop_beats):
    layout = plan_layout(loop_beats, None)
    assert layout.shift == 0 and layout.count_in_seconds == 0


def test_sequence_orders_offs_before_ons_and_ends_on_a_bar(loop_beats):
    notes = [Note(0.0, "kick", 36, 100, 0.9), Note(0.6, "snare", 38, 90, 0.8)]
    seq = build_sequence(notes, loop_beats, count_in=None)
    assert [(e.tick, e.status) for e in seq.events] == [
        (0, NOTE_ON),
        (PPQ // 8, NOTE_OFF),
        (PPQ, NOTE_ON),
        (PPQ + PPQ // 8, NOTE_OFF),
    ]
    assert seq.end_tick == 4 * PPQ, "one full bar"


def test_write_midi_count_in_and_shifted_notes(loop_beats, tmp_path: Path):
    notes = [Note(0.0, "kick", 36, 100, 0.9), Note(0.6, "snare", 38, 90, 0.8)]
    seq = build_sequence(notes, loop_beats, count_in=CountIn(bars=2, note=37))
    path = tmp_path / "drums.mid"
    write_midi(path, seq)

    ons = note_ons(path)
    clicks = [o for o in ons if o[1] == 37]
    assert [tick for tick, _, _ in clicks] == [i * PPQ for i in range(8)]
    assert [vel for _, _, vel in clicks] == [110, 85, 85, 85, 110, 85, 85, 85]

    kick, snare = [o for o in ons if o[1] in (36, 38)]
    assert kick == (8 * PPQ, 36, 100), "first kick lands exactly one beat after the last click"
    assert snare[0] == 9 * PPQ
    assert seq.layout.shift == pytest.approx(4.8)

    midi = mido.MidiFile(path)
    assert midi.ticks_per_beat == PPQ
    metas = {m.type: m for m in midi.tracks[0] if m.is_meta}
    assert metas["set_tempo"].tempo == 600_000
    assert (metas["time_signature"].numerator, metas["time_signature"].denominator) == (4, 4)
    assert all(m.channel == 9 for m in midi.tracks[0] if m.type == "note_on")


def test_write_midi_without_count_in_keeps_song_time(loop_beats, tmp_path: Path):
    notes = [Note(1.2, "kick", 36, 100, 0.9)]
    path = tmp_path / "drums.mid"
    write_midi(path, build_sequence(notes, loop_beats, count_in=None))
    assert note_ons(path) == [(2 * PPQ, 36, 100)]


def test_write_midi_embeds_lyric_events(loop_beats, tmp_path: Path):
    notes = [Note(0.0, "kick", 36, 100, 0.9)]
    seq = build_sequence(notes, loop_beats, count_in=None)
    path = tmp_path / "drums.mid"
    write_midi(path, seq, lyrics=[(0.0, "Tal vez te olvides de mí"), (1.2, "Pero esta vez")])
    midi = mido.MidiFile(path, charset="utf-8")
    lyric_msgs, tick = [], 0
    for msg in midi.tracks[0]:
        tick += msg.time
        if msg.type == "lyrics":
            lyric_msgs.append((tick, msg.text))
    assert lyric_msgs == [(0, "Tal vez te olvides de mí"), (2 * PPQ, "Pero esta vez")]
