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

Handles are closed when :meth:`close_all` runs, not when a thread ends. That
is deliberate: a pooled worker thread is reused for the life of the process,
so a handle per thread is a bounded, small number.
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
        factory: Callable[[Path], "TranscriptStore"] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self._factory = factory
        self._local = threading.local()
        self._all: list = []
        self._lock = threading.Lock()

    def _build(self) -> "TranscriptStore":
        if self._factory is not None:
            return self._factory(self.db_path)
        from .store import TranscriptStore  # noqa: PLC0415

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return TranscriptStore(self.db_path)

    @property
    def handle(self) -> "TranscriptStore":
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
