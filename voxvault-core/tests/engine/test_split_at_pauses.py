"""A decoded segment split at the long pauses between its words.

The timings are the ones measured on a real meeting, where voice activity
detection put two utterances minutes apart side by side and the model gave
them back as one line; the words are stand-ins.
"""

from __future__ import annotations

from types import SimpleNamespace

from voxvault.engine.local_whisper import split_at_pauses


def word(text: str, start: float, end: float) -> SimpleNamespace:
    return SimpleNamespace(word=text, start=start, end=end)


def segment(*words: SimpleNamespace, text: str = "") -> SimpleNamespace:
    start = words[0].start if words else 0.0
    end = words[-1].end if words else 0.0
    return SimpleNamespace(
        start=start, end=end, words=list(words),
        text=text or "".join(w.word for w in words),
    )


def test_an_utterance_minutes_later_becomes_its_own_segment() -> None:
    """The line found: one word at 16.6 s, four at 333 s, shown as 318 s."""
    item = segment(
        word(" um", 16.6, 17.0),
        word(" dois", 332.8, 333.3),
        word(" tres", 333.3, 333.6),
        word(" quatro", 333.6, 333.7),
        word(" cinco", 333.7, 334.0),
    )

    assert split_at_pauses(item) == [
        (16_600, 17_000, "um"),
        (332_800, 334_000, "dois tres quatro cinco"),
    ]


def test_the_pauses_within_a_sentence_keep_it_whole() -> None:
    item = segment(
        word(" bom", 10.0, 10.3),
        word(" dia", 10.3, 10.6),
        word(" a", 11.9, 12.0),  # 1.3 s: a breath, not another utterance
        word(" todos", 12.0, 12.4),
    )

    assert split_at_pauses(item) == [(10_000, 12_400, "bom dia a todos")]


def test_the_pause_that_splits_is_the_one_longer_than_the_limit() -> None:
    item = segment(word(" a", 0.0, 0.5), word(" b", 2.5, 3.0), word(" c", 5.1, 5.5))

    assert split_at_pauses(item, max_gap_s=2.0) == [
        (0, 3_000, "a b"),
        (5_100, 5_500, "c"),
    ]


def test_a_segment_without_words_keeps_its_own_bounds() -> None:
    item = SimpleNamespace(start=1.25, end=2.5, words=None, text=" algo dito ")

    assert split_at_pauses(item) == [(1_250, 2_500, "algo dito")]


def test_nothing_is_made_of_empty_text() -> None:
    assert split_at_pauses(SimpleNamespace(start=0.0, end=1.0, words=[], text="  ")) == []
    assert split_at_pauses(segment(word(" ", 0.0, 0.1), word("", 0.1, 0.2), text=" ")) == []
