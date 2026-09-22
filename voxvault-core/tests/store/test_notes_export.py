"""Task 1.9: notes in the exports, in a section that is not the timeline.

The rule being tested is not "the notes appear somewhere". It is that no line
of note text ever lands inside the transcribed timeline, so that a person
reading the file months later cannot mistake an interpretation for a record of
what was said.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_meeting, transcribe

from voxvault.store import (
    NOTES_HEADING,
    READABLE_NAME,
    STRUCTURED_NAME,
    STRUCTURED_VERSION,
    TIMELINE_HEADING,
    NoteAuthor,
    TranscriptStore,
    load_structured,
    notes_from_structured,
    regenerate_exports,
)
from voxvault.types import EngineInfo, Segment

AGENT = NoteAuthor.agent("claude-desktop")

RESUMO = "A equipe fechou o orcamento e marcou a revisao para sexta."
PENDENCIA = "Confirmar o numero final com o financeiro."


@pytest.fixture
def annotated(store: TranscriptStore, tmp_path: Path, engine: EngineInfo):
    """A meeting with a transcript, a summary and a list of pending items."""
    make_meeting(store, tmp_path, uid="reuniao-1", title="Revisao trimestral")
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(1000, 4000, "vamos revisar o orçamento")],
        system=[Segment(5000, 6000, "fechado, sexta entao")],
    )
    store.create_note("reuniao-1", kind="resumo", content=RESUMO, author=AGENT)
    store.create_note(
        "reuniao-1",
        kind="pendencias",
        content=PENDENCIA,
        author=NoteAuthor.user(),
    )
    regenerate_exports(store, "reuniao-1")
    return store


def readable(tmp_path: Path) -> str:
    return (tmp_path / "reuniao-1" / READABLE_NAME).read_text(encoding="utf-8")


# -- the readable export -------------------------------------------------


def test_the_readable_export_has_a_section_of_its_own_for_the_notes(
    annotated, tmp_path: Path
) -> None:
    text = readable(tmp_path)
    assert NOTES_HEADING in text
    assert TIMELINE_HEADING in text
    assert RESUMO in text
    assert PENDENCIA in text


def test_each_note_is_identified_by_type_and_by_authorship(
    annotated, tmp_path: Path
) -> None:
    text = readable(tmp_path)
    notes_block = text.split(NOTES_HEADING, 1)[1].split(TIMELINE_HEADING, 1)[0]
    assert "resumo" in notes_block
    assert "pendencias" in notes_block
    # Which client wrote it, not merely that something automated did.
    assert "agente claude-desktop" in notes_block
    assert "usuario" in notes_block


def test_no_note_text_appears_inside_the_timeline(annotated, tmp_path: Path) -> None:
    """The requirement, stated as the only assertion that can catch it.

    Everything after the timeline heading is what was said. A summary landing
    there is the failure the whole separation exists to prevent.
    """
    text = readable(tmp_path)
    timeline = text.split(TIMELINE_HEADING, 1)[1]

    assert RESUMO not in timeline
    assert PENDENCIA not in timeline
    assert "vamos revisar o orçamento" in timeline
    assert "fechado, sexta entao" in timeline

    # Every line of the timeline is a stamped segment and nothing else.
    spoken = [ln for ln in timeline.splitlines() if ln.strip()]
    assert spoken, "a linha de tempo ficou vazia"
    assert all(ln.startswith("[") for ln in spoken), spoken


def test_the_notes_section_comes_before_the_timeline_and_says_what_it_is(
    annotated, tmp_path: Path
) -> None:
    text = readable(tmp_path)
    assert text.index(NOTES_HEADING) < text.index(TIMELINE_HEADING)
    caption = text.split(NOTES_HEADING, 1)[1].split(TIMELINE_HEADING, 1)[0]
    assert "Interpretacao" in caption
    assert "linha de tempo" in caption


def test_an_edited_note_shows_that_it_was_altered(annotated, tmp_path: Path) -> None:
    import time

    note = annotated.notes_of("reuniao-1")[0]
    time.sleep(0.005)
    annotated.update_note(note.uid, content="Leitura corrigida do encontro.")
    regenerate_exports(annotated, "reuniao-1")

    text = readable(tmp_path)
    assert "Leitura corrigida do encontro." in text
    assert RESUMO not in text
    assert "alterada em" in text


# -- the structured export -----------------------------------------------


def test_the_structured_export_carries_every_field_of_every_note(
    annotated, tmp_path: Path
) -> None:
    payload = load_structured(tmp_path / "reuniao-1" / STRUCTURED_NAME)
    stored = annotated.notes_of("reuniao-1")
    assert len(payload["notas"]) == 2

    for item, note in zip(payload["notas"], stored):
        assert item["uid"] == note.uid
        assert item["tipo"] == str(note.kind)
        assert item["conteudo"] == note.content
        assert item["autoria"]["tipo"] == str(note.author.kind)
        assert item["autoria"]["cliente"] == note.author.client
        assert item["criada_em_ms"] == note.created_at_ms
        assert item["alterada_em_ms"] == note.updated_at_ms
        assert item["criada_em"].startswith("20")


def test_the_structured_notes_round_trip_without_loss(
    annotated, tmp_path: Path
) -> None:
    payload = load_structured(tmp_path / "reuniao-1" / STRUCTURED_NAME)
    rebuilt = notes_from_structured(payload)
    stored = annotated.notes_of("reuniao-1")

    assert [n.uid for n in rebuilt] == [n.uid for n in stored]
    for back, note in zip(rebuilt, stored):
        assert back.kind is note.kind
        assert back.content == note.content
        assert back.author == note.author
        assert back.created_at_ms == note.created_at_ms
        assert back.updated_at_ms == note.updated_at_ms


def test_the_notes_never_leak_into_the_segments_of_the_structured_export(
    annotated, tmp_path: Path
) -> None:
    payload = load_structured(tmp_path / "reuniao-1" / STRUCTURED_NAME)
    texts = [s["texto"] for s in payload["segmentos"]]
    assert texts == ["vamos revisar o orçamento", "fechado, sexta entao"]
    assert RESUMO not in texts
    assert PENDENCIA not in texts


def test_the_structured_version_moved_when_notes_appeared(
    annotated, tmp_path: Path
) -> None:
    """A reader has to be able to tell "no notes" from "file predates notes"."""
    payload = load_structured(tmp_path / "reuniao-1" / STRUCTURED_NAME)
    assert payload["versao"] == STRUCTURED_VERSION == 2


# -- meetings with no notes ----------------------------------------------


def test_a_meeting_without_notes_still_exports(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path, uid="reuniao-1", title="Sem notas")
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 1000, "uma frase qualquer")],
        system=[Segment(2000, 3000, "outra frase")],
    )
    status = regenerate_exports(store, "reuniao-1")
    assert status.current is True

    text = readable(tmp_path)
    assert TIMELINE_HEADING in text
    assert "uma frase qualquer" in text
    # No empty section is emitted for nothing.
    assert NOTES_HEADING not in text

    payload = load_structured(tmp_path / "reuniao-1" / STRUCTURED_NAME)
    # But the key is always there, so a reader can tell the two cases apart.
    assert payload["notas"] == []
    assert notes_from_structured(payload) == []


def test_removing_the_last_note_removes_the_section(
    annotated, tmp_path: Path
) -> None:
    for note in annotated.notes_of("reuniao-1"):
        annotated.delete_note(note.uid)
    regenerate_exports(annotated, "reuniao-1")

    text = readable(tmp_path)
    assert NOTES_HEADING not in text
    assert RESUMO not in text
    assert "vamos revisar o orçamento" in text
    assert load_structured(tmp_path / "reuniao-1" / STRUCTURED_NAME)["notas"] == []
