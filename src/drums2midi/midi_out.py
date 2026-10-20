from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mido

from drums2midi.simplify import Note
from drums2midi.tempo import Beats

PPQ = 480
NOTE_LENGTH_TICKS = PPQ // 8  # 1/32 note
DRUM_CHANNEL = 9


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


def write_midi(
    path: Path,
    notes: list[Note],
    beats: Beats,
    *,
    count_in: CountIn | None = None,
    tempo_map: TempoMap | None = None,
    track_name: str = "Drums",
) -> Layout:
    layout = plan_layout(beats, count_in)
    tempo_map = tempo_map or TempoMap.constant(beats.bpm)

    # (tick, order, message): order makes note_offs precede note_ons at the same tick.
    events: list[tuple[int, int, mido.Message]] = []

    def add_note(seconds: float, note: int, velocity: int) -> None:
        on = tempo_map.tick(seconds)
        events.append(
            (on, 1, mido.Message("note_on", channel=DRUM_CHANNEL, note=note, velocity=velocity))
        )
        events.append(
            (
                on + NOTE_LENGTH_TICKS,
                0,
                mido.Message("note_off", channel=DRUM_CHANNEL, note=note, velocity=0),
            )
        )

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

    events.sort(key=lambda e: (e[0], e[1]))

    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name=track_name, time=0))
    track.append(mido.MetaMessage("time_signature", numerator=beats.meter, denominator=4, time=0))
    for tick, tempo in tempo_map.tempo_events():
        events.append((tick, -1, mido.MetaMessage("set_tempo", tempo=tempo)))
    events.sort(key=lambda e: (e[0], e[1]))

    cursor = 0
    for tick, _, msg in events:
        msg.time = tick - cursor
        cursor = tick
        track.append(msg)
    track.append(mido.MetaMessage("end_of_track", time=0))

    midi = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    midi.tracks.append(track)
    midi.save(path)
    return layout
