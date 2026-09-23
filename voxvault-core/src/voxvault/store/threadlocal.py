"""One store handle per thread.

A ``sqlite3`` connection belongs to the thread that opened it and raises if
another one touches it. Three parts of VoxVault run work on threads they did
not create the connection on -- the transcription queue's loop, the MCP
protocol layer's worker pool, and the service's HTTP handlers -- and each hit
this separately before it was written down once.

Write-ahead logging is what makes several handles on one database safe, and it
is already on: readers do not block the writer and the writer does not block
readers. The contention policy lives in the store itself, so nothing here has
to know about it.

Handles are closed when :meth:`close_all` runs, and a thread that ends with
its work closes its own with :meth:`release`. The difference matters: a pooled
worker thread is reused for the life of the process, so its handle is one of a
bounded, small number, but the service's HTTP server starts a thread per
request -- and a resident app asks every few seconds, all day. Left open, each
request's connection stayed behind: measured, about 2 MB a minute and a file
handle per request.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .store import TranscriptStore


class ThreadLocalStore:
    """Hands each thread its own connection to the same database."""

    def __init__(
        self,
        db_path: Path,
        *,
        factory: Callable[[Path], TranscriptStore] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self._factory = factory
        self._local = threading.local()
        self._all: list = []
        self._lock = threading.Lock()

    def _build(self) -> TranscriptStore:
        if self._factory is not None:
            return self._factory(self.db_path)
        from .store import TranscriptStore

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return TranscriptStore(self.db_path)

    @property
    def handle(self) -> TranscriptStore:
        existing = getattr(self._local, "store", None)
        if existing is not None:
            return existing
        built = self._build()
        self._local.store = built
        with self._lock:
            self._all.append(built)
        return built

    def __getattr__(self, name: str):
        """Delegate anything else to this thread's handle.

        Lets a caller hold one of these and use it as though it were the store,
        which is what keeps the constraint from leaking into every call site.
        """
        return getattr(self.handle, name)

    def release(self) -> None:
        """Close and forget the calling thread's handle, if it opened one.

        Only the owning thread may close a connection, which is why a thread
        that is about to end does this itself instead of anyone sweeping up
        after it.
        """
        existing = getattr(self._local, "store", None)
        if existing is None:
            return
        self._local.store = None
        with self._lock:
            try:
                self._all.remove(existing)
            except ValueError:
                pass
        try:
            existing.close()
        except Exception:
            pass

    def close_all(self) -> None:
        with self._lock:
            handles, self._all = self._all, []
        for handle in handles:
            try:
                handle.close()
            except Exception:
                pass
        self._local = threading.local()

    @property
    def open_handles(self) -> int:
        with self._lock:
            return len(self._all)
