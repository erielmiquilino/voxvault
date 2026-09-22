"""Tasks 1.9 and 6.1-6.2: one timeline out of two tracks.

Attribution here is physical, not inferred: the microphone is the person at
the machine and the system mix is everyone else. No individual identity is
derived for the other participants, and nothing in this layer tries.
"""

from __future__ import annotations

from pathlib import Path

from conftest import make_meeting, transcribe

from voxvault.store import TranscriptStore, find_overlaps, format_duration, format_offset
from voxvault.store import overlapping_ids
from voxvault.types import EngineInfo, Segment, Speaker, Track


def test_the_two_tracks_are_interleaved_by_start_instant(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 900, "bom dia"), Segment(4000, 4500, "certo")],
        system=[Segment(1000, 1800, "bom dia a voce"), Segment(6000, 6500, "ate logo")],
    )
    entries = store.timeline("reuniao-1")
    assert [e.text for e in entries] == [
        "bom dia",
        "bom dia a voce",
        "certo",
        "ate logo",
    ]
    assert [e.start_ms for e in entries] == sorted(e.start_ms for e in entries)


def test_each_segment_carries_the_attribution_of_its_track(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 900, "eu falei")],
        system=[Segment(1000, 1800, "outro falou")],
    )
    by_track = {str(e.track): e.speaker for e in store.timeline("reuniao-1")}
    assert by_track["mic"] == Speaker.ME
    assert by_track["system"] == Speaker.OTHERS


def test_simultaneous_speech_keeps_both_segments_and_flags_the_overlap(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(1000, 4000, "eu estava dizendo que")],
        system=[Segment(3000, 6000, "desculpa, posso interromper")],
    )
    entries = store.timeline("reuniao-1")
    assert len(entries) == 2
    assert [e.text for e in entries] == [
        "eu estava dizendo que",
        "desculpa, posso interromper",
    ]
    assert [(e.start_ms, e.end_ms) for e in entries] == [(1000, 4000), (3000, 6000)]

    pairs = find_overlaps(entries)
    assert len(pairs) == 1
    assert set(pairs[0]) == {e.segment_id for e in entries}


def test_segments_of_the_same_track_are_not_reported_as_overlapping(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    """Two adjacent phrases from one speaker are not an interruption."""
    make_meeting(store, tmp_path)
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 3000, "primeira parte"), Segment(2000, 5000, "segunda parte")],
        system=[Segment(9000, 9500, "longe dali")],
    )
    assert find_overlaps(store.timeline("reuniao-1")) == []


def test_overlap_detection_scales_to_a_long_meeting(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(i * 1000, i * 1000 + 800, f"micro {i}") for i in range(600)],
        system=[Segment(i * 1000 + 500, i * 1000 + 900, f"sistema {i}") for i in range(600)],
    )
    entries = store.timeline("reuniao-1")
    flagged = overlapping_ids(entries)
    assert len(entries) == 1200
    assert len(flagged) == 1200


def test_a_meeting_without_an_active_revision_has_an_empty_timeline(
    store: TranscriptStore, tmp_path: Path
) -> None:
    make_meeting(store, tmp_path, uid="sem-transcricao")
    assert store.timeline("sem-transcricao") == []


def test_offsets_are_rendered_relative_to_the_start_of_the_meeting() -> None:
    assert format_offset(0) == "00:00:00.000"
    assert format_offset(1234) == "00:00:01.234"
    assert format_offset(3_723_456) == "01:02:03.456"
    assert format_duration(3_723_456) == "01:02:03"
