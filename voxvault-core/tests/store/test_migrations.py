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
from conftest import make_meeting, read_json_line, result_of, spawn, wait_ready

from voxvault.errors import SchemaTooNewError, StorageError
from voxvault.store import SCHEMA_VERSION, TranscriptStore
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


def seed_v1_records(path: Path, directory: Path) -> None:
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
        assert store.applied_migrations == [2]
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


def test_migration_is_recorded_once_per_version(db_path: Path) -> None:
    with TranscriptStore(db_path) as store:
        rows = store._conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert [r[0] for r in rows] == [1, 2]


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
    seed_v1_records(db_path, tmp_path / "antiga")
    barrier = tmp_path / "go"

    first = spawn("open_and_report", str(db_path), str(barrier))
    second = spawn("open_and_report", str(db_path), str(barrier))
    wait_ready(first)
    wait_ready(second)
    barrier.write_text("go", encoding="utf-8")

    left = result_of(first)
    right = result_of(second)

    assert left["pid"] != right["pid"]
    applied = sorted([left["applied"], right["applied"]])
    assert applied == [[], [2]], f"as migracoes nao foram aplicadas uma unica vez: {applied}"
    assert left["version"] == right["version"] == SCHEMA_VERSION
    assert left["meetings"] == right["meetings"] == 1

    with TranscriptStore(db_path) as store:
        versions = [
            r[0]
            for r in store._conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
    assert versions == [1, 2], "uma migracao foi registrada duas vezes"


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

    # The schema stayed where it was: nothing operated half-migrated.
    conn = sqlite3.connect(db_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()


def test_default_migration_budget_is_thirty_seconds() -> None:
    from voxvault.store import DEFAULT_MIGRATION_TIMEOUT_S

    assert DEFAULT_MIGRATION_TIMEOUT_S == 30.0
