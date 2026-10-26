"""Line timing from the recording itself: Whisper's word timestamps on the vocal stem, matched
to the lyrics' words. Used when a lookup's sync is for another edit of the song, or when the
lyrics are plain text."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from backline.lrclib import normalize

WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Alignment:
    times: list[float | None]  # one per line, song time; None = past the end of this recording
    matched: list[bool]  # whether the line's own words were heard (else placed by its neighbours)

    @property
    def coverage(self) -> float:
        return sum(self.matched) / len(self.matched) if self.matched else 0.0


def whisper_available() -> bool:
    try:
        import mlx_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def transcribe_words(
    audio: Path, cache: Path, *, model: str = WHISPER_MODEL, language: str | None = None
) -> list[Word]:
    """Words with timestamps; cached in `cache` (JSON) keyed on model and language."""
    if cache.exists():
        try:
            saved = json.loads(cache.read_text())
            if saved.get("model") == model and saved.get("language") == language:
                return [Word(*w) for w in saved["words"]]
        except (ValueError, TypeError, KeyError):
            pass
    import mlx_whisper  # heavy, Apple Silicon only

    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=model,
        word_timestamps=True,
        language=language,
        condition_on_previous_text=False,  # a repeated chorus must not be "remembered" wrong
        verbose=None,
    )
    words = [
        Word(float(w["start"]), float(w["end"]), str(w["word"]).strip())
        for seg in result.get("segments", [])
        for w in seg.get("words", [])
        if str(w["word"]).strip()
    ]
    cache.write_text(
        json.dumps(
            {
                "model": model,
                "language": language,
                "detected": result.get("language"),
                "words": [[w.start, w.end, w.text] for w in words],
            },
            ensure_ascii=False,
        )
    )
    return words


_TOKEN = re.compile(r"[a-z0-9']+")


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(normalize(text))


def realign(
    reference: list[float], lines: list[str], words: list[Word], duration: float | None = None
) -> Alignment:
    """Line times for this recording, given a sync made against another edit of the song.

    The edit changes where sections start, not how they are sung: the lines' relative timing
    is right and only the *offset* against our audio changes, a handful of times (a longer
    intro, a cut solo). Every place a line's words were heard implies an offset; a dynamic
    programme picks one offset per line, paying for each change, so a chorus sung four times
    goes to the right one of its four places and a line nobody heard rides on its neighbours.
    Lines that end up past `duration` (a longer fade-out in the other edit) get None.
    """
    n = len(lines)
    if n == 0 or not words:
        return Alignment(list(reference), [False] * n)
    heard = [tokens(w.text) for w in words]
    flat = [(t, j) for j, ts in enumerate(heard) for t in ts]
    flat_tokens = [t for t, _ in flat]

    candidates: list[list[tuple[float, float]]] = []  # per line: (offset, score)
    for i, line in enumerate(lines):
        mine = tokens(line)
        found: list[tuple[float, float]] = []
        if mine:
            width = len(mine) + 2
            for p in range(len(flat_tokens)):
                if flat_tokens[p] != mine[0]:
                    continue
                score = SequenceMatcher(None, mine, flat_tokens[p : p + width]).ratio()
                if score < MIN_LINE_MATCH:
                    continue
                offset = words[flat[p][1]].start - reference[i]
                if not any(abs(offset - o) < 0.3 for o, _ in found):
                    found.append((offset, score))
        candidates.append(found)

    # Viterbi over lines. The state is the offset the current section is anchored on; a match
    # within the timestamp jitter of it is free and nudges the anchor only a little (lines
    # with many near-identical candidates, "na na na", must not walk it away), a match further
    # out opens a new section and pays for it. What was heard is consumed in order: a later
    # line may only match later words, or every written chorus would buy the one that was heard.
    best: dict[float | None, _Path] = {None: _Path(0.0, [], -1.0)}
    for i in range(n):
        nxt: dict[float | None, _Path] = {}
        for state, prev in best.items():
            _keep(nxt, state, prev.extend(None, prev.heard_at))  # nobody heard this line
            for offset, score in candidates[i]:
                heard_at = reference[i] + offset
                if heard_at < prev.heard_at:
                    continue
                if state is None or abs(offset - state) > JITTER:
                    anchor, cost = offset, (0.0 if state is None else _jump_cost(offset - state))
                else:
                    anchor, cost = state + max(-DRIFT, min(DRIFT, offset - state)), 0.0
                _keep(nxt, anchor, prev.extend(offset, heard_at, score - cost))
        best = nxt
    path = max(best.values(), key=lambda v: v.total).offsets

    matched = [o is not None for o in path]
    first = next((o for o in path if o is not None), 0.0)
    offsets: list[float] = []
    current = first
    for o in path:
        if o is not None:
            current = o
        offsets.append(current)

    times: list[float | None] = []
    last = 0.0
    for i in range(n):
        t = max(reference[i] + offsets[i], last)
        if duration is not None and t > duration - 0.5:
            times.append(None)
            continue
        times.append(round(t, 2))
        last = t
    return Alignment(times, matched)


MIN_LINE_MATCH = 0.6
JITTER = 0.75  # Whisper's word timestamps wobble this much; not an edit
DRIFT = 0.15  # how far one within-jitter match may move the section's anchor


@dataclass
class _Path:
    total: float
    offsets: list[float | None]  # per line so far; None = not heard
    heard_at: float  # time of the last word matched, for monotonicity

    def extend(self, offset: float | None, heard_at: float, gain: float = 0.0) -> _Path:
        return _Path(self.total + gain, self.offsets + [offset], heard_at)


def _keep(table: dict[float | None, _Path], state: float | None, path: _Path) -> None:
    key = None if state is None else round(state, 2)
    if key not in table or path.total > table[key].total:
        table[key] = path


def _jump_cost(delta: float) -> float:
    return 2.0 + 0.02 * abs(delta)


def summary(alignment: Alignment, reference: list[float] | None) -> dict[str, Any]:
    info: dict[str, Any] = {
        "lines": len(alignment.times),
        "lines_heard": sum(alignment.matched),
        "coverage": round(alignment.coverage, 3),
    }
    info["lines_dropped"] = sum(t is None for t in alignment.times)
    if reference and alignment.times:
        shifts = [
            t - r
            for t, r, m in zip(alignment.times, reference, alignment.matched, strict=True)
            if m and t is not None
        ]
        if shifts:
            shifts.sort()
            info["shift_median"] = round(shifts[len(shifts) // 2], 2)
            info["shift_min"] = round(shifts[0], 2)
            info["shift_max"] = round(shifts[-1], 2)
    return info
