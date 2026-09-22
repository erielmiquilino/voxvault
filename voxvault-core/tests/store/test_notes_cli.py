"""Task 1.10: the four note commands, run end to end against a real database.

Placed beside the storage tests because that is where the notes fixtures and
the real-database conventions of this package live; what it exercises is the
command line, through ``main`` with a real ``argv`` and a real ``--data-dir``.

Nothing is mocked: each test runs the command the user would type, then opens
the database and checks what the command actually did.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from voxvault.cli.__main__ import main
from voxvault.store import (
    READABLE_NAME,
    NoteAuthorKind,
    NoteKind,
    TranscriptStore,
    regenerate_exports,
)
from voxvault.types import EngineInfo, Segment, Track

BASE_TIME = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)


@pytest.fixture
def data_dir(tmp_path: Path, engine: EngineInfo) -> Path:
    """A data directory holding one transcribed meeting, as the CLI expects."""
    root = tmp_path / "dados"
    root.mkdir()
    directory = root / "recordings" / "reuniao-1"
    directory.mkdir(parents=True)
    with TranscriptStore(root / "voxvault.db") as store:
        store.create_meeting(
            uid="reuniao-1",
            title="Planejamento",
            started_at=BASE_TIME,
            directory=directory,
        )
        revision = store.begin_revision("reuniao-1", engine=engine)
        store.add_segments(
            revision.id, Track.MIC, [Segment(0, 2000, "vamos revisar o orçamento")]
        )
        store.add_segments(
            revision.id, Track.SYSTEM, [Segment(2500, 4000, "fechado")]
        )
        store.publish_revision(
            revision.id, tracks_ok=[Track.MIC, Track.SYSTEM]
        )
        regenerate_exports(store, "reuniao-1")
    return root


def run(data_dir: Path, *args: str) -> int:
    return main(["--data-dir", str(data_dir), *args])


def notes_in(data_dir: Path) -> list:
    with TranscriptStore(data_dir / "voxvault.db") as store:
        return store.notes_of("reuniao-1")


def test_notes_add_creates_a_note_attributed_to_the_user(
    data_dir: Path, capsys
) -> None:
    assert run(
        data_dir, "notes", "add", "reuniao-1",
        "--type", "resumo",
        "--content", "O orcamento fecha na sexta.",
    ) == 0

    notes = notes_in(data_dir)
    assert len(notes) == 1
    assert notes[0].kind is NoteKind.SUMMARY
    assert notes[0].content == "O orcamento fecha na sexta."
    # A note typed at the terminal came from the person, not from a client.
    assert notes[0].author.kind is NoteAuthorKind.USER

    out = capsys.readouterr().out
    assert notes[0].uid in out


def test_notes_add_accepts_a_meeting_prefix_like_the_other_commands(
    data_dir: Path,
) -> None:
    assert run(
        data_dir, "notes", "add", "reuni",
        "--type", "pendencias", "--content", "Falar com o financeiro.",
    ) == 0
    assert len(notes_in(data_dir)) == 1


def test_notes_add_refuses_an_unknown_type_naming_the_accepted_ones(
    data_dir: Path, capsys
) -> None:
    assert run(
        data_dir, "notes", "add", "reuniao-1",
        "--type", "ata", "--content", "qualquer coisa",
    ) == 1
    err = capsys.readouterr().err
    assert "ata" in err
    for kind in NoteKind:
        assert kind.value in err
    assert notes_in(data_dir) == []


def test_notes_list_shows_every_note_with_type_and_authorship(
    data_dir: Path, capsys
) -> None:
    run(data_dir, "notes", "add", "reuniao-1", "--type", "resumo",
        "--content", "Primeira leitura.")
    run(data_dir, "notes", "add", "reuniao-1", "--type", "pendencias",
        "--content", "Confirmar com o financeiro.")
    capsys.readouterr()

    assert run(data_dir, "notes", "list", "reuniao-1") == 0
    out = capsys.readouterr().out
    assert "resumo" in out
    assert "pendencias" in out
    assert "usuario" in out
    assert "Primeira leitura." in out
    for note in notes_in(data_dir):
        assert note.uid[:8] in out


def test_notes_list_on_a_meeting_without_notes_says_so(
    data_dir: Path, capsys
) -> None:
    assert run(data_dir, "notes", "list", "reuniao-1") == 0
    assert "Nenhuma nota" in capsys.readouterr().out


def test_notes_update_replaces_the_content_by_a_prefix_of_the_identifier(
    data_dir: Path, capsys
) -> None:
    run(data_dir, "notes", "add", "reuniao-1", "--type", "resumo",
        "--content", "Versao inicial.")
    note = notes_in(data_dir)[0]
    capsys.readouterr()

    assert run(
        data_dir, "notes", "update", note.uid[:8], "--content", "Versao corrigida."
    ) == 0

    after = notes_in(data_dir)
    assert len(after) == 1
    assert after[0].uid == note.uid
    assert after[0].content == "Versao corrigida."
    assert after[0].author == note.author
    assert after[0].created_at_ms == note.created_at_ms


def test_notes_remove_deletes_only_that_note(data_dir: Path, capsys) -> None:
    run(data_dir, "notes", "add", "reuniao-1", "--type", "resumo",
        "--content", "Fica.")
    run(data_dir, "notes", "add", "reuniao-1", "--type", "livre",
        "--content", "Sai.")
    doomed = next(n for n in notes_in(data_dir) if n.content == "Sai.")
    capsys.readouterr()

    assert run(data_dir, "notes", "remove", doomed.uid) == 0

    remaining = notes_in(data_dir)
    assert [n.content for n in remaining] == ["Fica."]


def test_an_unknown_note_identifier_fails_naming_it(
    data_dir: Path, capsys
) -> None:
    assert run(data_dir, "notes", "remove", "deadbeef") == 1
    assert "deadbeef" in capsys.readouterr().err


def test_a_note_written_from_the_terminal_reaches_the_export(
    data_dir: Path,
) -> None:
    """The exports carry the notes, so a note command has to refresh them.

    Otherwise the file on disk keeps describing a meeting that no longer has
    the notes it has -- stale in a way nothing else would detect, because the
    revision it was generated from has not changed.
    """
    run(data_dir, "notes", "add", "reuniao-1", "--type", "resumo",
        "--content", "Resumo escrito no terminal.")

    readable = data_dir / "recordings" / "reuniao-1" / READABLE_NAME
    assert "Resumo escrito no terminal." in readable.read_text(encoding="utf-8")

    note = notes_in(data_dir)[0]
    run(data_dir, "notes", "remove", note.uid)
    assert "Resumo escrito no terminal." not in readable.read_text(encoding="utf-8")


def test_the_help_text_lists_exactly_the_note_types_the_store_accepts() -> None:
    """The parser spells the types out to avoid importing the store to build
    itself. This is what keeps that copy honest."""
    from voxvault.cli.__main__ import _build_parser

    parser = _build_parser()
    action = next(
        a for a in parser._subparsers._group_actions if a.dest == "command"
    )
    add = action.choices["notes"]._subparsers._group_actions[0].choices["add"]
    help_text = next(a for a in add._actions if a.dest == "type").help
    for kind in NoteKind:
        assert kind.value in help_text, f"'{kind.value}' sumiu da ajuda"


def test_the_note_commands_do_not_touch_the_transcript(data_dir: Path) -> None:
    with TranscriptStore(data_dir / "voxvault.db") as store:
        before = [
            (e.segment_id, e.start_ms, str(e.speaker), e.text)
            for e in store.timeline("reuniao-1")
        ]
        revision = store.active_revision("reuniao-1").uid

    run(data_dir, "notes", "add", "reuniao-1", "--type", "decisoes",
        "--content", "Aprovado.")
    note = notes_in(data_dir)[0]
    run(data_dir, "notes", "update", note.uid, "--content", "Aprovado por todos.")
    run(data_dir, "notes", "remove", note.uid)

    with TranscriptStore(data_dir / "voxvault.db") as store:
        after = [
            (e.segment_id, e.start_ms, str(e.speaker), e.text)
            for e in store.timeline("reuniao-1")
        ]
        assert after == before
        assert store.active_revision("reuniao-1").uid == revision
