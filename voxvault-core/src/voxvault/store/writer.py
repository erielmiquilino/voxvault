"""Persistence decoupled from capture.

Why this exists
---------------

The store admits one writer at a time. When the MCP server, the interface and
the resident service all write, a writer can be made to wait, and the wait is
bounded at five seconds before it fails. Five seconds of waiting on the audio
capture path would be five seconds of lost meeting.

So the capture path never waits. It calls :meth:`AsyncWriter.submit`, which
appends to an unbounded in-memory queue and returns -- no SQLite call, no lock,
no syscall that can block on another process. A dedicated thread drains the
queue and is the only thing that ever pays the contention budget.

How the decoupling is enforced rather than trusted
--------------------------------------------------

Three things make it structural:

1. ``submit`` is the only method offered to the capture path, and it cannot
   touch the database: it has no connection. The connection is created inside
   the worker thread and never leaves it.
2. ``SimpleQueue.put`` is unbounded and lock-free for the producer, so the
   producer has no path that blocks on the consumer.
3. :func:`voxvault.store.connection.capture_execution_path` marks the capture
   thread. Any synchronous store write attempted from inside that mark raises
   immediately instead of waiting. A regression that reintroduces a blocking
   write on the capture path fails a test instead of costing audio in a real
   meeting.

What it does not do
-------------------

It does not make a write more likely to succeed. If contention outlasts the
budget, the operation fails and is recorded in :attr:`failures`, where the
session can surface it. Losing a database write is recoverable; losing audio
is not, and that is the trade this class encodes.
"""

from __future__ import annotations

import queue
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from . import connection as _conn
from .store import TranscriptStore

Operation = Callable[[TranscriptStore], None]

_MAX_RECORDED_FAILURES: Final = 100


@dataclass(frozen=True, slots=True)
class WriteFailure:
    label: str
    error: str


class _Stop:
    __slots__ = ()


class _Barrier:
    __slots__ = ("event",)

    def __init__(self) -> None:
        self.event = threading.Event()


class AsyncWriter:
    """A single writer thread in front of the store."""

    def __init__(
        self,
        path: str | Path,
        *,
        busy_timeout_s: float = _conn.DEFAULT_BUSY_TIMEOUT_S,
        name: str = "voxvault-store-writer",
    ) -> None:
        self.path = Path(path)
        self.busy_timeout_s = busy_timeout_s
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._failures: deque[WriteFailure] = deque(maxlen=_MAX_RECORDED_FAILURES)
        self._lock = threading.Lock()
        self._submitted = 0
        self._completed = 0
        self._failed = 0
        self._started = False

    # -- lifecycle -------------------------------------------------------

    def start(self, *, timeout_s: float = 30.0) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()
        if not self._ready.wait(timeout_s):
            raise TimeoutError(
                f"A thread de escrita nao ficou pronta em {timeout_s:.0f} segundos."
            )
        if self._start_error is not None:
            raise self._start_error

    def stop(self, *, timeout_s: float = 30.0) -> bool:
        if not self._started:
            return True
        self._queue.put(_Stop())
        self._thread.join(timeout_s)
        return not self._thread.is_alive()

    def __enter__(self) -> AsyncWriter:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- producer side ---------------------------------------------------

    def submit(self, operation: Operation, *, label: str = "") -> None:
        """Hand a write to the writer thread. Never blocks, never touches SQLite.

        Safe to call from the audio callback's thread or from any capture
        worker: the only work done here is appending to an unbounded queue.
        """
        self._queue.put((operation, label or getattr(operation, "__name__", "escrita")))
        with self._lock:
            self._submitted += 1

    def flush(self, *, timeout_s: float = 30.0) -> bool:
        """Wait until everything submitted so far has been attempted.

        For shutdown and for tests. Never call it from the capture path.
        """
        barrier = _Barrier()
        self._queue.put(barrier)
        return barrier.event.wait(timeout_s)

    # -- observability ---------------------------------------------------

    @property
    def pending(self) -> int:
        with self._lock:
            return self._submitted - self._completed - self._failed

    @property
    def submitted(self) -> int:
        with self._lock:
            return self._submitted

    @property
    def completed(self) -> int:
        with self._lock:
            return self._completed

    @property
    def failures(self) -> tuple[WriteFailure, ...]:
        with self._lock:
            return tuple(self._failures)

    # -- consumer side ---------------------------------------------------

    def _run(self) -> None:
        try:
            store = TranscriptStore(self.path, busy_timeout_s=self.busy_timeout_s)
        except BaseException as exc:
            self._start_error = exc
            self._ready.set()
            return
        self._ready.set()
        try:
            while True:
                item = self._queue.get()
                if isinstance(item, _Stop):
                    return
                if isinstance(item, _Barrier):
                    item.event.set()
                    continue
                operation, label = item
                try:
                    operation(store)
                except BaseException as exc:
                    with self._lock:
                        self._failed += 1
                        self._failures.append(WriteFailure(label, str(exc)))
                else:
                    with self._lock:
                        self._completed += 1
        finally:
            store.close()
