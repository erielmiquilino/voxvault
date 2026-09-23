"""The transcription queue.

Covers specs/transcription-pipeline/spec.md. The properties that matter here
are the ones that protect a recording: the queue must never run during one, a
failing track must not take the other down, and an interrupted attempt must
leave nothing half-written behind.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from voxvault.pipeline import (
    INTERRUPT_BUDGET_S,
    QueueRefused,
    TranscriptionPipeline,
    _Interrupted,
)
from voxvault.store import RevisionStatus
from voxvault.types import AttemptState, Segment

from .conftest import FakeWorker


@pytest.fixture
def pipeline(config, store):
    workers: list[FakeWorker] = []

    def factory(cfg, model):
        worker = FakeWorker(cfg, model)
        workers.append(worker)
        return worker

    handle = TranscriptionPipeline(config, store, worker_factory=factory)
    handle.workers = workers  # type: ignore[attr-defined]
    return handle


def _drain(pipeline, store, meeting_uid: str) -> None:
    """Run exactly one meeting through, synchronously.

    Releases the worker first, matching what the real loop does whenever the
    queue empties: it lets go of the GPU rather than sitting on it. Without
    that, a second attempt would silently reuse the first attempt's worker and
    a test that changes the engine's behaviour would be testing nothing.
    """
    pipeline._release_worker()
    # The loop only ever processes a meeting it can claim from the queue.
    store.set_attempt_state(meeting_uid, AttemptState.QUEUED)
    meeting = store.get_meeting(meeting_uid)
    pipeline._process(meeting)


# -- enqueueing --------------------------------------------------------

def test_enqueue_marks_the_meeting_as_waiting(pipeline, store, make_meeting) -> None:
    meeting = make_meeting()
    pipeline.enqueue(meeting.uid)
    assert str(store.get_meeting(meeting.uid).attempt_state) == AttemptState.QUEUED


def test_second_request_is_refused_not_queued(pipeline, store, make_meeting) -> None:
    """Scenario: Reprocessamento pedido duas vezes."""
    meeting = make_meeting()
    pipeline.enqueue(meeting.uid)

    with pytest.raises(QueueRefused, match="aguardando"):
        pipeline.enqueue(meeting.uid)

    assert len(pipeline.pending()) == 1


def test_request_while_running_is_refused_without_interrupting(
    pipeline, store, make_meeting
) -> None:
    meeting = make_meeting()
    store.set_attempt_state(meeting.uid, AttemptState.RUNNING)

    with pytest.raises(QueueRefused, match="sendo transcrita"):
        pipeline.enqueue(meeting.uid)

    assert str(store.get_meeting(meeting.uid).attempt_state) == AttemptState.RUNNING


def test_meeting_without_audio_cannot_be_reprocessed(
    pipeline, store, make_meeting
) -> None:
    """Scenario: Reprocessamento sem audio disponivel."""
    meeting = make_meeting()
    for leftover in Path(meeting.directory).glob("*.wav"):
        leftover.unlink()

    with pytest.raises(QueueRefused, match="nao tem mais audio"):
        pipeline.enqueue(meeting.uid)


# -- processing --------------------------------------------------------

def test_both_tracks_are_transcribed_and_published(
    pipeline, store, make_meeting
) -> None:
    meeting = make_meeting()
    _drain(pipeline, store, meeting.uid)

    revision = store.active_revision(meeting.uid)
    assert revision is not None
    assert str(revision.status) == RevisionStatus.PUBLISHED
    entries = store.timeline(meeting.uid)
    assert {e.track for e in entries} == {"mic", "system"}
    assert str(store.get_meeting(meeting.uid).attempt_state) == AttemptState.NONE


def test_configured_vocabulary_reaches_the_engine(
    pipeline, store, make_meeting
) -> None:
    """Scenario: Vocabulario global aplicado."""
    meeting = make_meeting()
    _drain(pipeline, store, meeting.uid)

    worker = pipeline.workers[0]
    assert all(vocab == "Kubernetes" for _, _, vocab in worker.jobs)
    revision = store.active_revision(meeting.uid)
    assert revision.vocabulary == "Kubernetes"


def test_one_failing_track_still_publishes_the_other(
    pipeline, store, make_meeting
) -> None:
    """Scenario: Uma das trilhas falha na primeira transcricao."""
    meeting = make_meeting()

    def factory(cfg, model):
        worker = FakeWorker(cfg, model)
        worker.fail_tracks = {"system": "dispositivo sem audio"}
        pipeline.workers.append(worker)
        return worker

    pipeline._worker_factory = factory
    _drain(pipeline, store, meeting.uid)

    entries = store.timeline(meeting.uid)
    assert {e.track for e in entries} == {"mic"}, "a trilha boa tem de sobreviver"
    meeting_now = store.get_meeting(meeting.uid)
    assert "system" in meeting_now.attempt_error
    assert "dispositivo sem audio" in meeting_now.attempt_error


def test_all_tracks_failing_leaves_no_revision(pipeline, store, make_meeting) -> None:
    meeting = make_meeting()

    def factory(cfg, model):
        worker = FakeWorker(cfg, model)
        worker.fail_tracks = {"mic": "erro A", "system": "erro B"}
        pipeline.workers.append(worker)
        return worker

    pipeline._worker_factory = factory
    _drain(pipeline, store, meeting.uid)

    assert store.active_revision(meeting.uid) is None
    meeting_now = store.get_meeting(meeting.uid)
    assert str(meeting_now.attempt_state) == AttemptState.FAILED
    assert "erro A" in meeting_now.attempt_error


def test_partial_reprocess_does_not_evict_a_complete_revision(
    pipeline, store, make_meeting
) -> None:
    """Scenario: Uma trilha falha no reprocessamento de reuniao ja completa.

    The existing transcript is the thing being protected: reprocessing may not
    destroy the only usable result a meeting has.
    """
    meeting = make_meeting()
    _drain(pipeline, store, meeting.uid)
    first = store.active_revision(meeting.uid)
    assert first is not None

    def factory(cfg, model):
        worker = FakeWorker(cfg, model)
        worker.fail_tracks = {"system": "falhou de novo"}
        pipeline.workers.append(worker)
        return worker

    pipeline._worker_factory = factory
    _drain(pipeline, store, meeting.uid)

    still = store.active_revision(meeting.uid)
    assert still.uid == first.uid, "a revisao completa anterior tem de continuar ativa"
    meeting_now = store.get_meeting(meeting.uid)
    assert str(meeting_now.attempt_state) == AttemptState.FAILED
    assert meeting_now.attempt_error, "o usuario precisa saber que nada foi trocado"


def test_reprocess_replaces_the_whole_timeline(pipeline, store, make_meeting) -> None:
    """Scenario: Reprocessamento com motor melhor -- nenhum segmento antigo fica."""
    meeting = make_meeting()

    def factory_with(text):
        def factory(cfg, model):
            worker = FakeWorker(cfg, model)
            worker.text_by_track = {"mic": text, "system": text}
            pipeline.workers.append(worker)
            return worker
        return factory

    pipeline._worker_factory = factory_with("versao antiga")
    _drain(pipeline, store, meeting.uid)

    pipeline._worker_factory = factory_with("versao nova")
    _drain(pipeline, store, meeting.uid)

    texts = {e.text for e in store.timeline(meeting.uid)}
    assert texts == {"versao nova"}
    assert "versao antiga" not in texts


# -- interruption ------------------------------------------------------

def test_interruption_discards_the_revision_entirely(
    pipeline, store, make_meeting
) -> None:
    """Scenario: Processo encerrado durante uma transcricao."""
    meeting = make_meeting()

    def factory(cfg, model):
        worker = FakeWorker(cfg, model)
        worker.interrupt_at = 1  # first track succeeds, second is cut off
        pipeline.workers.append(worker)
        return worker

    pipeline._worker_factory = factory



    with pytest.raises(_Interrupted):
        _drain(pipeline, store, meeting.uid)

    assert store.active_revision(meeting.uid) is None
    assert store.timeline(meeting.uid) == [], "nenhum segmento pode sobrar"
    statuses = {str(r.status) for r in store.revisions_of(meeting.uid)}
    assert RevisionStatus.BUILDING not in statuses


def test_interruption_preserves_the_previous_active_revision(
    pipeline, store, make_meeting
) -> None:
    meeting = make_meeting()
    _drain(pipeline, store, meeting.uid)
    first = store.active_revision(meeting.uid)

    def factory(cfg, model):
        worker = FakeWorker(cfg, model)
        worker.interrupt_at = 0
        pipeline.workers.append(worker)
        return worker

    pipeline._worker_factory = factory


    with pytest.raises(_Interrupted):
        _drain(pipeline, store, meeting.uid)

    assert store.active_revision(meeting.uid).uid == first.uid


def test_recover_pending_requeues_and_discards_half_written_work(
    pipeline, store, make_meeting
) -> None:
    """Scenario: Processo encerrado com sessoes na fila."""
    meeting = make_meeting()
    store.set_attempt_state(meeting.uid, AttemptState.RUNNING)
    revision = store.begin_revision(
        meeting.uid, engine=pipeline._worker_factory(pipeline._config, None).engine
    )
    store.add_segments(revision.id, "mic", [Segment(0, 1000, "meio escrito")])

    recovered = pipeline.recover_pending()

    assert recovered == 1
    assert str(store.get_meeting(meeting.uid).attempt_state) == AttemptState.QUEUED
    assert store.revision(revision.id).status == RevisionStatus.DISCARDED
    assert store.timeline(meeting.uid) == []


# -- the recording interlock ------------------------------------------

def test_suspending_kills_the_worker_and_reports_the_cost(
    pipeline, store, make_meeting
) -> None:
    """The machine belongs to the recording; inference lets go of the GPU."""
    meeting = make_meeting()
    _drain(pipeline, store, meeting.uid)
    assert pipeline.workers[0].killed == 0

    elapsed = pipeline.suspend_for_recording()

    assert pipeline.suspended is True
    assert pipeline.workers[0].killed == 1

    assert elapsed < INTERRUPT_BUDGET_S


def test_resuming_clears_the_suspension(pipeline) -> None:
    pipeline.suspend_for_recording()
    pipeline.resume_after_recording()
    assert pipeline.suspended is False


def test_queue_does_not_run_while_suspended(pipeline, store, make_meeting) -> None:
    """Scenario: Sessao enfileirada durante outra gravacao."""
    meeting = make_meeting()
    pipeline.enqueue(meeting.uid)
    pipeline.suspend_for_recording()

    pipeline.start()
    try:
        # Give the loop several turns; it must decline to touch the queue.
        pipeline.wait_idle(timeout=2.0)
        assert str(store.get_meeting(meeting.uid).attempt_state) == AttemptState.QUEUED
        assert store.active_revision(meeting.uid) is None
    finally:
        pipeline.stop()


def test_queue_runs_once_the_recording_ends(pipeline, store, make_meeting) -> None:
    meeting = make_meeting()
    pipeline.enqueue(meeting.uid)
    pipeline.suspend_for_recording()
    pipeline.start()
    try:
        pipeline.wait_idle(timeout=2.0)
        assert store.active_revision(meeting.uid) is None

        pipeline.resume_after_recording()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if store.active_revision(meeting.uid) is not None:
                break
            time.sleep(0.1)
    finally:
        pipeline.stop()

    assert store.active_revision(meeting.uid) is not None
    assert str(store.get_meeting(meeting.uid).attempt_state) == AttemptState.NONE


# -- the claim ---------------------------------------------------------

def test_a_meeting_gone_before_the_claim_is_skipped_for_the_next(
    pipeline, store, make_meeting
) -> None:
    """Deleted between being chosen and being claimed: the queue moves on.

    The choice and the claim are two moments. Whatever removes the meeting in
    between -- a deletion, in practice -- must leave the loop alive and
    working on the next meeting, with nothing recorded against the one that
    is gone.
    """
    make_meeting("reuniao-1")
    make_meeting("reuniao-2")
    pipeline.enqueue("reuniao-1")
    pipeline.enqueue("reuniao-2")
    events: list[tuple[str, str, str]] = []
    pipeline._on_event = lambda kind, uid, detail: events.append((kind, uid, detail))

    choose = pipeline._next_queued
    vanished: list[str] = []

    def choose_then_vanish(db):
        meeting = choose(db)
        if meeting is not None and not vanished:
            vanished.append(meeting.uid)
            db._conn.execute("DELETE FROM meetings WHERE uid = ?", (meeting.uid,))
            shutil.rmtree(meeting.directory)
        return meeting

    pipeline._next_queued = choose_then_vanish
    pipeline.start()
    try:
        deadline = time.monotonic() + 10
        remaining = None
        while time.monotonic() < deadline:
            if vanished:
                remaining = next(u for u in ("reuniao-1", "reuniao-2") if u != vanished[0])
                if store.active_revision(remaining) is not None:
                    break
            time.sleep(0.05)
        assert pipeline._thread is not None and pipeline._thread.is_alive()
    finally:
        pipeline.stop()

    assert remaining is not None
    assert store.get_meeting(vanished[0]) is None
    assert store.active_revision(remaining) is not None
    assert str(store.get_meeting(remaining).attempt_state) == AttemptState.NONE
    assert not [e for e in events if e[0] == "falhou"], events
    assert all(uid != vanished[0] for _, uid, _ in events), events


def test_the_claim_takes_only_a_waiting_meeting(store, make_meeting) -> None:
    meeting = make_meeting()
    assert store.claim_queued(meeting.uid) is False, "nao estava na fila"

    store.set_attempt_state(meeting.uid, AttemptState.QUEUED)
    assert store.claim_queued(meeting.uid) is True
    assert str(store.get_meeting(meeting.uid).attempt_state) == AttemptState.RUNNING
    assert store.claim_queued(meeting.uid) is False, "ja foi reivindicada"
    assert store.claim_queued("nao-existe") is False


def test_a_queued_meeting_that_is_deleted_is_never_transcribed(
    pipeline, store, make_meeting
) -> None:
    """Scenario: Reuniao aguardando na fila -- with the real queue loop."""
    from voxvault.store.deletion import delete_meeting

    doomed = make_meeting("reuniao-excluida")
    kept = make_meeting("reuniao-mantida")
    pipeline.enqueue(doomed.uid)

    outcome = delete_meeting(store, doomed.uid)
    assert outcome.deleted, outcome.reason
    pipeline.enqueue(kept.uid)

    pipeline.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if store.active_revision(kept.uid) is not None:
                break
            time.sleep(0.05)
    finally:
        pipeline.stop()

    assert store.active_revision(kept.uid) is not None
    assert store.get_meeting(doomed.uid) is None
    transcribed = [str(path) for worker in pipeline.workers for path in worker.paths]
    assert len(transcribed) == 2, "so as duas trilhas da reuniao mantida"
    assert all("reuniao-mantida" in path for path in transcribed), transcribed


def test_a_meeting_deleted_right_after_its_publication_does_not_stop_the_queue(
    pipeline, store, make_meeting, monkeypatch
) -> None:
    """Publication clears the attempt, so a deletion may land before the loop
    has finished wrapping the meeting up. The loop must survive it."""
    from voxvault.store import TranscriptStore
    from voxvault.store.deletion import delete_meeting

    make_meeting("reuniao-1")
    make_meeting("reuniao-2")
    pipeline.enqueue("reuniao-1")
    pipeline.enqueue("reuniao-2")
    events: list[tuple[str, str, str]] = []
    pipeline._on_event = lambda kind, uid, detail: events.append((kind, uid, detail))

    publish = TranscriptStore.publish_revision
    deleted: list[str] = []

    def publish_then_delete(self, revision_id, **kwargs):
        outcome = publish(self, revision_id, **kwargs)
        if not deleted:
            uid = self._conn.execute(
                "SELECT m.uid FROM revisions r JOIN meetings m ON m.id = r.meeting_id"
                " WHERE r.id = ?",
                (revision_id,),
            ).fetchone()[0]
            result = delete_meeting(self, uid)
            assert result.deleted, result.reason
            deleted.append(uid)
        return outcome

    monkeypatch.setattr(TranscriptStore, "publish_revision", publish_then_delete)
    pipeline.start()
    try:
        deadline = time.monotonic() + 10
        survivor = None
        while time.monotonic() < deadline:
            if deleted:
                survivor = next(u for u in ("reuniao-1", "reuniao-2") if u != deleted[0])
                if store.active_revision(survivor) is not None:
                    break
            time.sleep(0.05)
        assert pipeline._thread is not None and pipeline._thread.is_alive()
    finally:
        pipeline.stop()

    assert survivor is not None and store.active_revision(survivor) is not None
    assert store.get_meeting(deleted[0]) is None
    assert not Path(store.get_meeting(survivor).directory).with_name(deleted[0]).exists()
    assert not [e for e in events if e[0] == "falhou"], events
