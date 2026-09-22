"""Task 1.1: a new database is created with every expected table."""

from __future__ import annotations

from pathlib import Path

from conftest import make_meeting

from voxvault.store import SCHEMA_VERSION, Origin, TranscriptStore
from voxvault.store.schema import EXPECTED_TABLES


def columns(store: TranscriptStore, table: str) -> set[str]:
    return {row[1] for row in store._conn.execute(f"PRAGMA table_info({table})")}


def test_new_database_has_every_expected_table(store: TranscriptStore) -> None:
    assert EXPECTED_TABLES <= store.tables()


def test_new_database_is_at_the_current_schema_version(store: TranscriptStore) -> None:
    assert store.schema_version == SCHEMA_VERSION
    assert store.applied_migrations == [1, 2]


def test_reopening_applies_nothing(db_path: Path, store: TranscriptStore) -> None:
    with TranscriptStore(db_path) as reopened:
        assert reopened.applied_migrations == []
        assert reopened.schema_version == SCHEMA_VERSION


def test_meeting_table_carries_state_origin_and_directory(
    store: TranscriptStore,
) -> None:
    present = columns(store, "meetings")
    assert {
        "uid",
        "title",
        "started_at_ms",
        "ended_at_ms",
        "duration_ms",
        "state",
        "attempt_state",
        "transcript_state",
        "origin",
        "directory",
        "active_revision_id",
        "exports_revision_uid",
    } <= present


def test_segment_table_carries_track_speaker_offsets_and_text(
    store: TranscriptStore,
) -> None:
    present = columns(store, "segments")
    assert {
        "revision_id",
        "meeting_id",
        "track",
        "speaker",
        "start_ms",
        "end_ms",
        "text",
    } <= present


def test_revision_table_carries_engine_configuration_and_vocabulary(
    store: TranscriptStore,
) -> None:
    present = columns(store, "revisions")
    assert {
        "uid",
        "meeting_id",
        "status",
        "state",
        "engine_id",
        "config_fingerprint",
        "vocabulary",
        "tracks_ok",
        "tracks_failed",
        "completed_at_ms",
    } <= present


def test_write_ahead_logging_is_on(store: TranscriptStore) -> None:
    """The pragma only says what was asked for.

    That readers really do not block is proved in ``test_concurrency`` with two
    real processes; this is the cheap precondition.
    """
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_foreign_keys_are_enforced(store: TranscriptStore) -> None:
    assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_ordering_index_covers_the_timeline_order(store: TranscriptStore) -> None:
    """The timeline must be an ordered index scan, never a sort.

    A sort on a four-hour meeting is exactly the kind of cost that made the
    tool this one replaces unusable.
    """
    plan = store._conn.execute(
        "EXPLAIN QUERY PLAN SELECT id FROM segments WHERE revision_id = 1 "
        "ORDER BY start_ms, track, id"
    ).fetchall()
    text = " ".join(str(row[3]) for row in plan)
    assert "idx_segments_order" in text
    assert "USE TEMP B-TREE" not in text.upper()


def test_integrity_check_passes(store: TranscriptStore, tmp_path: Path) -> None:
    make_meeting(store, tmp_path)
    assert store.integrity_check() == "ok"


def test_imported_origin_is_accepted_and_recorded(
    store: TranscriptStore, tmp_path: Path
) -> None:
    meeting = make_meeting(
        store, tmp_path, uid="importada-1", origin=Origin.IMPORTED
    )
    assert meeting.origin is Origin.IMPORTED
