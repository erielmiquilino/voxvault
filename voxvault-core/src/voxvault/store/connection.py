"""Opening the database: pragmas, migrations and write contention.

Three guarantees live here, and each one is enforced rather than hoped for.

**A reader never blocks on a writer.** Write-ahead logging is enabled at open.
That is verified in the test suite by two real processes, not by reading the
pragma back: the pragma says what was asked for, the two processes say what
actually happens.

**Migrations run once, even with several processes starting together.** The
resident service and one or more MCP servers can open the same database in the
same second. The migration is performed inside a single ``BEGIN IMMEDIATE``
transaction, so SQLite's own write lock is the cross-process mutex: the loser
waits, then re-reads the version inside its own transaction and finds nothing
left to do. Waiting has a deadline; past it the open fails saying so, instead
of proceeding on an indeterminate schema.

**A database from a newer build is refused without writing anything.** The
version is read through a connection opened read-only, which physically cannot
write, before any pragma that would touch the file.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Final

from ..errors import SchemaTooNewError, StorageBusyError, StorageError
from . import schema

#: How long a writer waits for the database before giving up.
DEFAULT_BUSY_TIMEOUT_S: Final = 5.0

#: How long a process waits for another process's migration.
DEFAULT_MIGRATION_TIMEOUT_S: Final = 30.0

_BUSY_MARKERS: Final = ("database is locked", "database table is locked")

_capture_path = threading.local()


@contextlib.contextmanager
def capture_execution_path() -> Iterator[None]:
    """Mark the calling thread as the audio capture path.

    Inside this context every blocking store write raises immediately instead
    of waiting on contention. The point is structural, not stylistic: the
    capture path must reach persistence only through :class:`AsyncWriter`, and
    a violation should fail loudly in a test rather than quietly cost audio
    during a real meeting.
    """
    previous = getattr(_capture_path, "active", False)
    _capture_path.active = True
    try:
        yield
    finally:
        _capture_path.active = previous


def in_capture_path() -> bool:
    return bool(getattr(_capture_path, "active", False))


def is_busy_error(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.OperationalError):
        message = str(exc).lower()
        return any(marker in message for marker in _BUSY_MARKERS)
    return False


def _read_version_without_writing(path: Path) -> int:
    """Read the schema version through a connection that cannot write.

    A missing file is version zero. Everything else is opened with ``mode=ro``
    so that refusing a too-new database is provably free of side effects.
    """
    if not path.exists():
        return 0
    try:
        return _read_version(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        # A process killed mid-write leaves the write-ahead log needing
        # recovery, and recovery needs write access to the shared-memory file
        # that a read-only connection cannot obtain. Recovering after a crash
        # is a first-class scenario here, so fall back to a writable
        # connection pinned in query-only mode: it can recover the log and
        # still refuses every write of ours.
        pass
    try:
        return _read_version(str(path), uri=False, query_only=True)
    except sqlite3.DatabaseError as exc:
        raise StorageError(
            f"Nao foi possivel ler a versao do esquema em {path}: {exc}. "
            f"O arquivo pode nao ser um banco do VoxVault."
        ) from None


def _read_version(target: str, *, uri: bool, query_only: bool = False) -> int:
    probe = sqlite3.connect(target, uri=uri, timeout=5.0)
    try:
        if query_only:
            probe.execute("PRAGMA query_only = ON")
        return schema.read_version(probe)
    finally:
        probe.close()


def open_connection(
    path: Path,
    *,
    busy_timeout_s: float = DEFAULT_BUSY_TIMEOUT_S,
    migration_timeout_s: float = DEFAULT_MIGRATION_TIMEOUT_S,
) -> tuple[sqlite3.Connection, list[int]]:
    """Open the database, migrating it if needed.

    Returns the connection and the list of migration versions this process
    actually applied -- empty when another process had already done it, which
    is what the concurrent-start test asserts on.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    found = _read_version_without_writing(path)
    if found > schema.SCHEMA_VERSION:
        raise SchemaTooNewError(found, schema.SCHEMA_VERSION)

    conn = sqlite3.connect(
        path,
        timeout=migration_timeout_s,
        isolation_level=None,  # transactions are explicit; see transaction()
        check_same_thread=True,
    )
    conn.row_factory = sqlite3.Row
    try:
        try:
            _apply_pragmas(conn, busy_timeout_s=migration_timeout_s)
        except sqlite3.OperationalError as exc:
            if is_busy_error(exc):
                raise _migration_wait_expired(path, migration_timeout_s) from None
            raise
        # A database already at the current version is opened without taking
        # the write lock at all. Taking it would make every startup queue
        # behind whatever is writing -- an MCP server opening while the
        # resident service publishes a transcription would simply wait, and
        # startup time is a product requirement.
        applied = (
            []
            if schema.read_version(conn) == schema.SCHEMA_VERSION
            else _migrate(conn, path, migration_timeout_s)
        )
        # Back to the normal write-contention budget once the schema is settled.
        conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_s * 1000)}")
    except BaseException:
        conn.close()
        raise
    return conn, applied


def _apply_pragmas(conn: sqlite3.Connection, *, busy_timeout_s: float) -> None:
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_s * 1000)}")
    mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    if str(mode).lower() != "wal":
        raise StorageError(
            f"Nao foi possivel habilitar o modo WAL no banco (modo atual: {mode}). "
            f"Leituras concorrentes ficariam bloqueadas durante as escritas."
        )
    # NORMAL is the right pairing with WAL: a commit is durable against a
    # process crash, and only a machine power loss can cost the last commits.
    # Audio durability is handled where audio is written, not here.
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA temp_store = MEMORY")


def _migrate(
    conn: sqlite3.Connection, path: Path, migration_timeout_s: float
) -> list[int]:
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if is_busy_error(exc):
            raise _migration_wait_expired(path, migration_timeout_s) from None
        raise
    try:
        # Re-read inside the lock: between the read-only probe and this point
        # another process may have migrated the database past what this build
        # knows. Rolling back here leaves this connection's work undone; the
        # only file change that may already have happened is the journal mode,
        # which carries no data.
        found = schema.read_version(conn)
        if found > schema.SCHEMA_VERSION:
            conn.execute("ROLLBACK")
            raise SchemaTooNewError(found, schema.SCHEMA_VERSION)
        applied = schema.apply_migrations(conn, pid=_pid())
        conn.execute("COMMIT")
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    return applied


def _migration_wait_expired(path: Path, timeout_s: float) -> StorageError:
    return StorageError(
        f"A espera pela migracao conduzida por outro processo excedeu "
        f"{timeout_s:.0f} segundos no banco {path}. A abertura foi abortada "
        f"para nao operar sobre um esquema indeterminado."
    )


def _pid() -> int:
    import os

    return os.getpid()


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection, *, busy_timeout_s: float) -> Iterator[None]:
    """A short write transaction.

    ``BEGIN IMMEDIATE`` and not the default deferred begin: a deferred
    transaction that starts by reading and only later writes cannot be retried
    by SQLite's busy handler, so it would fail instantly under contention
    instead of waiting the budget out.

    Nothing slow belongs inside. Reading a file, running a transcription or
    waiting on any external I/O while holding this lock turns a five second
    budget for everyone else into whatever that work takes.
    """
    if in_capture_path():
        raise StorageError(
            "Escrita sincrona no armazenamento a partir do caminho de captura "
            "de audio. A espera por contencao nao pode ocorrer nesse caminho: "
            "use AsyncWriter.submit(), que entrega a operacao a uma thread "
            "propria e retorna imediatamente."
        )
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        if is_busy_error(exc):
            raise StorageBusyError(
                f"O armazenamento continuou ocupado por outro escritor apos "
                f"{busy_timeout_s:.0f} segundos de espera. A operacao foi "
                f"abortada e nada foi gravado."
            ) from None
        raise
    try:
        yield
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    try:
        conn.execute("COMMIT")
    except sqlite3.OperationalError as exc:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        if is_busy_error(exc):
            raise StorageBusyError(
                f"O armazenamento continuou ocupado por outro escritor apos "
                f"{busy_timeout_s:.0f} segundos de espera. A transacao foi "
                f"desfeita e nada foi gravado."
            ) from None
        raise
