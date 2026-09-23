"""Deleting a meeting for good.

A meeting lives in two places -- rows in the database and a directory on disk
-- and nobody reading the store may ever see one without the other. So the
deletion is a choreography, and the order is the whole design:

1. **Check** the rows: the meeting exists, is not being recorded and has no
   attempt running. Otherwise refuse, having touched nothing.
2. **Rename** ``recordings/<uid>`` to ``recordings/.excluindo-<uid>``. Atomic
   on one volume, and it fails when any file inside is open without delete
   sharing -- the one failure a deletion really meets in practice, placed
   first so that it costs nothing.
3. **Delete the rows** in one ``BEGIN IMMEDIATE`` transaction that checks the
   state again (:meth:`TranscriptStore.purge_meeting`). A refusal there puts
   the directory back where it was.
4. **Remove the tombstone.** A failure here undoes nothing: the meeting is
   already gone, and the next start of the service removes what is left
   (:func:`resolve_tombstones`).

A process that dies anywhere in between leaves either a whole meeting or no
meeting, plus at most one tombstone whose fate the database decides: restored
when the meeting is still there, removed when it is not.

The file a meeting was imported from lives outside the meeting's directory
and is never looked at here. Only the copy the data directory keeps goes.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ..errors import DeletionRefused, StorageError
from ..types import AttemptState, MeetingState

if TYPE_CHECKING:
    from .store import TranscriptStore

#: What a meeting's directory is renamed to while its rows are deleted. Never
#: a valid identifier, so a build that does not know about deletion ignores it.
TOMBSTONE_PREFIX: Final = ".excluindo-"

RECORDING_REFUSAL: Final = (
    "a reuniao esta sendo gravada. Encerre a gravacao antes de exclui-la."
)
RUNNING_REFUSAL: Final = (
    "a transcricao desta reuniao esta em execucao. Aguarde ela terminar para "
    "excluir a reuniao."
)
IN_USE_REFUSAL: Final = (
    "um arquivo da reuniao esta aberto por outro programa -- o reprodutor de "
    "audio, um editor, o antivirus ou o Explorador de Arquivos na pasta dela. "
    "Feche-o e tente de novo em instantes. Nada foi removido."
)

#: A refusal as a word a program can branch on, beside the sentence a person
#: reads. The interface retries once on ``em_uso`` -- its own audio player may
#: still be letting go of the file -- and on nothing else.
CAUSES: Final = {
    RECORDING_REFUSAL: "gravando",
    RUNNING_REFUSAL: "transcrevendo",
    IN_USE_REFUSAL: "em_uso",
}
CAUSE_GONE: Final = "inexistente"
CAUSE_OTHER: Final = "falha"


def cause_of(reason: str, *, gone: bool = False) -> str:
    """The machine-readable word for a refusal; empty when there is none."""
    if not reason:
        return ""
    if gone:
        return CAUSE_GONE
    return CAUSES.get(reason, CAUSE_OTHER)


#: Attempts, and the pause between them, at removing a tombstone. An indexer
#: or an antivirus opening a file a moment after the rename is the usual
#: reason for a first failure, and it passes quickly.
_REMOVE_ATTEMPTS: Final = 3
_REMOVE_PAUSE_S: Final = 0.1

#: Test seam: called with a label between the steps, so a test can stop the
#: choreography exactly where a crash would. Always ``None`` in production.
interruption_hook: Callable[[str], None] | None = None


def deletion_blocker(state: str, attempt_state: str) -> str:
    """Why a meeting in this state cannot be deleted now; empty if it can.

    One function for the check before the rename and the check inside the
    transaction, so the two can never disagree about what blocks a deletion.
    """
    if str(state) in (MeetingState.RECORDING.value, MeetingState.PAUSED.value):
        return RECORDING_REFUSAL
    if str(attempt_state) == AttemptState.RUNNING.value:
        return RUNNING_REFUSAL
    return ""


@dataclass(frozen=True, slots=True)
class DeletionFile:
    """One file a deletion removes, relative to the meeting's directory."""

    path: str
    size: int


@dataclass(frozen=True, slots=True)
class DeletionPreview:
    """Everything a deletion would take, read without writing anything.

    ``blocker`` says why the deletion would be refused if it were asked for
    right now, and is empty when it would go through.
    """

    uid: str
    title: str
    started_at_ms: int
    duration_ms: int
    revisions: int
    notes: int
    directory: str
    files: tuple[DeletionFile, ...]
    blocker: str

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def cause(self) -> str:
        return cause_of(self.blocker)


@dataclass(frozen=True, slots=True)
class DeletionOutcome:
    """What happened to one meeting of a deletion request.

    ``reason`` is the sentence for a person, ``cause`` the word for a program
    (:data:`CAUSES`); both are empty when the meeting was deleted.
    """

    uid: str
    preview: DeletionPreview | None
    deleted: bool
    reason: str = ""
    cause: str = ""


def preview_deletion(store: TranscriptStore, uid: str) -> DeletionPreview:
    """What deleting ``uid`` would remove. Reads only; changes nothing."""
    meeting = store.get_meeting(uid)
    if meeting is None:
        raise DeletionRefused(
            f"a reuniao '{uid}' nao existe no armazenamento.", gone=True
        )
    revisions, notes = store.meeting_footprint(uid)
    directory = Path(meeting.directory)
    return DeletionPreview(
        uid=meeting.uid,
        title=meeting.title,
        started_at_ms=meeting.started_at_ms,
        duration_ms=meeting.duration_ms,
        revisions=revisions,
        notes=notes,
        directory=str(directory),
        files=tuple(_files_of(_where_the_files_are(directory))),
        blocker=deletion_blocker(str(meeting.state), str(meeting.attempt_state)),
    )


def delete_meeting(store: TranscriptStore, uid: str) -> DeletionOutcome:
    """Delete one meeting, or refuse with the reason and remove nothing."""
    try:
        preview = preview_deletion(store, uid)
    except DeletionRefused as exc:
        return _refused(uid, None, exc.reason, gone=exc.gone)
    if preview.blocker:
        return _refused(uid, preview, preview.blocker)

    directory = Path(preview.directory)
    if directory.name != uid:
        # Every meeting directory is named after its meeting. One that is not
        # is not ours to remove, whatever the row says.
        return _refused(
            uid, preview,
            f"o diretorio registrado para a reuniao ({directory}) nao e o "
            f"diretorio dela. Nada foi removido.",
        )
    tombstone = tombstone_of(directory)

    if directory.exists():
        try:
            os.rename(directory, tombstone)
        except PermissionError:
            return _refused(uid, preview, IN_USE_REFUSAL)
        except OSError as exc:
            return _refused(
                uid, preview,
                f"nao foi possivel mover a pasta da reuniao ({exc}). "
                f"Nada foi removido.",
            )
    _checkpoint("depois_de_renomear")

    try:
        store.purge_meeting(uid)
    except DeletionRefused as exc:
        if exc.gone:
            # Deleted by someone else between the check and the transaction:
            # the files are nobody's any more.
            _remove_tree(tombstone)
        else:
            _put_back(tombstone, directory)
        return _refused(uid, preview, exc.reason, gone=exc.gone)
    except Exception as exc:
        # Busy past its budget, or anything else the database raised: the
        # transaction rolled back, so the rows are whole and the directory
        # goes back to them.
        _put_back(tombstone, directory)
        reason = (
            str(exc)
            if isinstance(exc, StorageError)
            else f"o armazenamento recusou a exclusao ({type(exc).__name__}: "
            f"{exc}). Nada foi removido."
        )
        return _refused(uid, preview, reason)
    _checkpoint("depois_do_banco")

    # A service starting at this very moment may already have put the
    # directory back, seeing the meeting still listed; either way, what is on
    # disk now belongs to nothing.
    _remove_tree(tombstone if tombstone.exists() else directory)
    return DeletionOutcome(uid, preview, True)


def _refused(
    uid: str, preview: DeletionPreview | None, reason: str, *, gone: bool = False
) -> DeletionOutcome:
    return DeletionOutcome(uid, preview, False, reason, cause_of(reason, gone=gone))


def delete_meetings(
    store: TranscriptStore, uids: Iterable[str]
) -> list[DeletionOutcome]:
    """Delete several meetings, each on its own.

    One refusal stops nothing else, and each meeting ends either whole or
    gone: the choreography runs to completion, or to its refusal, for one
    meeting before the next one starts.
    """
    seen: set[str] = set()
    outcomes: list[DeletionOutcome] = []
    for uid in uids:
        if uid in seen:
            continue
        seen.add(uid)
        outcomes.append(delete_meeting(store, uid))
    return outcomes


def resolve_tombstones(store: TranscriptStore, data_dir: Path) -> int:
    """Finish or undo every deletion a dead process left half done.

    The database is the judge. A meeting still listed means the process died
    before its rows went, so its directory comes back; a meeting no longer
    listed means it died after, so the tombstone goes. Returns how many were
    resolved either way.
    """
    root = Path(data_dir) / "recordings"
    if not root.is_dir():
        return 0
    resolved = 0
    for tombstone in sorted(root.glob(f"{TOMBSTONE_PREFIX}*")):
        uid = tombstone.name[len(TOMBSTONE_PREFIX):]
        if not uid or not tombstone.is_dir():
            continue
        if store.get_meeting(uid) is not None:
            target = root / uid
            if target.exists() or not _put_back(tombstone, target):
                continue
        elif not _remove_tree(tombstone):
            continue
        resolved += 1
    return resolved


def tombstone_of(directory: Path) -> Path:
    return directory.with_name(f"{TOMBSTONE_PREFIX}{directory.name}")


def _where_the_files_are(directory: Path) -> Path:
    """The meeting's directory, or the tombstone an interrupted deletion left."""
    if not directory.exists():
        tombstone = tombstone_of(directory)
        if tombstone.is_dir():
            return tombstone
    return directory


def _files_of(directory: Path) -> list[DeletionFile]:
    if not directory.is_dir():
        return []
    found: list[DeletionFile] = []
    for base, _dirs, names in os.walk(directory):
        for name in names:
            path = Path(base) / name
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            found.append(
                DeletionFile(path.relative_to(directory).as_posix(), size)
            )
    return sorted(found, key=lambda f: f.path)


def _put_back(tombstone: Path, directory: Path) -> bool:
    try:
        os.rename(tombstone, directory)
    except OSError:
        # Left for the next start of the service, which restores it: the
        # meeting is still listed, so the tombstone is still its directory.
        return False
    return True


def _remove_tree(path: Path) -> bool:
    """Remove a directory, retrying briefly; ``True`` once it is gone."""
    for attempt in range(_REMOVE_ATTEMPTS):
        if not path.exists():
            return True
        try:
            shutil.rmtree(path, onexc=_clear_read_only)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt + 1 < _REMOVE_ATTEMPTS:
                time.sleep(_REMOVE_PAUSE_S)
    return not path.exists()


def _clear_read_only(function, path, _exc) -> None:
    """A read-only file stops ``rmtree`` on Windows; clear the flag and retry."""
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IWRITE)
    function(path)


def _checkpoint(label: str) -> None:
    if interruption_hook is not None:
        interruption_hook(label)


__all__ = [
    "CAUSES",
    "CAUSE_GONE",
    "CAUSE_OTHER",
    "IN_USE_REFUSAL",
    "RECORDING_REFUSAL",
    "RUNNING_REFUSAL",
    "TOMBSTONE_PREFIX",
    "DeletionFile",
    "DeletionOutcome",
    "DeletionPreview",
    "cause_of",
    "delete_meeting",
    "delete_meetings",
    "deletion_blocker",
    "preview_deletion",
    "resolve_tombstones",
    "tombstone_of",
]
