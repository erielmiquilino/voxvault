"""Tasks 1.2, 1.7 and 1.8: concurrent access, contention, and the capture path.

Every test in this file uses real processes or a real worker thread against a
real database file. Concurrency verified with mocks proves nothing about the
one writer SQLite actually admits.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import make_meeting, read_json_line, result_of, spawn, transcribe, wait_ready

from voxvault.errors import StorageBusyError, StorageError
from voxvault.store import (
    AsyncWriter,
    Origin,
    TranscriptStore,
    capture_execution_path,
    in_capture_path,
)
from voxvault.types import EngineInfo, Segment, Track


# -- readers are never blocked by a writer -------------------------------


def test_a_reader_is_not_blocked_by_a_writer_in_another_process(
    db_path: Path, tmp_path: Path, engine: EngineInfo
) -> None:
    """Task 1.2, with two real processes.

    One writes a long meeting's worth of segments and keeps the transaction
    open for two seconds. The other reads throughout. The reads must finish
    immediately and must not see anything the writer has not committed.

    The size of that write is the whole point. A small uncommitted write holds
    only a RESERVED lock, which never blocks a reader under any journal mode,
    so a small writer here would pass with write-ahead logging turned off and
    prove nothing. This one spills the page cache and escalates to EXCLUSIVE:
    measured on this machine, a rollback journal lets the reader through once
    in 1.5 seconds and makes that single read take 3.05 s, while the
    write-ahead log serves 228,000 reads with a worst case of 41 ms.
    """
    with TranscriptStore(db_path) as store:
        make_meeting(store, tmp_path, uid="reuniao-base")
        transcribe(
            store,
            "reuniao-base",
            engine,
            mic=[Segment(0, 1000, "linha do micro")],
            system=[Segment(0, 1000, "linha do sistema")],
        )

    # The reader opens first, so that a blocked open cannot masquerade as a
    # fast read afterwards. Its timed loop begins only once the writer is
    # holding the database.
    barrier = tmp_path / "ler"
    reader = spawn("read_under_write", str(db_path), "1.5", str(barrier))
    wait_ready(reader)
    writer = spawn("hold_big_write", str(db_path), "3")
    wait_ready(writer, timeout_s=120)
    barrier.write_text("ok", encoding="utf-8")

    reading = result_of(reader)
    result_of(writer, timeout_s=120)

    assert reading["reads"] > 100, "as leituras ficaram bloqueadas"
    assert reading["slowest_s"] < 0.5, (
        f"uma leitura levou {reading['slowest_s']:.2f}s durante uma escrita"
    )
    assert reading["saw_uncommitted"] is False


# -- several writers -----------------------------------------------------


def test_three_concurrent_writers_all_complete_without_loss(
    db_path: Path, tmp_path: Path
) -> None:
    """Task 1.7. Three processes, each writing whole meetings at once."""
    with TranscriptStore(db_path):
        pass
    barrier = tmp_path / "go"

    processes = [
        spawn("write_meetings", str(db_path), f"escritor{index}", "8", str(barrier))
        for index in range(3)
    ]
    for process in processes:
        wait_ready(process)
    barrier.write_text("go", encoding="utf-8")
    results = [result_of(process, timeout_s=120) for process in processes]

    for result in results:
        assert result["busy"] == [], f"escritor excedeu o prazo: {result['busy']}"
        assert result["errors"] == [], f"escritor falhou: {result['errors']}"
        assert len(result["written"]) == 8
        assert result["slowest_s"] < 5.0, (
            f"uma reuniao levou {result['slowest_s']:.2f}s, acima do prazo de contencao"
        )

    with TranscriptStore(db_path) as store:
        meetings = {m.uid for m in store.list_meetings()}
        expected = {f"escritor{i}-{j}" for i in range(3) for j in range(8)}
        assert expected <= meetings, "escritas se perderam"
        for uid in sorted(expected):
            assert len(store.timeline(uid)) == 2
        assert store.integrity_check() == "ok"
        assert store.orphan_index_rows() == []


# -- contention beyond the budget ---------------------------------------


def test_the_default_contention_budget_is_five_seconds() -> None:
    from voxvault.store import DEFAULT_BUSY_TIMEOUT_S

    assert DEFAULT_BUSY_TIMEOUT_S == 5.0


@pytest.mark.slow
def test_contention_beyond_the_budget_fails_explicitly_and_writes_nothing(
    db_path: Path, tmp_path: Path
) -> None:
    """Task 1.7, at the real five second budget."""
    with TranscriptStore(db_path):
        pass

    store = TranscriptStore(db_path)
    holder = spawn("hold_write_lock", str(db_path), "20")
    wait_ready(holder)
    try:
        started = time.perf_counter()
        with pytest.raises(StorageBusyError) as caught:
            store.create_meeting(
                uid="perdida",
                title="Nao deve existir",
                started_at=datetime.now(timezone.utc),
                directory=tmp_path / "perdida",
            )
        elapsed = time.perf_counter() - started
        assert 4.5 <= elapsed < 12.0, f"o prazo nao foi respeitado: {elapsed:.2f}s"
        assert "ocupado" in str(caught.value)
        assert "nada foi gravado" in str(caught.value)
    finally:
        holder.kill()
        holder.wait(timeout=30)

    with TranscriptStore(db_path) as reopened:
        assert reopened.get_meeting("perdida") is None
        assert reopened.get_meeting("bloqueador") is None
        assert reopened.integrity_check() == "ok"
    store.close()


def test_a_failed_write_leaves_no_partial_segments(
    db_path: Path, tmp_path: Path, engine: EngineInfo
) -> None:
    with TranscriptStore(db_path) as store:
        make_meeting(store, tmp_path, uid="reuniao-1")
        revision = store.begin_revision("reuniao-1", engine=engine)
        revision_id = revision.id

    store = TranscriptStore(db_path, busy_timeout_s=1.0)
    holder = spawn("hold_write_lock", str(db_path), "6")
    wait_ready(holder)
    try:
        with pytest.raises(StorageBusyError):
            store.add_segments(
                revision_id,
                Track.MIC,
                [Segment(i * 10, i * 10 + 5, f"trecho {i}") for i in range(500)],
            )
        store.close()
    finally:
        holder.kill()
        holder.wait(timeout=30)

    with TranscriptStore(db_path) as reopened:
        remaining = reopened._conn.execute(
            "SELECT count(*) FROM segments WHERE revision_id = ?", (revision_id,)
        ).fetchone()[0]
        assert remaining == 0


# -- an interrupted write -----------------------------------------------


def test_killing_a_process_mid_write_leaves_the_store_consistent(
    db_path: Path, tmp_path: Path, engine: EngineInfo
) -> None:
    with TranscriptStore(db_path) as store:
        make_meeting(store, tmp_path, uid="reuniao-1")
        transcribe(
            store,
            "reuniao-1",
            engine,
            mic=[Segment(0, 1000, "texto ja publicado")],
            system=[Segment(0, 1000, "eco ja publicado")],
        )

    victim = spawn("die_mid_write", str(db_path), "reuniao-1", "2000")
    started = read_json_line(victim)
    wait_ready(victim)
    victim.kill()
    victim.wait(timeout=30)

    with TranscriptStore(db_path) as store:
        assert store.integrity_check() == "ok"
        revision = store.revision_by_uid(started["revision_uid"])
        written = store._conn.execute(
            "SELECT count(*) FROM segments WHERE revision_id = ?", (revision.id,)
        ).fetchone()[0]
        assert written == 0, "sobraram segmentos de uma revisao parcialmente gravada"
        assert [e.text for e in store.timeline("reuniao-1")] == [
            "texto ja publicado",
            "eco ja publicado",
        ]


# -- the capture path never waits ---------------------------------------


def test_a_synchronous_write_from_the_capture_path_is_refused(
    store: TranscriptStore, tmp_path: Path
) -> None:
    """Task 1.8, enforced instead of documented.

    A blocking write introduced on the capture path fails here, in a test,
    rather than silently costing audio during a real meeting.
    """
    assert in_capture_path() is False
    with capture_execution_path():
        assert in_capture_path() is True
        with pytest.raises(StorageError) as caught:
            store.create_meeting(
                uid="do-callback",
                title="Nunca",
                started_at=datetime.now(timezone.utc),
                directory=tmp_path / "do-callback",
            )
        assert "AsyncWriter" in str(caught.value)
    assert in_capture_path() is False
    assert store.get_meeting("do-callback") is None


def test_the_writer_accepts_work_from_the_capture_path(
    db_path: Path, tmp_path: Path
) -> None:
    with TranscriptStore(db_path):
        pass
    with AsyncWriter(db_path) as writer:
        with capture_execution_path():
            writer.submit(
                lambda store: store.create_meeting(
                    uid="da-captura",
                    title="Gravacao em andamento",
                    started_at=datetime.now(timezone.utc),
                    directory=tmp_path / "da-captura",
                    origin=Origin.RECORDED,
                ),
                label="criar sessao",
            )
        assert writer.flush(timeout_s=30)
        assert writer.failures == ()

    with TranscriptStore(db_path) as store:
        assert store.get_meeting("da-captura") is not None


@pytest.mark.slow
def test_prolonged_contention_during_a_recording_costs_no_audio(
    db_path: Path, tmp_path: Path
) -> None:
    """Task 1.8, measured.

    Another process holds the single write lock for two seconds. A capture
    loop runs throughout, at a fixed tick, submitting a write on every tick.
    What must hold: not one tick is dropped, and no tick waits anywhere near
    the contention budget -- because the capture path never touches SQLite.

    The fifty millisecond bound is four orders of magnitude away from what the
    alternative costs. Measured on this machine with the same loop: submitting
    through the writer, the worst tick took 0.2 ms; calling the same store
    method synchronously from the loop, it took 2082.6 ms -- two seconds of a
    meeting, gone, for one database dispute.
    """
    with TranscriptStore(db_path) as store:
        make_meeting(store, tmp_path, uid="gravando")

    tick_s = 0.01
    ticks = 300  # outlasts the two second lock even at Windows timer resolution
    captured: list[int] = []
    latencies: list[float] = []

    holder = spawn("hold_write_lock", str(db_path), "2")
    wait_ready(holder)
    blocked_seen = False
    try:
        with AsyncWriter(db_path) as writer:
            with capture_execution_path():
                next_tick = time.perf_counter()
                for index in range(ticks):
                    next_tick += tick_s
                    now = time.perf_counter()
                    if next_tick > now:
                        time.sleep(next_tick - now)
                    started = time.perf_counter()
                    # Standing in for the audio callback: stamp the block and
                    # hand the persistence off without touching the database.
                    captured.append(index)
                    writer.submit(
                        lambda store, i=index: store.record_progress(
                            "gravando", (i + 1) * 10
                        ),
                        label=f"bloco {index}",
                    )
                    latencies.append(time.perf_counter() - started)
                    if writer.pending > 1:
                        blocked_seen = True
            assert writer.flush(timeout_s=60)
    finally:
        holder.kill()
        holder.wait(timeout=30)

    assert len(captured) == ticks, "blocos de audio foram perdidos"
    assert max(latencies) < 0.05, (
        f"o caminho de captura esperou {max(latencies) * 1000:.1f} ms; "
        f"a persistencia nao esta desacoplada"
    )
    assert blocked_seen, "a disputa pelo banco nao chegou a ocorrer no teste"


def test_the_writer_records_a_failure_instead_of_killing_its_thread(
    db_path: Path, tmp_path: Path
) -> None:
    with TranscriptStore(db_path):
        pass
    with AsyncWriter(db_path) as writer:
        writer.submit(lambda store: store.rename_meeting("inexistente", "x"), label="falha")
        writer.submit(
            lambda store: store.create_meeting(
                uid="depois-da-falha",
                title="Segue viva",
                started_at=datetime.now(timezone.utc),
                directory=tmp_path / "depois",
            ),
            label="sucesso",
        )
        assert writer.flush(timeout_s=30)
        failures = writer.failures
        assert len(failures) == 1
        assert failures[0].label == "falha"
        assert writer.completed == 1

    with TranscriptStore(db_path) as store:
        assert store.get_meeting("depois-da-falha") is not None
