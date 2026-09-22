"""Merging the two tracks into one timeline.

The merge itself is done by the database: the ordering index is
``(revision_id, start_ms, track, id)``, so a page of the timeline is an
ordered index scan rather than a sort over a whole meeting. What is left here
is the part that is not a query -- overlap detection and rendering.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from ..types import TimelineEntry


#: Sort key of the timeline, stated once so that the Python side and the SQL
#: side cannot drift apart. Ordering by start instant alone is not enough:
#: both tracks can produce a segment beginning in the same millisecond, and an
#: unstable order would break the cursor pagination built on top of it.
def order_key(entry: TimelineEntry) -> tuple[int, str, int]:
    return (entry.start_ms, str(entry.track), entry.segment_id)


def merge(entries: Iterable[TimelineEntry]) -> list[TimelineEntry]:
    """Interleave segments from any number of tracks into one timeline."""
    return sorted(entries, key=order_key)


def find_overlaps(entries: Iterable[TimelineEntry]) -> list[tuple[int, int]]:
    """Segment id pairs from different tracks that overlap in time.

    Both segments are preserved whole; the overlap is reported, never
    resolved. An interruption is the most informative moment of a meeting, and
    dropping one side of it would throw that away.

    Linear in the number of segments: the input is already ordered by start
    instant, so only the still-open segments need to be compared against.
    """
    ordered = sorted(entries, key=order_key)
    open_entries: list[TimelineEntry] = []
    pairs: list[tuple[int, int]] = []
    for entry in ordered:
        open_entries = [e for e in open_entries if e.end_ms > entry.start_ms]
        for other in open_entries:
            if other.track != entry.track:
                pairs.append(
                    (min(other.segment_id, entry.segment_id),
                     max(other.segment_id, entry.segment_id))
                )
        open_entries.append(entry)
    return pairs


def overlapping_ids(entries: Iterable[TimelineEntry]) -> set[int]:
    """Ids of segments that overlap a segment of the other track.

    ``TimelineEntry`` cannot carry the flag itself -- it is a frozen slots
    dataclass defined in ``types.py``, which this layer does not own -- so the
    signal travels beside the timeline instead of inside it.
    """
    flagged: set[int] = set()
    for left, right in find_overlaps(entries):
        flagged.add(left)
        flagged.add(right)
    return flagged


def format_offset(ms: int) -> str:
    """``hh:mm:ss.mmm`` relative to the start of the meeting."""
    negative = ms < 0
    ms = abs(int(ms))
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    text = f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    return f"-{text}" if negative else text


def format_duration(ms: int) -> str:
    """``hh:mm:ss`` for durations shown to a reader."""
    ms = max(0, int(ms))
    hours, rest = divmod(ms // 1000, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def iter_speaker_turns(
    entries: Iterable[TimelineEntry],
) -> Iterator[tuple[str, list[TimelineEntry]]]:
    """Group consecutive entries by speaker, for readable output."""
    current: list[TimelineEntry] = []
    speaker: str | None = None
    for entry in entries:
        if speaker is not None and str(entry.speaker) != speaker:
            yield speaker, current
            current = []
        speaker = str(entry.speaker)
        current.append(entry)
    if speaker is not None and current:
        yield speaker, current
