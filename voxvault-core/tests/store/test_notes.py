"""Task group 1: notes as a record that lives beside the transcript.

The properties here are the ones that make a note worth writing at all: that a
second opinion does not destroy the first, that editing one keeps its identity
and its authorship, and that re-transcribing a meeting -- which rewrites every
segment it has -- leaves every note standing.

Real databases throughout, as everywhere else in this package.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from conftest import make_meeting, transcribe

from voxvault.errors import StorageError
from voxvault.store import (
    NoteAuthor,
    NoteAuthorKind,
    NoteKind,
    TranscriptStore,
)
from voxvault.types import EngineInfo, Segment


@pytest.fixture
def meeting(store: TranscriptStore, tmp_path: Path, engine: EngineInfo):
    """One transcribed meeting, which is what a note is written about."""
    make_meeting(store, tmp_path, uid="reuniao-1", title="Planejamento")
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 2000, "precisamos decidir o orçamento")],
        system=[Segment(2500, 4000, "concordo, fecha na sexta")],
    )
    return store


AGENT = NoteAuthor.agent("claude-desktop")


# -- the model -----------------------------------------------------------


def test_a_note_written_by_an_agent_records_type_content_and_which_client(
    meeting: TranscriptStore,
) -> None:
    note = meeting.create_note(
        "reuniao-1",
        kind=NoteKind.SUMMARY,
        content="A equipe fechou o orcamento na sexta.",
        author=AGENT,
    )
    assert note.kind is NoteKind.SUMMARY
    assert note.content == "A equipe fechou o orcamento na sexta."
    assert note.author.kind is NoteAuthorKind.AGENT
    assert note.author.client == "claude-desktop"
    assert note.meeting_uid == "reuniao-1"
    assert note.uid
    # Both instants are recorded, and a fresh note has not been altered.
    assert note.created_at_ms > 0
    assert note.updated_at_ms == note.created_at_ms


def test_every_declared_note_type_is_accepted(meeting: TranscriptStore) -> None:
    for kind in NoteKind:
        note = meeting.create_note(
            "reuniao-1", kind=kind, content=f"conteudo de {kind}", author=AGENT
        )
        assert note.kind is kind
    assert len(meeting.notes_of("reuniao-1")) == len(NoteKind)


def test_an_unknown_type_is_refused_and_the_message_lists_what_is_accepted(
    meeting: TranscriptStore,
) -> None:
    with pytest.raises(StorageError) as caught:
        meeting.create_note(
            "reuniao-1", kind="ata", content="qualquer coisa", author=AGENT
        )
    message = str(caught.value)
    assert "ata" in message
    for kind in NoteKind:
        assert kind.value in message
    assert meeting.notes_of("reuniao-1") == []


def test_the_database_itself_refuses_a_type_it_does_not_know(
    meeting: TranscriptStore,
) -> None:
    """The closed set is a CHECK constraint, not only a Python guard.

    A future build that forgets the guard must still be unable to write a
    note type that no reader knows how to present.
    """
    with pytest.raises(sqlite3.IntegrityError):
        meeting._conn.execute(
            "INSERT INTO notes(uid, meeting_id, kind, content, author_kind,"
            " author_client, created_at_ms, updated_at_ms)"
            " VALUES ('x', 1, 'ata', 'texto', 'usuario', '', 1, 1)"
        )


def test_an_automated_author_must_name_its_client(meeting: TranscriptStore) -> None:
    with pytest.raises(ValueError):
        NoteAuthor(NoteAuthorKind.AGENT, "")
    # And the database says the same thing about a row built around the class.
    with pytest.raises(sqlite3.IntegrityError):
        meeting._conn.execute(
            "INSERT INTO notes(uid, meeting_id, kind, content, author_kind,"
            " author_client, created_at_ms, updated_at_ms)"
            " VALUES ('x', 1, 'resumo', 'texto', 'agente', '', 1, 1)"
        )


def test_a_note_for_a_meeting_that_does_not_exist_fails_naming_it(
    meeting: TranscriptStore,
) -> None:
    with pytest.raises(StorageError) as caught:
        meeting.create_note(
            "reuniao-fantasma", kind="resumo", content="texto", author=AGENT
        )
    assert "reuniao-fantasma" in str(caught.value)
    # "and no note is created": not for that meeting, and not anywhere.
    assert meeting._conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 0


def test_empty_content_is_refused(meeting: TranscriptStore) -> None:
    with pytest.raises(StorageError):
        meeting.create_note(
            "reuniao-1", kind="livre", content="   \n  ", author=AGENT
        )
    assert meeting.notes_of("reuniao-1") == []


# -- several notes of one type -------------------------------------------


def test_two_summaries_coexist_and_the_second_does_not_overwrite_the_first(
    meeting: TranscriptStore,
) -> None:
    first = meeting.create_note(
        "reuniao-1", kind="resumo", content="Primeira leitura da reuniao.",
        author=AGENT,
    )
    second = meeting.create_note(
        "reuniao-1", kind="resumo", content="Segunda leitura, mais completa.",
        author=NoteAuthor.user(),
    )

    assert first.uid != second.uid
    notes = meeting.notes_of("reuniao-1", kind="resumo")
    assert len(notes) == 2
    assert [n.content for n in notes] == [
        "Primeira leitura da reuniao.",
        "Segunda leitura, mais completa.",
    ]
    # The first one is still exactly what it was, byte for byte and author
    # for author.
    assert meeting.get_note(first.uid) == first


def test_notes_are_listed_oldest_first(meeting: TranscriptStore) -> None:
    uids = []
    for index in range(4):
        time.sleep(0.002)
        uids.append(
            meeting.create_note(
                "reuniao-1", kind="livre", content=f"nota {index}", author=AGENT
            ).uid
        )
    assert [n.uid for n in meeting.notes_of("reuniao-1")] == uids


def test_notes_of_an_unknown_meeting_fails_naming_it(
    meeting: TranscriptStore,
) -> None:
    with pytest.raises(StorageError) as caught:
        meeting.notes_of("reuniao-fantasma")
    assert "reuniao-fantasma" in str(caught.value)


# -- updating ------------------------------------------------------------


def test_updating_keeps_the_identifier_and_the_original_authorship(
    meeting: TranscriptStore,
) -> None:
    note = meeting.create_note(
        "reuniao-1", kind="resumo", content="Versao inicial.", author=AGENT
    )
    time.sleep(0.005)
    updated = meeting.update_note(note.uid, content="Versao corrigida.")

    assert updated.uid == note.uid
    assert updated.content == "Versao corrigida."
    assert updated.kind is note.kind
    # The authorship is the one that wrote it, not the one that edited it.
    assert updated.author == note.author
    assert updated.created_at_ms == note.created_at_ms
    # And the last-changed instant really moved.
    assert updated.updated_at_ms > note.updated_at_ms

    assert [n.uid for n in meeting.notes_of("reuniao-1")] == [note.uid]


def test_updating_a_note_that_does_not_exist_fails_naming_the_identifier(
    meeting: TranscriptStore,
) -> None:
    with pytest.raises(StorageError) as caught:
        meeting.update_note("nao-existe-esse-id", content="qualquer coisa")
    assert "nao-existe-esse-id" in str(caught.value)


def test_updating_one_note_leaves_the_others_untouched(
    meeting: TranscriptStore,
) -> None:
    notes = [
        meeting.create_note(
            "reuniao-1", kind="livre", content=f"texto {i}", author=AGENT
        )
        for i in range(3)
    ]
    time.sleep(0.005)
    meeting.update_note(notes[1].uid, content="texto trocado")

    assert meeting.get_note(notes[0].uid) == notes[0]
    assert meeting.get_note(notes[2].uid) == notes[2]


# -- removing ------------------------------------------------------------


def test_removing_one_of_three_notes_leaves_the_other_two_intact(
    meeting: TranscriptStore,
) -> None:
    notes = [
        meeting.create_note(
            "reuniao-1",
            kind=kind,
            content=f"conteudo de {kind}",
            author=AGENT,
        )
        for kind in ("resumo", "decisoes", "pendencias")
    ]

    meeting.delete_note(notes[1].uid)

    assert meeting.get_note(notes[1].uid) is None
    remaining = meeting.notes_of("reuniao-1")
    assert [n.uid for n in remaining] == [notes[0].uid, notes[2].uid]
    assert remaining == [notes[0], notes[2]]
    # The index went with the row instead of rotting behind an inner join.
    assert meeting.orphan_note_index_rows() == []
    assert meeting.unindexed_notes() == []


def test_removing_a_note_that_does_not_exist_fails_naming_the_identifier(
    meeting: TranscriptStore,
) -> None:
    with pytest.raises(StorageError) as caught:
        meeting.delete_note("nao-existe-esse-id")
    assert "nao-existe-esse-id" in str(caught.value)


def test_a_unique_prefix_of_a_note_identifier_is_enough(
    meeting: TranscriptStore,
) -> None:
    note = meeting.create_note(
        "reuniao-1", kind="resumo", content="texto", author=AGENT
    )
    assert meeting.resolve_note_uid(note.uid[:8]) == note.uid
    with pytest.raises(StorageError):
        meeting.resolve_note_uid("zzzzzzzz")


# -- independence from the transcript ------------------------------------


def _snapshot(store: TranscriptStore, uid: str) -> dict:
    meeting = store.get_meeting(uid)
    revision = store.active_revision(uid)
    return {
        "titulo": meeting.title,
        "estado": str(meeting.state),
        "transcricao": str(meeting.transcript_state),
        "tentativa": meeting.attempt_state,
        "diretorio": meeting.directory,
        "revisao": revision.uid,
        "segmentos": [(e.segment_id, e.start_ms, e.speaker, e.text)
                      for e in store.timeline(uid)],
    }


def test_writing_changing_and_removing_notes_never_touches_the_transcript(
    meeting: TranscriptStore,
) -> None:
    before = _snapshot(meeting, "reuniao-1")

    note = meeting.create_note(
        "reuniao-1", kind="resumo", content="Uma leitura da reuniao.", author=AGENT
    )
    assert _snapshot(meeting, "reuniao-1") == before

    meeting.update_note(note.uid, content="Outra leitura.")
    assert _snapshot(meeting, "reuniao-1") == before

    meeting.delete_note(note.uid)
    assert _snapshot(meeting, "reuniao-1") == before


def test_reprocessing_replaces_the_segments_and_keeps_every_note(
    meeting: TranscriptStore, other_engine: EngineInfo
) -> None:
    """Task 1.8, and the reason notes are a table and not a column.

    Re-transcribing throws away every segment the meeting had. If notes went
    with them, a summary written today would evaporate the next time the user
    tried a better model -- which is exactly when they would want it most.
    """
    notes = [
        meeting.create_note(
            "reuniao-1", kind="resumo", content="O orcamento fecha na sexta.",
            author=AGENT,
        ),
        meeting.create_note(
            "reuniao-1", kind="pendencias", content="Confirmar com o financeiro.",
            author=NoteAuthor.user(),
        ),
    ]
    old_revision = meeting.active_revision("reuniao-1").uid

    transcribe(
        meeting,
        "reuniao-1",
        other_engine,
        mic=[Segment(0, 2000, "texto novo do microfone")],
        system=[Segment(2500, 4000, "texto novo do sistema")],
    )

    # The segments really were replaced.
    assert meeting.active_revision("reuniao-1").uid != old_revision
    assert [e.text for e in meeting.timeline("reuniao-1")] == [
        "texto novo do microfone",
        "texto novo do sistema",
    ]
    assert meeting.search("orçamento") == []

    # And every note came through untouched -- identifier, authorship, both
    # instants and the text.
    assert meeting.notes_of("reuniao-1") == notes
    assert meeting.unindexed_notes() == []
    assert meeting.orphan_note_index_rows() == []
    assert [
        h.note_uid
        for h in meeting.search("financeiro", scope="notas")
    ] == [notes[1].uid]
