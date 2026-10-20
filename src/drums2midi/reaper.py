from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from drums2midi.midi_out import PPQ, Sequence

STEM_VOLUME_DB = -9.0  # a dry stem next to a drum VSTi; tweak in Reaper if needed


@dataclass
class AudioTrack:
    name: str
    file: str  # relative to the project file
    duration: float
    position: float
    pan: float = 0.0  # -1 left … +1 right
    volume_db: float = 0.0
    muted: bool = False


@dataclass
class MidiTrack:
    name: str
    item_name: str
    seq: Sequence
    pan: float = 0.0
    muted: bool = False


def write_rpp(
    path: Path,
    *,
    stem: AudioTrack,
    midi: list[MidiTrack],
    original: AudioTrack | None = None,
    markers: list[tuple[float, str]] | None = None,
) -> None:
    """Reaper project: drum stem panned left, MIDI panned right, same timeline as drums.mid.

    `markers` are (output-time seconds, text) pairs shown on the ruler, e.g. lyric lines.
    """
    if not midi:
        raise ValueError("at least one MIDI track is required")
    bpm, meter = midi[0].seq.layout.bpm, midi[0].seq.layout.meter
    lines: list[str] = [
        '<REAPER_PROJECT 0.1 "7.0/drums2midi" 0',
        f"  TEMPO {_num(bpm)} {meter} 4",
    ]
    for i, (seconds, text) in enumerate(markers or [], start=1):
        lines.append(f"  MARKER {i} {_num(max(seconds, 0.0))} {_quote(text)} 0")
    lines += _audio_track(stem)
    for m in midi:
        lines += _midi_track(m)
    if original is not None:
        lines += _audio_track(original)
    lines.append(">")
    path.write_text("\n".join(lines) + "\n")


def _audio_track(t: AudioTrack) -> list[str]:
    guid = _guid()
    return [
        f"  <TRACK {guid}",
        f"    NAME {_quote(t.name)}",
        f"    VOLPAN {_num(_db_to_linear(t.volume_db))} {_num(t.pan)} -1 -1 1",
        f"    MUTESOLO {int(t.muted)} 0 0",
        f"    TRACKID {guid}",
        "    <ITEM",
        f"      POSITION {_num(t.position)}",
        f"      LENGTH {_num(t.duration)}",
        "      LOOP 0",
        f"      NAME {_quote(t.file)}",
        f"      IGUID {_guid()}",
        f"      GUID {_guid()}",
        "      <SOURCE WAVE",
        f"        FILE {_quote(t.file)}",
        "      >",
        "    >",
        "  >",
    ]


def _midi_track(m: MidiTrack) -> list[str]:
    guid = _guid()
    seq = m.seq
    bpm, meter = seq.layout.bpm, seq.layout.meter
    lines = [
        f"  <TRACK {guid}",
        f"    NAME {_quote(m.name)}",
        f"    VOLPAN 1 {_num(m.pan)} -1 -1 1",
        f"    MUTESOLO {int(m.muted)} 0 0",
        f"    TRACKID {guid}",
        "    <ITEM",
        "      POSITION 0",
        f"      LENGTH {_num(seq.tempo_map.seconds(seq.end_tick))}",
        "      LOOP 0",
        f"      NAME {_quote(m.item_name)}",
        f"      IGUID {_guid()}",
        f"      GUID {_guid()}",
        "      <SOURCE MIDI",
        f"        HASDATA 1 {PPQ} QN",
    ]
    cursor = 0
    for e in seq.events:
        lines.append(f"        E {e.tick - cursor} {e.status:02x} {e.data1:02x} {e.data2:02x}")
        cursor = e.tick
    # All-notes-off at the end marks the source length, as Reaper itself writes it.
    lines.append(f"        E {seq.end_tick - cursor} b0 7b 00")
    lines += [
        f"        IGNTEMPO 0 {_num(bpm)} {meter} 4",
        "      >",
        "    >",
        "  >",
    ]
    return lines


def _guid() -> str:
    return "{" + str(uuid.uuid4()).upper() + "}"


def _quote(s: str) -> str:
    return '"' + s.replace('"', "'") + '"'


def _num(x: float) -> str:
    return f"{x:.10g}"


def _db_to_linear(db: float) -> float:
    return 10 ** (db / 20)
