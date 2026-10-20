from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import mido

from backline.simplify import Note
from backline.tempo import Beats

PPQ = 480
NOTE_LENGTH_TICKS = PPQ // 8  # 1/32 note
DRUM_CHANNEL = 9
NOTE_ON = 0x90 | DRUM_CHANNEL
NOTE_OFF = 0x80 | DRUM_CHANNEL


@dataclass
class CountIn:
    bars: int = 2
    note: int = 37  # GM side stick
    accent_velocity: int = 110
    velocity: int = 85


@dataclass
class TempoMap:
    """Piecewise-constant tempo. Each segment: (start_sec, start_tick, seconds_per_beat)."""

    segments: list[tuple[float, int, float]]

    @classmethod
    def constant(cls, bpm: float) -> TempoMap:
        return cls([(0.0, 0, 60.0 / bpm)])

    def tick(self, seconds: float) -> int:
        for start_sec, start_tick, spb in reversed(self.segments):
            if seconds >= start_sec:
                return start_tick + round((seconds - start_sec) / spb * PPQ)
        start_sec, start_tick, spb = self.segments[0]
        return max(0, start_tick + round((seconds - start_sec) / spb * PPQ))

    def seconds(self, tick: int) -> float:
        for start_sec, start_tick, spb in reversed(self.segments):
            if tick >= start_tick:
                return start_sec + (tick - start_tick) / PPQ * spb
        start_sec, start_tick, spb = self.segments[0]
        return start_sec + (tick - start_tick) / PPQ * spb

    def tempo_events(self) -> list[tuple[int, int]]:
        """(tick, microseconds per beat) for every segment."""
        return [(start_tick, round(spb * 1_000_000)) for _, start_tick, spb in self.segments]


@dataclass
class Layout:
    """How song time maps onto the output timeline."""

    shift: float  # seconds added to every song note (and to the audio in the DAW)
    count_in_start: float  # output-time second where the count-in begins
    count_in_seconds: float
    first_downbeat: float  # output-time second of the song's first downbeat
    bpm: float
    meter: int


@dataclass
class Event:
    """A channel MIDI event at an absolute tick."""

    tick: int
    status: int
    data1: int
    data2: int


@dataclass
class Sequence:
    events: list[Event]  # sorted by tick; note-offs precede note-ons at equal ticks
    end_tick: int  # end of the last bar containing an event
    layout: Layout
    tempo_map: TempoMap


def plan_layout(beats: Beats, count_in: CountIn | None) -> Layout:
    """Place the count-in so its last click is one beat before the song's first downbeat."""
    spb = 60.0 / beats.bpm
    count_in_seconds = count_in.bars * beats.meter * spb if count_in else 0.0
    shift = max(0.0, count_in_seconds - beats.first_downbeat)
    first_downbeat = beats.first_downbeat + shift
    return Layout(
        shift=round(shift, 6),
        count_in_start=round(first_downbeat - count_in_seconds, 6),
        count_in_seconds=round(count_in_seconds, 6),
        first_downbeat=round(first_downbeat, 6),
        bpm=beats.bpm,
        meter=beats.meter,
    )


def build_sequence(
    notes: list[Note],
    beats: Beats,
    *,
    count_in: CountIn | None = None,
    tempo_map: TempoMap | None = None,
) -> Sequence:
    layout = plan_layout(beats, count_in)
    tempo_map = tempo_map or TempoMap.constant(beats.bpm)
    events: list[Event] = []

    def add_note(seconds: float, note: int, velocity: int) -> None:
        on = tempo_map.tick(seconds)
        events.append(Event(on, NOTE_ON, note, velocity))
        events.append(Event(on + NOTE_LENGTH_TICKS, NOTE_OFF, note, 0))

    if count_in:
        spb = 60.0 / beats.bpm
        for k in range(count_in.bars * beats.meter):
            accent = k % beats.meter == 0
            add_note(
                layout.count_in_start + k * spb,
                count_in.note,
                count_in.accent_velocity if accent else count_in.velocity,
            )

    for n in notes:
        add_note(n.time + layout.shift, n.note, n.velocity)

    events.sort(key=lambda e: (e.tick, e.status == NOTE_ON))

    bar_ticks = PPQ * beats.meter
    last = events[-1].tick if events else 0
    end_tick = (math.floor(last / bar_ticks) + 1) * bar_ticks
    return Sequence(events=events, end_tick=end_tick, layout=layout, tempo_map=tempo_map)


def write_midi(
    path: Path,
    seq: Sequence,
    *,
    track_name: str = "Drums",
    lyrics: list[tuple[float, str]] | None = None,
) -> None:
    """Write the sequence; `lyrics` are (output-time seconds, text) pairs for FF 05 events."""
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=track_name, time=0))
    track.append(
        mido.MetaMessage("time_signature", numerator=seq.layout.meter, denominator=4, time=0)
    )

    timed: list[tuple[int, int, mido.Message | mido.MetaMessage]] = []
    for tick, tempo in seq.tempo_map.tempo_events():
        timed.append((tick, 0, mido.MetaMessage("set_tempo", tempo=tempo)))
    for seconds, text in lyrics or []:
        timed.append((seq.tempo_map.tick(seconds), 0, mido.MetaMessage("lyrics", text=text)))
    for i, e in enumerate(seq.events):
        kind = "note_on" if e.status == NOTE_ON else "note_off"
        msg = mido.Message(kind, channel=DRUM_CHANNEL, note=e.data1, velocity=e.data2)
        timed.append((e.tick, 1 + i, msg))
    timed.sort(key=lambda t: (t[0], t[1]))

    cursor = 0
    for tick, _, msg in timed:
        msg.time = tick - cursor
        cursor = tick
        track.append(msg)
    track.append(mido.MetaMessage("end_of_track", time=max(seq.end_tick - cursor, 0)))

    midi = mido.MidiFile(type=1, ticks_per_beat=PPQ, charset="utf-8")
    midi.tracks.append(track)
    midi.save(path)
