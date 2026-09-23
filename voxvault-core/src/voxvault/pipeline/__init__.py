"""The transcription queue.

Holds the premise the whole product rests on: **during a meeting the machine
only records**. Transcription saturates GPU and CPU, and a tool that makes the
machine unusable during the call has failed at the thing it exists for.

So the queue never runs while a recording is active, and when a recording is
requested mid-transcription the running work is killed rather than asked
politely to finish.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..errors import VoxVaultError
from ..layout import available_tracks
from ..types import AttemptState, EngineInfo, Segment

#: The specification's budget for starting a recording while transcribing,
#: measured from the command to the first captured sample.
INTERRUPT_BUDGET_S = 2.0
#: How long the worker is given to exit politely before it is killed. Kept well
#: inside the budget so the kill path still fits.
GRACEFUL_EXIT_S = 0.4
#: Idle poll interval of the queue loop. Long enough to cost nothing, short
#: enough that a finished recording starts transcribing promptly.
POLL_INTERVAL_S = 1.0


class QueueRefused(VoxVaultError):
    """A second attempt was requested for a meeting that already has one."""


@dataclass(slots=True)
class TrackOutcome:
    track: str
    ok: bool
    segments: list[Segment]
    error: str = ""


class _Worker:
    """A live inference subprocess with its model already loaded."""

    def __init__(self, config: Config, model: str | None) -> None:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        self._process = subprocess.Popen(
            [sys.executable, "-m", "voxvault.pipeline.worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=env, bufsize=1,
        )
        self.engine: EngineInfo | None = None
        # A model is sent only when somebody chose one. Sending the built-in
        # default would turn it into an explicit choice on the other side,
        # and the worker would stop picking the model by hardware.
        from ..engine.capability import model_is_default

        self._send({
            "data_dir": str(config.data_dir),
            "model": model or (None if model_is_default(config) else config.model),
            "allow_cpu_fallback": config.allow_cpu_fallback,
        })
        reply = self._receive()
        if reply is None or not reply.get("ok"):
            detail = (reply or {}).get("error", "o trabalhador nao respondeu")
            self.kill()
            raise VoxVaultError(f"Nao foi possivel carregar o motor: {detail}")
        spec = reply["engine"]
        self.engine = EngineInfo(
            name=spec["name"], model=spec["model"], device=spec["device"],
            compute_type=spec["compute_type"], version=spec.get("version", ""),
        )

    @property
    def alive(self) -> bool:
        return self._process.poll() is None

    def _send(self, payload: dict) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._process.stdin.flush()

    def _receive(self) -> dict | None:
        assert self._process.stdout is not None
        while True:
            line = self._process.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue

    def transcribe(
        self, audio_path: Path, track: str, language: str, vocabulary: str
    ) -> TrackOutcome:
        self._send({
            "job": "transcribe",
            "audio_path": str(audio_path),
            "track": track,
            "language": language,
            "vocabulary": vocabulary,
        })
        reply = self._receive()
        if reply is None:
            # The pipe closed: the process was killed, which is how an
            # interruption reaches us. Nothing partial is kept.
            raise _Interrupted()
        if not reply.get("ok"):
            return TrackOutcome(track, False, [], reply.get("error", "falha"))
        segments = [
            Segment(s["start_ms"], s["end_ms"], s["text"]) for s in reply["segments"]
        ]
        return TrackOutcome(track, True, segments)

    def kill(self) -> float:
        """Stop the worker and return how long releasing it took.

        Terminate first so a healthy worker exits cleanly, then kill. GPU
        memory comes back when the process is gone, which is precisely why
        inference runs out of process.
        """
        started = time.monotonic()
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=GRACEFUL_EXIT_S)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=1.0)
        for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        return time.monotonic() - started


class _Interrupted(Exception):
    """Raised inside the loop when the worker was killed mid-job."""


class TranscriptionPipeline:
    """Persistent, resumable queue that yields the machine to recording."""

    def __init__(
        self,
        config: Config,
        store,
        *,
        on_event: Callable[[str, str, str], None] | None = None,
        worker_factory: Callable[[Config, str | None], _Worker] | None = None,
    ) -> None:
        self._config = config
        self._store = store
        # (kind, meeting uid, detail). The identifier travels on its own so
        # a client can act on it -- open the meeting a notification is about
        # -- without parsing it back out of a sentence.
        self._on_event = on_event or (lambda kind, uid, detail: None)
        # Injectable so the queue's own logic -- ordering, refusal, partial
        # publication, interruption -- is testable without a GPU or a model.
        self._worker_factory = worker_factory or (lambda cfg, model: _Worker(cfg, model))
        self._halt = threading.Event()
        self._recording = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._worker: _Worker | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # A sqlite3 connection belongs to the thread that opened it. The queue
        # loop runs on its own thread, so it opens its own handle rather than
        # borrowing the caller's; write-ahead logging is what makes two handles
        # on one database safe.
        self._handles = None
        self._owner = threading.current_thread()

    # -- storage per thread ---------------------------------------------

    @property
    def _db(self):
        """The store handle belonging to the calling thread.

        The caller's own handle is reused on the caller's thread; the queue
        loop, which runs on its own, opens one of its own through the shared
        helper.
        """
        if threading.current_thread() is self._owner:
            return self._store
        if self._handles is None:
            from ..store import ThreadLocalStore

            self._handles = ThreadLocalStore(self._config.db_path)
        return self._handles.handle

    def release_thread_store(self) -> None:
        """Close the calling thread's own handle, for a thread about to end.

        The owner thread borrows the caller's store and has nothing of its own
        to release; any other thread that asked this pipeline something, an
        HTTP request of the service in practice, opened one here.
        """
        if self._handles is not None and threading.current_thread() is not self._owner:
            self._handles.release()

    def _close_thread_store(self) -> None:
        if self._handles is not None:
            self._handles.close_all()
            self._handles = None

    # -- queue ---------------------------------------------------------

    def enqueue(self, meeting_uid: str, *, allow_requeue: bool = False) -> None:
        """Put a meeting in line, refusing a duplicate attempt.

        Never more than one attempt per meeting: a second request while one is
        pending is refused rather than queued, and the running attempt is left
        alone.
        """
        meeting = self._db.get_meeting(meeting_uid)
        if meeting is None:
            raise QueueRefused(f"Reuniao desconhecida: {meeting_uid}")

        state = str(meeting.attempt_state)
        if not allow_requeue and state in {AttemptState.QUEUED, AttemptState.RUNNING}:
            human = (
                "ja esta aguardando na fila" if state == AttemptState.QUEUED
                else "ja esta sendo transcrita"
            )
            raise QueueRefused(
                f"A reuniao '{meeting.title}' {human}. O pedido foi recusado para "
                f"nao duplicar nem interromper a tentativa em andamento."
            )

        directory = Path(meeting.directory)
        if not available_tracks(directory):
            raise QueueRefused(
                f"A reuniao '{meeting.title}' nao tem mais audio em disco "
                f"({directory}), entao nao pode ser transcrita novamente. "
                f"A transcricao existente permanece intacta."
            )

        self._db.set_attempt_state(meeting_uid, AttemptState.QUEUED)
        self._on_event("enfileirada", meeting_uid, "")

    def recover_pending(self) -> int:
        """Return anything that was mid-flight to the queue, discarding its work.

        A process that died during a transcription leaves a revision under
        construction. It is discarded whole rather than published: half a
        transcription presented as a whole one is worse than none, because
        nothing downstream can tell.
        """
        recovered = 0
        for meeting in self._db.iter_meetings():
            if str(meeting.attempt_state) != AttemptState.RUNNING:
                continue
            for revision in self._db.revisions_of(meeting.uid):
                if str(revision.status) == "em_construcao":
                    self._db.discard_revision(
                        revision.id,
                        "o processo foi encerrado durante a transcricao",
                    )
            self._db.set_attempt_state(meeting.uid, AttemptState.QUEUED)
            recovered += 1
        return recovered

    def pending(self) -> list:
        return self._db.meetings_with_attempt(AttemptState.QUEUED, AttemptState.RUNNING)

    # -- recording interlock -------------------------------------------

    def suspend_for_recording(self) -> float:
        """Free the machine for a recording, and report how long it took.

        Returns the seconds spent releasing inference resources, so the caller
        can hold the whole start sequence to its budget and report honestly
        when the budget was missed instead of quietly exceeding it.
        """
        started = time.monotonic()
        self._recording.set()
        with self._lock:
            worker, self._worker = self._worker, None
        if worker is not None:
            worker.kill()
        return time.monotonic() - started

    def resume_after_recording(self) -> None:
        self._recording.clear()

    @property
    def suspended(self) -> bool:
        return self._recording.is_set()

    # -- loop ----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._halt.clear()
        self._thread = threading.Thread(
            target=self._run, name="voxvault-transcricao", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._halt.set()
        with self._lock:
            worker, self._worker = self._worker, None
        if worker is not None:
            worker.kill()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    def _run(self) -> None:
        while not self._halt.is_set():
            if self._recording.is_set():
                self._release_worker()
                self._idle.set()
                self._halt.wait(POLL_INTERVAL_S)
                continue

            meeting = self._next_queued(self._db)
            if meeting is None:
                # Nothing to do: let go of the GPU rather than sitting on it.
                self._release_worker()
                self._idle.set()
                self._halt.wait(POLL_INTERVAL_S)
                continue

            self._idle.clear()
            try:
                self._process(meeting)
            except _Interrupted:
                self._db.set_attempt_state(meeting.uid, AttemptState.QUEUED)
                self._on_event("interrompida", meeting.uid, "")
            except Exception as exc:
                if self._db.get_meeting(meeting.uid) is None:
                    # Deleted once its transcript was published, while the
                    # attempt was still being wrapped up: nothing left to mark.
                    continue
                self._db.set_attempt_state(
                    meeting.uid, AttemptState.FAILED, f"{type(exc).__name__}: {exc}"
                )
                self._on_event("falhou", meeting.uid, str(exc))
        self._release_worker()
        self._close_thread_store()
        self._idle.set()

    def _next_queued(self, store):
        waiting = store.meetings_with_attempt(AttemptState.QUEUED)
        return waiting[0] if waiting else None

    def _release_worker(self) -> None:
        with self._lock:
            worker, self._worker = self._worker, None
        if worker is not None:
            worker.kill()

    def _ensure_worker(self, model: str | None = None) -> _Worker:
        with self._lock:
            if self._worker is not None and self._worker.alive:
                return self._worker
        worker = self._worker_factory(self._config, model)
        with self._lock:
            self._worker = worker
        return worker

    # -- one meeting ---------------------------------------------------

    def _process(self, meeting) -> None:
        # Claimed with one conditional write instead of chosen and then
        # marked. Between the choice and the claim the meeting may have been
        # deleted, and the database is what decides which of the two came
        # first: losing the claim just means there is nothing here to do.
        if not self._db.claim_queued(meeting.uid):
            return

        directory = Path(meeting.directory)
        tracks = available_tracks(directory)
        if not tracks:
            self._db.set_attempt_state(
                meeting.uid, AttemptState.FAILED,
                f"o audio da reuniao nao esta mais em {directory}",
            )
            return

        self._on_event("transcrevendo", meeting.uid, "")

        vocabulary = self._config.vocabulary
        language = self._config.language

        worker = self._ensure_worker()
        assert worker.engine is not None

        revision = self._db.begin_revision(
            meeting.uid,
            engine=worker.engine,
            vocabulary=vocabulary,
            language=language,
            config_fingerprint=f"{language}|{worker.engine.identifier()}",
        )

        outcomes: list[TrackOutcome] = []
        try:
            for track, audio_path in sorted(tracks.items()):
                # Each track is independent: one failing must not stop the
                # other, because half a meeting is far better than none.
                outcome = worker.transcribe(audio_path, track, language, vocabulary)
                outcomes.append(outcome)
                if outcome.ok and outcome.segments:
                    self._db.add_segments(revision.id, track, outcome.segments)
        except _Interrupted:
            self._db.discard_revision(
                revision.id, "interrompida para liberar a maquina para uma gravacao"
            )
            raise

        ok = [o.track for o in outcomes if o.ok]
        failed = [o.track for o in outcomes if not o.ok]
        reason = "; ".join(f"{o.track}: {o.error}" for o in outcomes if not o.ok)

        if not ok:
            self._db.discard_revision(revision.id, reason or "todas as trilhas falharam")
            self._db.set_attempt_state(meeting.uid, AttemptState.FAILED, reason)
            self._on_event("falhou", meeting.uid, reason)
            return

        outcome = self._db.publish_revision(
            revision.id, tracks_ok=ok, tracks_failed=failed, failure_reason=reason,
        )

        if outcome.published:
            self._db.set_attempt_state(meeting.uid, AttemptState.NONE, reason)
            self._on_event("pronta", meeting.uid, "")
            self._regenerate_exports(meeting.uid)
        else:
            # Refused because what the meeting already had was more complete.
            # Nothing to regenerate: the active revision did not change.
            self._db.set_attempt_state(
                meeting.uid, AttemptState.FAILED, outcome.message
            )
            self._on_event("preservada", meeting.uid, outcome.message)

    def _regenerate_exports(self, meeting_uid: str) -> None:
        try:
            from ..store import regenerate_exports

            regenerate_exports(self._db, meeting_uid)
        except Exception as exc:  # exports are derived; never fail the meeting
            self._on_event("aviso", meeting_uid, f"exportacoes: {exc}")


__all__ = ["INTERRUPT_BUDGET_S", "QueueRefused", "TrackOutcome", "TranscriptionPipeline"]
