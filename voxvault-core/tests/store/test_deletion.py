"""Deleting a meeting for good.

Covers the requirement "Exclusao definitiva de reuniao" of
specs/transcript-store. What is being protected is the one property a deletion
can break without anyone noticing: that nobody reading the store ever sees a
meeting listed whose files are gone, or files left behind by a meeting that no
longer exists.
"""

from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import BASE_TIME, transcribe

from voxvault.config import Config
from voxvault.layout import meeting_dir
from voxvault.store import (
    NoteAuthor,
    Origin,
    SearchScope,
    TranscriptStore,
    deletion,
    regenerate_exports,
)
from voxvault.store.deletion import (
    IN_USE_REFUSAL,
    RECORDING_REFUSAL,
    RUNNING_REFUSAL,
    TOMBSTONE_PREFIX,
    delete_meeting,
    delete_meetings,
    preview_deletion,
    resolve_tombstones,
)
from voxvault.types import AttemptState, EngineInfo, MeetingState, Segment


def _meeting_with_everything(
    store: TranscriptStore,
    data_dir: Path,
    engine: EngineInfo,
    uid: str = "reuniao-1",
    *,
    word: str = "orcamento",
    origin: Origin = Origin.RECORDED,
):
    """A meeting as a real one ends up: audio, two revisions, three notes,
    exports -- every place a deletion has to reach."""
    directory = meeting_dir(data_dir, uid)
    directory.mkdir(parents=True)
    (directory / "mic.flac").write_bytes(b"m" * 1000)
    (directory / "system.flac").write_bytes(b"s" * 2500)
    (directory / "metadados.json").write_text("{}", encoding="utf-8")
    store.create_meeting(
        uid=uid, title=f"Planejamento {uid}", started_at=BASE_TIME,
        directory=directory, origin=origin,
    )
    store.finish_meeting(
        uid, ended_at=BASE_TIME + timedelta(hours=1), duration_ms=3_600_000,
        state=MeetingState.RECORDED,
    )
    transcribe(
        store, uid, engine,
        mic=[Segment(0, 1000, f"primeira leitura do {word}")],
        system=[Segment(500, 1500, "combinado")],
    )
    transcribe(
        store, uid, engine,
        mic=[Segment(0, 1000, f"segunda leitura do {word}")],
        system=[Segment(500, 1500, "fechado")],
    )
    for kind, content in (
        ("resumo", f"resumo do {word}"),
        ("decisoes", f"decidimos o {word}"),
        ("livre", "anotacao solta"),
    ):
        store.create_note(uid, kind=kind, content=content, author=NoteAuthor.user())
    regenerate_exports(store, uid)
    return store.get_meeting(uid)


def _rows_of(store: TranscriptStore, meeting_id: int) -> dict[str, int]:
    conn = store._conn
    count = lambda sql: conn.execute(sql, (meeting_id,)).fetchone()[0]  # noqa: E731
    return {
        "meetings": count("SELECT COUNT(*) FROM meetings WHERE id = ?"),
        "revisions": count("SELECT COUNT(*) FROM revisions WHERE meeting_id = ?"),
        "segments": count("SELECT COUNT(*) FROM segments WHERE meeting_id = ?"),
        "notes": count("SELECT COUNT(*) FROM notes WHERE meeting_id = ?"),
        "segments_fts": count("SELECT COUNT(*) FROM segments_fts WHERE meeting_id = ?"),
        "notes_fts": count("SELECT COUNT(*) FROM notes_fts WHERE meeting_id = ?"),
    }


def _dump(store: TranscriptStore) -> str:
    return "\n".join(store._conn.iterdump())


def _files_on_disk(directory: Path) -> list[tuple[str, int]]:
    return sorted(
        (p.relative_to(directory).as_posix(), p.stat().st_size)
        for p in directory.rglob("*")
        if p.is_file()
    )


@pytest.fixture(autouse=True)
def _no_leftover_hook():
    yield
    deletion.interruption_hook = None


# -- preview -----------------------------------------------------------

def test_the_preview_describes_everything_and_writes_nothing(
    store, tmp_path, engine
) -> None:
    """Scenario: Previa antes de excluir."""
    meeting = _meeting_with_everything(store, tmp_path, engine)
    directory = Path(meeting.directory)
    before, changes = _dump(store), store._conn.total_changes

    preview = preview_deletion(store, meeting.uid)

    assert preview.title == meeting.title
    assert preview.started_at_ms == meeting.started_at_ms
    assert preview.duration_ms == 3_600_000
    assert preview.revisions == 2
    assert preview.notes == 3
    assert preview.blocker == ""
    assert [(f.path, f.size) for f in preview.files] == _files_on_disk(directory)
    assert {"mic.flac", "system.flac", "transcricao.md", "transcricao.json"} <= {
        f.path for f in preview.files
    }
    assert preview.size == sum(size for _, size in _files_on_disk(directory))
    assert store._conn.total_changes == changes
    assert _dump(store) == before
    assert _rows_of(store, meeting.id)["meetings"] == 1


# -- the transaction ---------------------------------------------------

def test_nothing_of_a_deleted_meeting_is_left_in_the_store(
    store, tmp_path, engine
) -> None:
    """Scenario: Exclusao de uma reuniao transcrita com notas."""
    meeting = _meeting_with_everything(store, tmp_path, engine, "reuniao-1")
    other = _meeting_with_everything(store, tmp_path, engine, "reuniao-2")
    assert _rows_of(store, meeting.id)["revisions"] == 2
    assert _rows_of(store, meeting.id)["notes"] == 3

    outcome = delete_meeting(store, meeting.uid)

    assert outcome.deleted, outcome.reason
    assert _rows_of(store, meeting.id) == dict.fromkeys(_rows_of(store, meeting.id), 0)
    assert store.get_meeting(meeting.uid) is None
    hits = store.search("orcamento", scope=SearchScope.BOTH)
    assert hits, "a outra reuniao ainda tem de ser encontrada"
    assert {h.meeting_uid for h in hits} == {other.uid}
    assert store.orphan_index_rows() == []
    assert store.orphan_note_index_rows() == []
    assert _rows_of(store, other.id)["notes"] == 3


def test_a_failure_inside_the_transaction_removes_no_row(
    store, tmp_path, engine
) -> None:
    """The transaction is the unit: halfway through, nothing has happened."""
    meeting = _meeting_with_everything(store, tmp_path, engine)
    rows = _rows_of(store, meeting.id)

    def fail(label: str) -> None:
        if label == "purge_after_notes":
            raise RuntimeError("falha simulada no meio da transacao")

    store.fault_hook = fail
    outcome = delete_meeting(store, meeting.uid)
    store.fault_hook = None

    assert not outcome.deleted
    assert "falha simulada" in outcome.reason
    assert _rows_of(store, meeting.id) == rows
    assert Path(meeting.directory).is_dir(), "a pasta tem de voltar para a reuniao"
    assert not list(tmp_path.glob(f"recordings/{TOMBSTONE_PREFIX}*"))


# -- the tombstone choreography ----------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="compartilhamento de arquivos do Windows")
def test_a_file_held_open_refuses_and_changes_nothing(
    store, tmp_path, engine
) -> None:
    """Scenario: Arquivo da reuniao em uso."""
    meeting = _meeting_with_everything(store, tmp_path, engine)
    directory = Path(meeting.directory)
    files, before = _files_on_disk(directory), _dump(store)

    with open(directory / "mic.flac", "rb"):
        outcome = delete_meeting(store, meeting.uid)

    assert not outcome.deleted
    assert outcome.reason == IN_USE_REFUSAL
    assert outcome.cause == "em_uso"
    assert _dump(store) == before
    assert _files_on_disk(directory) == files
    assert not list(tmp_path.glob(f"recordings/{TOMBSTONE_PREFIX}*"))


def test_a_refusal_inside_the_transaction_puts_the_directory_back(
    store, tmp_path, engine
) -> None:
    """The state changes between the check and the lock: the lock wins."""
    meeting = _meeting_with_everything(store, tmp_path, engine)
    directory = Path(meeting.directory)
    files = _files_on_disk(directory)

    def claimed_meanwhile(label: str) -> None:
        if label == "depois_de_renomear":
            # What the queue does when it takes the meeting at this instant.
            store.set_attempt_state(meeting.uid, AttemptState.QUEUED)
            assert store.claim_queued(meeting.uid)

    deletion.interruption_hook = claimed_meanwhile
    outcome = delete_meeting(store, meeting.uid)

    assert not outcome.deleted
    assert outcome.reason == RUNNING_REFUSAL
    assert _files_on_disk(directory) == files
    assert not list(tmp_path.glob(f"recordings/{TOMBSTONE_PREFIX}*"))
    assert _rows_of(store, meeting.id)["revisions"] == 2


def test_a_deletion_leaves_neither_the_directory_nor_a_tombstone(
    store, tmp_path, engine
) -> None:
    meeting = _meeting_with_everything(store, tmp_path, engine)

    outcome = delete_meeting(store, meeting.uid)

    assert outcome.deleted
    assert not Path(meeting.directory).exists()
    assert list((tmp_path / "recordings").iterdir()) == []


# -- refusals ----------------------------------------------------------

@pytest.mark.parametrize("state", [MeetingState.RECORDING, MeetingState.PAUSED])
def test_a_meeting_being_recorded_is_refused_whole(
    store, tmp_path, engine, state
) -> None:
    """Scenario: Exclusao recusada durante a gravacao."""
    meeting = _meeting_with_everything(store, tmp_path, engine)
    store.finish_meeting(
        meeting.uid, ended_at=BASE_TIME, duration_ms=1000, state=state
    )
    files, rows = _files_on_disk(Path(meeting.directory)), _rows_of(store, meeting.id)

    outcome = delete_meeting(store, meeting.uid)

    assert not outcome.deleted
    assert outcome.reason == RECORDING_REFUSAL
    assert outcome.cause == "gravando"
    assert preview_deletion(store, meeting.uid).blocker == RECORDING_REFUSAL
    assert _files_on_disk(Path(meeting.directory)) == files
    assert _rows_of(store, meeting.id) == rows


def test_a_meeting_being_transcribed_is_refused_whole(
    store, tmp_path, engine
) -> None:
    """Scenario: Exclusao recusada durante a transcricao."""
    meeting = _meeting_with_everything(store, tmp_path, engine)
    store.set_attempt_state(meeting.uid, AttemptState.RUNNING)
    files, rows = _files_on_disk(Path(meeting.directory)), _rows_of(store, meeting.id)

    outcome = delete_meeting(store, meeting.uid)

    assert not outcome.deleted
    assert outcome.reason == RUNNING_REFUSAL
    assert _files_on_disk(Path(meeting.directory)) == files
    assert _rows_of(store, meeting.id) == rows
    assert str(store.get_meeting(meeting.uid).attempt_state) == AttemptState.RUNNING


def test_a_meeting_waiting_in_the_queue_can_be_deleted(
    store, tmp_path, engine
) -> None:
    meeting = _meeting_with_everything(store, tmp_path, engine)
    store.set_attempt_state(meeting.uid, AttemptState.QUEUED)

    outcome = delete_meeting(store, meeting.uid)

    assert outcome.deleted
    assert store.claim_queued(meeting.uid) is False


def test_an_unknown_meeting_is_refused_by_name(store) -> None:
    outcome = delete_meeting(store, "nao-existe")
    assert not outcome.deleted
    assert outcome.cause == "inexistente"
    assert "nao-existe" in outcome.reason
    assert outcome.preview is None


# -- imported media ----------------------------------------------------

def test_deleting_an_imported_meeting_leaves_the_original_alone(
    store, tmp_path, monkeypatch
) -> None:
    """Scenario: Reuniao importada."""
    from voxvault.engine import media
    from voxvault.session.importing import import_media

    monkeypatch.setattr(media, "probe_duration_ms", lambda path: 5_000)
    origin = tmp_path / "meus arquivos" / "entrevista.opus"
    origin.parent.mkdir()
    origin.write_bytes(os.urandom(4096))
    fingerprint = hashlib.sha256(origin.read_bytes()).hexdigest()
    config = Config(data_dir=tmp_path / "dados")

    uid, directory = import_media(config, origin, store=store)
    assert (directory / "source.opus").is_file()

    outcome = delete_meeting(store, uid)

    assert outcome.deleted
    assert not directory.exists()
    assert origin.is_file()
    assert hashlib.sha256(origin.read_bytes()).hexdigest() == fingerprint


# -- several at once ---------------------------------------------------

def test_several_meetings_are_each_deleted_or_refused_on_their_own(
    store, tmp_path, engine
) -> None:
    """Scenario: Varias reunioes com uma recusa."""
    first = _meeting_with_everything(store, tmp_path, engine, "reuniao-1")
    busy = _meeting_with_everything(store, tmp_path, engine, "reuniao-2")
    last = _meeting_with_everything(store, tmp_path, engine, "reuniao-3")
    store.set_attempt_state(busy.uid, AttemptState.RUNNING)
    busy_files, busy_rows = _files_on_disk(Path(busy.directory)), _rows_of(store, busy.id)

    outcomes = delete_meetings(store, [first.uid, busy.uid, last.uid])

    assert [(o.uid, o.deleted) for o in outcomes] == [
        (first.uid, True), (busy.uid, False), (last.uid, True),
    ]
    assert outcomes[1].reason == RUNNING_REFUSAL
    assert store.get_meeting(first.uid) is None
    assert store.get_meeting(last.uid) is None
    assert _files_on_disk(Path(busy.directory)) == busy_files
    assert _rows_of(store, busy.id) == busy_rows


# -- interrupted deletions ---------------------------------------------

class _Crash(BaseException):
    """What the process dying looks like from inside: nothing else runs."""


def _crash_at(point: str):
    def hook(label: str) -> None:
        if label == point:
            raise _Crash(point)
    return hook


def test_a_deletion_stopped_before_the_rows_is_undone(
    store, tmp_path, engine
) -> None:
    meeting = _meeting_with_everything(store, tmp_path, engine)
    files = _files_on_disk(Path(meeting.directory))
    deletion.interruption_hook = _crash_at("depois_de_renomear")
    with pytest.raises(_Crash):
        delete_meeting(store, meeting.uid)
    deletion.interruption_hook = None
    assert not Path(meeting.directory).exists()

    assert resolve_tombstones(store, tmp_path) == 1

    assert _files_on_disk(Path(meeting.directory)) == files
    assert store.get_meeting(meeting.uid) is not None
    assert not list(tmp_path.glob(f"recordings/{TOMBSTONE_PREFIX}*"))


def test_a_deletion_stopped_after_the_rows_is_finished(
    store, tmp_path, engine
) -> None:
    meeting = _meeting_with_everything(store, tmp_path, engine)
    deletion.interruption_hook = _crash_at("depois_do_banco")
    with pytest.raises(_Crash):
        delete_meeting(store, meeting.uid)
    deletion.interruption_hook = None
    assert list(tmp_path.glob(f"recordings/{TOMBSTONE_PREFIX}*"))

    assert resolve_tombstones(store, tmp_path) == 1

    assert list((tmp_path / "recordings").iterdir()) == []
    assert store.get_meeting(meeting.uid) is None


def test_exports_never_recreate_the_directory_of_a_deleted_meeting(
    store, tmp_path, engine
) -> None:
    """A regeneration that lands after the directory went must not bring a
    transcript back to disk."""
    from voxvault.errors import StorageError

    meeting = _meeting_with_everything(store, tmp_path, engine)
    directory = Path(meeting.directory)
    os.rename(directory, deletion.tombstone_of(directory))

    with pytest.raises(StorageError, match="nao existe mais"):
        regenerate_exports(store, meeting.uid)

    assert not directory.exists()
