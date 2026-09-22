"""Child process used by the storage concurrency and migration tests.

Concurrency claims verified only with threads and mocks are worthless: the
things being proved here -- that a reader is not blocked by a writer in another
process, that two processes racing to migrate apply the migrations once, that
killing a process mid-write leaves nothing partial -- are properties of
separate OS processes sharing a file. So this is a real program, launched with
``sys.executable``.

Protocol: every command prints ``READY`` on a line of its own once it has
reached the interesting state, then prints one line of JSON as its result.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from voxvault.errors import StorageBusyError, StorageError
from voxvault.store import Origin, TranscriptStore, regenerate_exports
from voxvault.types import EngineInfo, Segment, Track

ENGINE = EngineInfo(
    name="faster-whisper",
    model="large-v3",
    compute_type="float16",
    device="cuda",
    version="1.1",
)


def _ready() -> None:
    print("READY", flush=True)


def _emit(payload: dict) -> None:
    print(json.dumps(payload), flush=True)


def _transcribe(store: TranscriptStore, uid: str, *, text: str) -> None:
    revision = store.begin_revision(uid, engine=ENGINE, language="pt")
    store.add_segments(revision.id, Track.MIC, [Segment(0, 1000, text)])
    store.add_segments(revision.id, Track.SYSTEM, [Segment(500, 1500, f"{text} eco")])
    store.publish_revision(revision.id, tracks_ok=[Track.MIC, Track.SYSTEM])


# -- commands ------------------------------------------------------------


def hold_write_lock(db: str, seconds: str) -> None:
    """Hold the single write lock without doing anything slow inside it.

    Exactly the situation the contention budget exists for.
    """
    store = TranscriptStore(db)
    store._conn.execute("BEGIN IMMEDIATE")
    store._conn.execute(
        "INSERT INTO meetings(uid, title, started_at_ms, state, origin, directory,"
        " created_at_ms, updated_at_ms) VALUES ('bloqueador', 'bloqueador', 0,"
        " 'gravando', 'gravada', '.', 0, 0)"
    )
    _ready()
    time.sleep(float(seconds))
    store._conn.execute("ROLLBACK")
    store.close()
    _emit({"held": float(seconds)})


def hold_big_write(db: str, seconds: str, rows: str = "100000") -> None:
    """Write a long meeting's worth of segments and hold the transaction open.

    The size matters. A small uncommitted write only takes a RESERVED lock,
    which does not block readers under any journal mode, so a small writer
    would prove nothing about write-ahead logging. A write that outgrows the
    page cache spills to disk and escalates to EXCLUSIVE -- and that is where a
    rollback journal stops every reader dead and the write-ahead log does not.
    """
    store = TranscriptStore(db)
    meeting = store.get_meeting("reuniao-base")
    revision_id = meeting.active_revision_id
    filler = "transcricao longa " * 12
    store._conn.execute("BEGIN IMMEDIATE")
    store._conn.execute(
        "INSERT INTO meetings(uid, title, started_at_ms, state, origin, directory,"
        " created_at_ms, updated_at_ms) VALUES ('bloqueador', 'bloqueador', 0,"
        " 'gravando', 'gravada', '.', 0, 0)"
    )
    store._conn.executemany(
        "INSERT INTO segments(revision_id, meeting_id, track, speaker, start_ms,"
        " end_ms, text) VALUES (?, ?, 'mic', 'eu', ?, ?, ?)",
        [
            (revision_id, meeting.id, 10_000 + i, 10_001 + i, f"{filler} {i}")
            for i in range(int(rows))
        ],
    )
    _ready()
    time.sleep(float(seconds))
    store._conn.execute("ROLLBACK")
    store.close()
    _emit({"rows": int(rows)})


def hold_raw_lock(db: str, seconds: str) -> None:
    """Hold the write lock on a database this process refuses to migrate."""
    conn = sqlite3.connect(db, timeout=60.0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("BEGIN IMMEDIATE")
    _ready()
    time.sleep(float(seconds))
    conn.execute("ROLLBACK")
    conn.close()
    _emit({"held": float(seconds)})


def open_and_report(db: str, barrier: str = "") -> None:
    """Open the store, reporting which migrations this process applied."""
    if barrier:
        _wait_for_barrier(Path(barrier))
    store = TranscriptStore(db)
    result = {
        "pid": os.getpid(),
        "applied": store.applied_migrations,
        "version": store.schema_version,
        "meetings": len(store.list_meetings()),
    }
    store.close()
    _emit(result)


def write_meetings(db: str, prefix: str, count: str, barrier: str = "") -> None:
    """Write whole meetings, each one a handful of short transactions."""
    if barrier:
        _wait_for_barrier(Path(barrier))
    store = TranscriptStore(db)
    written: list[str] = []
    busy: list[str] = []
    errors: list[str] = []
    slowest = 0.0
    for index in range(int(count)):
        uid = f"{prefix}-{index}"
        started = time.perf_counter()
        try:
            store.create_meeting(
                uid=uid,
                title=f"Reuniao {uid}",
                started_at=datetime.now(UTC),
                directory=str(Path(db).parent / uid),
                origin=Origin.RECORDED,
            )
            _transcribe(store, uid, text=f"nota de {uid}")
        except StorageBusyError as exc:
            busy.append(f"{uid}: {exc}")
        except StorageError as exc:
            errors.append(f"{uid}: {exc}")
        else:
            written.append(uid)
        slowest = max(slowest, time.perf_counter() - started)
    store.close()
    _emit(
        {
            "pid": os.getpid(),
            "written": written,
            "busy": busy,
            "errors": errors,
            "slowest_s": slowest,
        }
    )


def read_under_write(db: str, seconds: str, barrier: str) -> None:
    """Read repeatedly and report the slowest read and what was visible.

    The store is opened *before* signalling readiness and the timed loop only
    starts once the barrier appears. Otherwise a blocked open would absorb the
    contention and the reads that follow would look unblocked -- which is
    exactly how this test once passed with write-ahead logging turned off.
    """
    store = TranscriptStore(db)
    _ready()
    _await_file(Path(barrier))
    deadline = time.perf_counter() + float(seconds)
    slowest = 0.0
    reads = 0
    saw_uncommitted = False
    while time.perf_counter() < deadline:
        started = time.perf_counter()
        uids = {m.uid for m in store.list_meetings()}
        entries = store.timeline("reuniao-base")
        slowest = max(slowest, time.perf_counter() - started)
        reads += 1
        if "bloqueador" in uids:
            saw_uncommitted = True
        if len(entries) != 2:
            saw_uncommitted = True
    store.close()
    _emit({"reads": reads, "slowest_s": slowest, "saw_uncommitted": saw_uncommitted})


def die_mid_write(db: str, uid: str, count: str) -> None:
    """Open a write transaction, fill it, and wait to be killed."""
    store = TranscriptStore(db)
    revision = store.begin_revision(uid, engine=ENGINE, language="pt")
    store._conn.execute("BEGIN IMMEDIATE")
    store._conn.executemany(
        "INSERT INTO segments(revision_id, meeting_id, track, speaker, start_ms,"
        " end_ms, text) VALUES (?, ?, 'mic', 'eu', ?, ?, ?)",
        [
            (revision.id, revision.meeting_id, i * 10, i * 10 + 5, f"trecho {i}")
            for i in range(int(count))
        ],
    )
    print(json.dumps({"revision_uid": revision.uid}), flush=True)
    _ready()
    time.sleep(120)


def publish_then_die(db: str, uid: str) -> None:
    """Publish a revision and die before the exports are regenerated."""
    store = TranscriptStore(db)
    revision = store.begin_revision(
        uid,
        engine=EngineInfo(
            name="outro-motor",
            model="medium",
            compute_type="int8",
            device="cpu",
        ),
        language="pt",
    )
    store.add_segments(revision.id, Track.MIC, [Segment(0, 900, "texto novo do micro")])
    store.add_segments(
        revision.id, Track.SYSTEM, [Segment(1000, 1900, "texto novo do sistema")]
    )
    store.publish_revision(revision.id, tracks_ok=[Track.MIC, Track.SYSTEM])
    print(json.dumps({"revision_uid": revision.uid}), flush=True)
    # The exports are now stale on disk. Dying here is the scenario.
    sys.stdout.flush()
    os._exit(9)


def seed_and_export(db: str, uid: str) -> None:
    store = TranscriptStore(db)
    _transcribe(store, uid, text="texto original")
    status = regenerate_exports(store, uid)
    store.close()
    _emit({"revision_uid": status.exported_revision_uid})


def _wait_for_barrier(path: Path, timeout_s: float = 30.0) -> None:
    """Start together, so the race being tested actually happens."""
    _ready()
    _await_file(path, timeout_s)


def _await_file(path: Path, timeout_s: float = 60.0) -> None:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if path.exists():
            return
        time.sleep(0.005)
    raise TimeoutError(f"barreira {path} nao liberada")


COMMANDS = {
    "hold_write_lock": hold_write_lock,
    "hold_big_write": hold_big_write,
    "hold_raw_lock": hold_raw_lock,
    "open_and_report": open_and_report,
    "write_meetings": write_meetings,
    "read_under_write": read_under_write,
    "die_mid_write": die_mid_write,
    "publish_then_die": publish_then_die,
    "seed_and_export": seed_and_export,
}


if __name__ == "__main__":
    COMMANDS[sys.argv[1]](*sys.argv[2:])
