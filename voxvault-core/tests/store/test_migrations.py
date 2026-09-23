"""Tasks 1.3, 1.4 and 1.9: schema evolution, refusal and cross-process locking.

The two-process tests here launch real processes. A threaded imitation would
share one SQLite library instance and one process-wide lock table, which is
precisely the thing not being tested.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

import pytest
from conftest import result_of, spawn, wait_ready

from voxvault.errors import SchemaTooNewError, StorageError
from voxvault.store import SCHEMA_VERSION, TranscriptStore
from voxvault.store.connection import _read_version_without_writing
from voxvault.store.schema import MIGRATIONS


def build_v1_database(path: Path) -> None:
    """A database as the previous build would have left it: core tables, no index."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("BEGIN IMMEDIATE")
    for statement in MIGRATIONS[0][1]:
        conn.execute(statement)
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO schema_migrations(version, applied_at_ms, applied_by_pid)"
        " VALUES (1, 0, 0)"
    )
    conn.execute("COMMIT")
    conn.close()


def seed_v1_records(path: Path, directory: Path, *, filler: int = 0) -> None:
    """Records a previous build left behind, which no migration may lose.

    ``filler`` pads the meeting so that rebuilding the search index takes long
    enough for two processes starting together to genuinely overlap. Without
    it the migration finishes in microseconds and the race being tested would
    almost never happen.
    """
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO meetings(id, uid, title, started_at_ms, duration_ms, state,"
        " origin, directory, created_at_ms, updated_at_ms)"
        " VALUES (1, 'antiga', 'Reuniao antiga', 1000, 60000, 'gravada',"
        " 'gravada', ?, 0, 0)",
        (str(directory),),
    )
    conn.execute(
        "INSERT INTO revisions(id, uid, meeting_id, status, state, engine_id,"
        " started_at_ms) VALUES (1, 'rev-antiga', 1, 'publicada', 'completa',"
        " 'motor-antigo', 1000)"
    )
    conn.executemany(
        "INSERT INTO segments(revision_id, meeting_id, track, speaker, start_ms,"
        " end_ms, text) VALUES (1, 1, ?, ?, ?, ?, ?)",
        [
            ("mic", "eu", 0, 1000, "combinamos a reunião de quarta"),
            ("system", "outros", 1200, 2000, "perfeito, ate quarta"),
        ]
        + [
            ("mic", "eu", 3000 + i, 3001 + i, f"enchimento numero {i} da ata")
            for i in range(filler)
        ],
    )
    conn.execute("UPDATE meetings SET active_revision_id = 1 WHERE id = 1")
    conn.execute("COMMIT")
    conn.close()


def test_previous_version_is_migrated_without_losing_records(
    db_path: Path, tmp_path: Path
) -> None:
    build_v1_database(db_path)
    seed_v1_records(db_path, tmp_path / "antiga")

    with TranscriptStore(db_path) as store:
        assert store.applied_migrations == [2, 3]
        assert store.schema_version == SCHEMA_VERSION
        meeting = store.get_meeting("antiga")
        assert meeting is not None
        assert meeting.title == "Reuniao antiga"
        assert [e.text for e in store.timeline("antiga")] == [
            "combinamos a reunião de quarta",
            "perfeito, ate quarta",
        ]
        # The index a migration created must cover what already existed.
        assert [h.meeting_uid for h in store.search("reuniao")] == ["antiga"]
        assert store.integrity_check() == "ok"


def build_v2_database(path: Path, directory: Path) -> None:
    """A database as the build before notes would have left it.

    The records are seeded between the two migrations, not after both, so the
    search index is populated the way the v2 migration really populates it.
    Seeding afterwards would produce a v2 database no v2 build could have
    written, and the test would prove nothing about the real upgrade path.
    """
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("BEGIN IMMEDIATE")
    for statement in MIGRATIONS[0][1]:
        conn.execute(statement)
    conn.execute("PRAGMA user_version = 1")
    conn.execute("COMMIT")
    conn.close()

    seed_v1_records(path, directory)

    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    for statement in MIGRATIONS[1][1]:
        conn.execute(statement)
    conn.execute("PRAGMA user_version = 2")
    conn.executemany(
        "INSERT INTO schema_migrations(version, applied_at_ms, applied_by_pid)"
        " VALUES (?, 0, 0)",
        [(1,), (2,)],
    )
    conn.execute("COMMIT")
    conn.close()


def test_a_v2_database_gains_notes_without_losing_meetings_or_segments(
    db_path: Path, tmp_path: Path
) -> None:
    """Task 1.1: the notes migration only adds.

    Everything a v2 build could have recorded -- the meeting, both segments,
    the active revision and the search index over it -- is checked on the far
    side of the upgrade.
    """
    from voxvault.store import NoteAuthor

    build_v2_database(db_path, tmp_path / "antiga")

    with TranscriptStore(db_path) as store:
        assert store.applied_migrations == [3]
        assert store.schema_version == SCHEMA_VERSION

        meeting = store.get_meeting("antiga")
        assert meeting is not None
        assert meeting.title == "Reuniao antiga"
        assert meeting.active_revision_id == 1
        assert [e.text for e in store.timeline("antiga")] == [
            "combinamos a reunião de quarta",
            "perfeito, ate quarta",
        ]
        assert [h.meeting_uid for h in store.search("reuniao")] == ["antiga"]
        assert store.integrity_check() == "ok"

        # And the thing the migration was for now works on that same database.
        assert store.notes_of("antiga") == []
        note = store.create_note(
            "antiga",
            kind="resumo",
            content="ficou combinada a reuniao de quarta",
            author=NoteAuthor.user(),
        )
        assert [n.uid for n in store.notes_of("antiga")] == [note.uid]


def test_migration_is_recorded_once_per_version(db_path: Path) -> None:
    with TranscriptStore(db_path) as store:
        rows = store._conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert [r[0] for r in rows] == [1, 2, 3]


def test_future_version_is_refused_naming_both_versions(db_path: Path) -> None:
    with TranscriptStore(db_path):
        pass
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 7}")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    with pytest.raises(SchemaTooNewError) as caught:
        TranscriptStore(db_path)
    message = str(caught.value)
    assert str(SCHEMA_VERSION + 7) in message
    assert str(SCHEMA_VERSION) in message
    assert caught.value.found == SCHEMA_VERSION + 7
    assert caught.value.known == SCHEMA_VERSION

    # "and nothing is written": byte for byte.
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before


def test_two_processes_opening_a_stale_database_migrate_exactly_once(
    db_path: Path, tmp_path: Path
) -> None:
    build_v1_database(db_path)
    seed_v1_records(db_path, tmp_path / "antiga", filler=40_000)
    barrier = tmp_path / "go"

    processes = [spawn("open_and_report", str(db_path), str(barrier)) for _ in range(4)]
    for process in processes:
        wait_ready(process)
    barrier.write_text("go", encoding="utf-8")
    results = [result_of(process, timeout_s=120) for process in processes]

    assert len({r["pid"] for r in results}) == 4
    applied = sorted([r["applied"] for r in results])
    assert applied == [[], [], [], [2, 3]], (
        f"as migracoes nao foram aplicadas uma unica vez: {applied}"
    )
    assert {r["version"] for r in results} == {SCHEMA_VERSION}
    assert {r["meetings"] for r in results} == {1}

    with TranscriptStore(db_path) as store:
        versions = [
            r[0]
            for r in store._conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
    assert versions == [1, 2, 3], "uma migracao foi registrada duas vezes"


def test_waiting_for_another_process_migration_fails_after_the_deadline(
    db_path: Path, tmp_path: Path
) -> None:
    """The 30 second budget, exercised with a shorter one.

    Waiting the real thirty seconds would only prove that ``time.sleep``
    works; what matters is that the deadline is honoured and that the failure
    names the wait instead of letting the process run on an unknown schema.
    """
    build_v1_database(db_path)
    seed_v1_records(db_path, tmp_path / "antiga")

    holder = spawn("hold_raw_lock", str(db_path), "6")
    wait_ready(holder)
    try:
        started = time.perf_counter()
        with pytest.raises(StorageError) as caught:
            TranscriptStore(db_path, migration_timeout_s=1.0)
        elapsed = time.perf_counter() - started
        assert 0.8 <= elapsed < 5.0, f"nao respeitou o prazo: {elapsed:.2f}s"
        assert "migracao" in str(caught.value)
        assert "esquema indeterminado" in str(caught.value)
    finally:
        holder.kill()
        holder.wait(timeout=30)

    # The schema stayed where it was: nothing operated half-migrated. It is
    # read the way the next process to start reads it: a bare connection
    # opened this soon after the kill can still meet the dead holder's locks,
    # which Windows releases asynchronously, and fail with a disk I/O error
    # that the store's own reader recovers from.
    assert _read_version_without_writing(db_path) == 1


def test_default_migration_budget_is_thirty_seconds() -> None:
    from voxvault.store import DEFAULT_MIGRATION_TIMEOUT_S

    assert DEFAULT_MIGRATION_TIMEOUT_S == 30.0
