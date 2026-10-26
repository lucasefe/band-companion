from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from backline.midi_out import PPQ, Sequence

GENERATOR_TAG = "backline"
LEGACY_TAGS = ("drums2midi",)  # projects written before the rename are still ours
HEADER = f'<REAPER_PROJECT 0.1 "7.0/{GENERATOR_TAG}" 0'

# Mix defaults, taken from a project the user saved: the practice render is the MIDI drums
# (through the user's VSTi) in the centre with the original song quiet on the right.
STEM_VOLUME_DB = -9.0
ORIGINAL_VOLUME_DB = -16.0
ORIGINAL_PAN = 0.6

# Reaper's render-format blob for 24-bit WAV ("evaw" + 0x18 + 0x00 0x01).
RENDER_CFG_WAV24 = "ZXZhdxgAAQ=="

FX_TEMPLATE_PATH = Path.home() / ".config" / "backline" / "midi-track.RfxChain"


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
    fx_chain: str | None = None  # inner lines of an <FXCHAIN> block (Reaper .RfxChain format)


@dataclass
class RenderSettings:
    directory: str  # absolute; what File → Render pre-fills
    pattern: str  # file name without extension; Reaper wildcards allowed
    sample_rate: int = 44100
    cfg: str = RENDER_CFG_WAV24


def write_rpp(
    path: Path,
    *,
    stem: AudioTrack,
    midi: list[MidiTrack],
    original: AudioTrack | None = None,
    markers: list[tuple[float, str]] | None = None,
    render: RenderSettings | None = None,
) -> None:
    """Reaper project on the same timeline as drums.mid.

    `markers` are (output-time seconds, text) pairs shown on the ruler: the lyric lines. Once
    Reaper has saved the project they are the lyrics (see read_markers).
    """
    if not midi:
        raise ValueError("at least one MIDI track is required")
    bpm, meter = midi[0].seq.layout.bpm, midi[0].seq.layout.meter
    lines: list[str] = [HEADER, f"  TEMPO {_num(bpm)} {meter} 4"]
    if render is not None:
        lines += [
            f"  RENDER_FILE {_quote(render.directory)}",
            f"  RENDER_PATTERN {_quote(render.pattern)}",
            f"  RENDER_FMT 0 2 {render.sample_rate}",
            "  RENDER_RANGE 1 0 0 0 1000",  # entire project
            "  RENDER_ADDTOPROJ 0",
            "  <RENDER_CFG",
            f"    {render.cfg}",
            "  >",
        ]
    lines += marker_lines(markers or [])
    lines += _audio_track(stem)
    for m in midi:
        lines += _midi_track(m)
    if original is not None:
        lines += _audio_track(original)
    lines.append(">")
    path.write_text("\n".join(lines) + "\n")


# --- markers: the lyrics live in the project ---------------------------------------------

# `MARKER <n> <pos> <name> <flags> ...`; Reaper appends colour, a GUID and more when it saves.
# flags 1 = region (a second line with an empty name closes it), 0 = marker.
_MARKER = re.compile(
    r'^\s*MARKER\s+(\d+)\s+(-?[\d.]+(?:[eE][-+]?\d+)?)\s+("[^"]*"|\'[^\']*\'|`[^`]*`|\S+)\s+(\d+)'
)


def marker_lines(markers: list[tuple[float, str]]) -> list[str]:
    return [
        f"  MARKER {i} {_num(max(seconds, 0.0))} {_quote(text)} 0"
        for i, (seconds, text) in enumerate(markers, start=1)
    ]


def read_markers(rpp_text: str) -> list[tuple[float, str]]:
    """(seconds, text) of every plain marker, in time order; regions are not lyrics."""
    found: list[tuple[float, str]] = []
    for line in rpp_text.splitlines():
        m = _MARKER.match(line)
        if m and int(m.group(4)) & 1 == 0:
            found.append((float(m.group(2)), _unquote(m.group(3))))
    found.sort(key=lambda x: x[0])
    return found


def replace_markers(rpp_text: str, markers: list[tuple[float, str]]) -> str:
    """The same project with its plain markers swapped for `markers`; everything else, regions
    included, is left byte for byte. Reaper re-assigns GUIDs and colours when it next saves."""
    kept: list[str] = []
    insert_at: int | None = None
    for line in rpp_text.splitlines():
        m = _MARKER.match(line)
        if m and int(m.group(4)) & 1 == 0:
            if insert_at is None:
                insert_at = len(kept)
            continue
        kept.append(line)
    if insert_at is None:
        # No markers yet: before the first track, else before the closing bracket.
        insert_at = next(
            (i for i, ln in enumerate(kept) if ln.startswith("  <TRACK")), len(kept) - 1
        )
    kept[insert_at:insert_at] = marker_lines(markers)
    return "\n".join(kept) + "\n"


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'`":
        return token[1:-1]
    return token


def is_generated(path: Path) -> bool:
    """True if `path` is one of ours and has not been saved over by Reaper."""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            header = f.readline()
        return any(tag in header for tag in (GENERATOR_TAG, *LEGACY_TAGS))
    except OSError:
        return False


def extract_fx_chain(rpp_text: str, *, track_contains: str = "MIDI") -> str | None:
    """Inner lines of the <FXCHAIN> block of the first track whose NAME contains `track_contains`.

    Returns them in Reaper's .RfxChain form (no <FXCHAIN>/> wrapper, 4-space base indent).
    """
    for block in _track_blocks(rpp_text):
        m = re.search(r'^\s*NAME\s+"?(.*?)"?\s*$', block, re.M)
        if not m or track_contains.lower() not in m.group(1).lower():
            continue
        chain = _balanced_block(block, "<FXCHAIN")
        if chain:
            inner = chain.splitlines()[1:-1]
            return _reindent(inner, 4) + "\n"
    return None


def load_fx_template(path: Path = FX_TEMPLATE_PATH) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace").strip("\n")
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("<FXCHAIN"):
        lines = lines[1:]
        if lines and lines[-1].strip() == ">":
            lines = lines[:-1]
    return _reindent(lines, 4) + "\n" if lines else None


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
        f"      NAME {_quote(Path(t.file).name)}",
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
    ]
    if m.fx_chain:
        # Each track needs its own FX instance id.
        chain = re.sub(r"(FXID )\{[0-9A-Fa-f-]+\}", lambda mm: mm.group(1) + _guid(), m.fx_chain)
        lines += [
            "    <FXCHAIN",
            *_reindent(chain.rstrip("\n").splitlines(), 6).splitlines(),
            "    >",
        ]
    lines += [
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


# --- RPP text helpers ------------------------------------------------------------------


def _track_blocks(text: str) -> list[str]:
    blocks, pos = [], 0
    while True:
        start = text.find("\n  <TRACK", pos)
        if start < 0:
            return blocks
        block = _balanced_block(text[start + 1 :], "<TRACK")
        if not block:
            return blocks
        blocks.append(block)
        pos = start + 1 + len(block)


def _balanced_block(text: str, opener: str) -> str | None:
    """The text from the first line starting with `opener` to its matching `>` line."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith(opener):
            depth = 0
            for j in range(i, len(lines)):
                s = lines[j].strip()
                if s.startswith("<"):
                    depth += 1
                elif s == ">":
                    depth -= 1
                    if depth == 0:
                        return "\n".join(lines[i : j + 1])
            return None
    return None


def _reindent(lines: list[str], base: int) -> str:
    stripped = [ln for ln in lines if ln.strip()]
    if not stripped:
        return ""
    current = min(len(ln) - len(ln.lstrip()) for ln in stripped)
    return "\n".join(" " * base + ln[current:] if ln.strip() else "" for ln in lines)


def _guid() -> str:
    return "{" + str(uuid.uuid4()).upper() + "}"


def _quote(s: str) -> str:
    """Reaper's own rule: double quotes unless the text has them, then single, then backticks."""
    if '"' not in s:
        return f'"{s}"'
    if "'" not in s:
        return f"'{s}'"
    return "`" + s.replace("`", "'") + "`"


def _num(x: float) -> str:
    return f"{x:.10g}"


def _db_to_linear(db: float) -> float:
    return 10 ** (db / 20)
