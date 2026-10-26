from __future__ import annotations

import json
from pathlib import Path

import pytest

from backline.align import Word, realign, summary, tokens, transcribe_words

VERSE = ["Walking like a man", "Hitting like a hammer", "Never was a quitter"]
CHORUS = ["She's got the look", "She's got the look", "What in the world can make her blue"]
NANA = ["Na-na-na-na na"] * 4


def _song(
    sections: list[tuple[float, list[str]]], gap: float = 2.0
) -> tuple[list[float], list[str]]:
    times, texts = [], []
    for start, lines in sections:
        for k, text in enumerate(lines):
            times.append(start + k * gap)
            texts.append(text)
    return times, texts


def _heard(times: list[float], texts: list[str], skip: set[int] = frozenset()) -> list[Word]:
    """What Whisper would return: each line's words spread over 1.5 s; some lines missed."""
    words = []
    for i, (t, text) in enumerate(zip(times, texts, strict=True)):
        if i in skip:
            continue
        toks = text.replace("-", " ").split()
        for k, w in enumerate(toks):
            words.append(Word(t + k * 1.5 / len(toks), t + (k + 1) * 1.5 / len(toks), w))
    return words


def test_tokens_normalise_case_accents_and_punctuation():
    # Contractions split like lrclib.normalize() does; both sides go through the same tokenizer.
    assert tokens("Na-na-na, she's got the LOOK!") == [
        "na",
        "na",
        "na",
        "she",
        "s",
        "got",
        "the",
        "look",
    ]
    assert tokens("Tú sólo en mis fotos estás") == ["tu", "solo", "en", "mis", "fotos", "estas"]


def test_realign_follows_the_edit_not_the_sync():
    # The recording: verse at 10 s, chorus at 20, solo, verse at 60, chorus at 70, na-na at 80.
    true_times, texts = _song([(10, VERSE), (20, CHORUS), (60, VERSE), (70, CHORUS), (80, NANA)])
    # The lookup was synced to an edit with a 25 s longer intro and a 5 s longer solo.
    sync = [t + 25 + (5 if t >= 60 else 0) for t in true_times]
    words = _heard(true_times, texts, skip={0, 1, 2, 13})  # verse 1 and one na-na unheard
    al = realign(sync, texts, words, duration=95.0)
    assert al.matched.count(True) == len(texts) - 4
    for got, want in zip(al.times, true_times, strict=True):
        assert got == pytest.approx(want, abs=0.3)
    info = summary(al, sync)
    assert info["shift_min"] == pytest.approx(-30, abs=0.3) and info["shift_max"] == pytest.approx(
        -25, abs=0.3
    )


def test_realign_never_reuses_what_was_heard():
    # Three written na-na blocks, Whisper heard only the middle one: the others ride on their
    # neighbours instead of piling onto the one block that was heard.
    true_times, texts = _song(
        [(10, CHORUS), (20, NANA), (30, CHORUS), (40, NANA), (50, CHORUS), (60, NANA)]
    )
    sync = [t + 20 for t in true_times]
    words = _heard(true_times, texts, skip={3, 4, 5, 6, 15, 16, 17, 18})
    al = realign(sync, texts, words, duration=80.0)
    for got, want in zip(al.times, true_times, strict=True):
        assert got == pytest.approx(want, abs=0.3)
    assert al.times == sorted(al.times)


def test_realign_drops_lines_past_the_end_and_keeps_order():
    true_times, texts = _song([(10, CHORUS), (20, NANA)])
    sync = [t + 5 for t in true_times]
    words = _heard(true_times, texts)
    al = realign(sync + [sync[-1] + 5], texts + ["She's got the look"], words, duration=30.0)
    assert al.times[-1] is None, "a fade-out line the other edit has and this one does not"
    assert all(t is not None for t in al.times[:-1])


def test_realign_without_anything_heard_keeps_the_sync():
    sync, texts = _song([(10, VERSE)])
    al = realign(sync, texts, [], duration=60.0)
    assert al.times == sync and not any(al.matched)


def test_transcribe_words_uses_the_cache(tmp_path: Path, monkeypatch):
    cache = tmp_path / "words.json"
    cache.write_text(json.dumps({"model": "m", "language": None, "words": [[1.0, 1.5, "hi"]]}))
    assert transcribe_words(tmp_path / "none.wav", cache, model="m") == [Word(1.0, 1.5, "hi")]
    # Another model: the cache does not apply, and we must not import whisper in tests.
    import sys

    monkeypatch.setitem(sys.modules, "mlx_whisper", None)
    with pytest.raises(ImportError):
        transcribe_words(tmp_path / "none.wav", cache, model="other")
