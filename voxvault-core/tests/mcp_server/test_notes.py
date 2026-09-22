"""Notes through the MCP surface.

The rule these guard: an agent may write a note and may not touch the
transcript. And every write takes an identifier, so a listing or a creation
has to hand one back -- otherwise update and delete are operations nobody can
reach.
"""

from __future__ import annotations

import pytest

from voxvault.types import Track

from .conftest import call


def _note(server, meeting_uid: str, *, tipo: str = "resumo", conteudo: str = "Resumo."):
    return call(server, "criar_nota", reuniao=meeting_uid, tipo=tipo, conteudo=conteudo)


def test_creating_returns_an_identifier_that_works(server, make_meeting) -> None:
    """Scenario: Identificador devolvido na criacao."""
    make_meeting("r1")
    created = _note(server, "r1", conteudo="Primeira leitura.")

    assert created["id"]
    # That identifier alone must be enough to read, change and remove it.
    assert call(server, "ler_nota", nota=created["id"])["conteudo"] == "Primeira leitura."
    call(server, "atualizar_nota", nota=created["id"], conteudo="Segunda leitura.")
    assert call(server, "ler_nota", nota=created["id"])["conteudo"] == "Segunda leitura."
    call(server, "remover_nota", nota=created["id"])
    assert call(server, "listar_notas", reuniao="r1")["itens"] == []


def test_the_writing_client_is_recorded_as_the_author(server, make_meeting) -> None:
    """Whether to trust a summary later depends on what produced it."""
    make_meeting("r1")
    created = _note(server, "r1")
    assert "cliente-de-teste" in created["autoria"]
    assert "agente" in created["autoria"]


def test_listing_carries_what_is_needed_to_act(server, make_meeting) -> None:
    """Scenario: Listagem das notas de uma reuniao."""
    make_meeting("r1")
    _note(server, "r1", tipo="resumo", conteudo="O que foi decidido.")
    _note(server, "r1", tipo="pendencias", conteudo="Quem faz o que.")

    itens = call(server, "listar_notas", reuniao="r1")["itens"]

    assert len(itens) == 2
    for item in itens:
        assert item["id"]
        assert item["tipo"]
        assert item["autoria"]
        assert item["criada_em"] and item["alterada_em"]
        assert item["recorte"]


def test_two_notes_of_the_same_kind_coexist(server, make_meeting) -> None:
    make_meeting("r1")
    first = _note(server, "r1", tipo="resumo", conteudo="Primeira.")
    second = _note(server, "r1", tipo="resumo", conteudo="Segunda.")

    assert first["id"] != second["id"]
    assert len(call(server, "listar_notas", reuniao="r1")["itens"]) == 2


def test_updating_preserves_identity_and_authorship(server, make_meeting) -> None:
    make_meeting("r1")
    created = _note(server, "r1", conteudo="Antes.")
    updated = call(server, "atualizar_nota", nota=created["id"], conteudo="Depois.")

    assert updated["id"] == created["id"]
    assert updated["autoria"] == created["autoria"]
    assert updated["criada_em"] == created["criada_em"]


def test_removing_one_note_leaves_the_others(server, make_meeting) -> None:
    make_meeting("r1")
    keep = _note(server, "r1", tipo="resumo", conteudo="Fica.")
    drop = _note(server, "r1", tipo="pendencias", conteudo="Sai.")

    call(server, "remover_nota", nota=drop["id"])

    remaining = call(server, "listar_notas", reuniao="r1")["itens"]
    assert [n["id"] for n in remaining] == [keep["id"]]


def test_the_meeting_listing_counts_notes(server, make_meeting) -> None:
    make_meeting("r1")
    assert call(server, "listar_reunioes")["itens"][0]["notas"] == 0
    _note(server, "r1")
    assert call(server, "listar_reunioes")["itens"][0]["notas"] == 1


def test_a_note_is_findable_and_carries_its_identifier(server, make_meeting) -> None:
    """A search result nobody can act on is a dead end."""
    make_meeting("r1")
    created = _note(server, "r1", conteudo="Definir a janela de manutencao na sexta.")

    hits = call(server, "buscar", termo="janela de manutencao")["itens"]
    notes = [h for h in hits if h["natureza"] == "nota"]

    assert notes, "a busca sem escopo tem de alcancar as notas"
    assert notes[0]["nota"] == created["id"]


def test_search_scopes_are_honoured(server, make_meeting) -> None:
    make_meeting("r1", segments=[(Track.MIC.value, 0, 1000, "assunto partilhado")])
    _note(server, "r1", conteudo="assunto partilhado tambem aqui")

    todos = call(server, "buscar", termo="partilhado", escopo="ambos")["itens"]
    so_notas = call(server, "buscar", termo="partilhado", escopo="notas")["itens"]
    so_texto = call(server, "buscar", termo="partilhado", escopo="transcricoes")["itens"]

    assert {h["natureza"] for h in todos} == {"transcricao", "nota"}
    assert {h["natureza"] for h in so_notas} == {"nota"}
    assert {h["natureza"] for h in so_texto} == {"transcricao"}


def test_a_note_result_does_not_invent_an_instant(server, make_meeting) -> None:
    """A note has no instant. Reporting zero would place interpretation at
    the start of the meeting as though someone had said it there."""
    make_meeting("r1")
    _note(server, "r1", conteudo="uma leitura posterior")

    hits = call(server, "buscar", termo="leitura", escopo="notas")["itens"]
    assert "inicio_ms" not in hits[0]
    assert "falante" not in hits[0]


def test_reprocessing_keeps_the_notes(server, store, make_meeting) -> None:
    """The property that makes notes worth writing at all."""
    from voxvault.types import Segment

    from .conftest import ENGINE

    make_meeting("r1")
    created = _note(server, "r1", conteudo="Vale depois do reprocessamento.")

    revision = store.begin_revision("r1", engine=ENGINE, language="pt")
    store.add_segments(revision.id, Track.MIC.value, [Segment(0, 1000, "novo texto")])
    store.add_segments(revision.id, Track.SYSTEM.value, [Segment(2000, 3000, "outro")])
    store.publish_revision(
        revision.id, tracks_ok=[Track.MIC.value, Track.SYSTEM.value]
    )

    itens = call(server, "listar_notas", reuniao="r1")["itens"]
    assert [n["id"] for n in itens] == [created["id"]]


def test_a_long_note_is_paged_by_position(server, make_meeting) -> None:
    """Scenario: Leitura integral de uma nota longa."""
    from voxvault.mcp.pagination import MAX_RESPONSE_CHARS

    make_meeting("r1")
    content = "".join(f"{n:06d} " for n in range(MAX_RESPONSE_CHARS // 4))
    created = _note(server, "r1", conteudo=content)

    collected = ""
    cursor = ""
    for _ in range(10):
        payload = call(server, "ler_nota", nota=created["id"], cursor=cursor)
        collected += payload["conteudo"]
        if not payload["ha_mais"]:
            break
        cursor = payload["proximo_cursor"]

    assert collected == content.strip() or collected.strip() == content.strip()


def test_an_unknown_note_names_the_identifier(server, make_meeting) -> None:
    make_meeting("r1")
    with pytest.raises(Exception) as caught:
        call(server, "ler_nota", nota="nao-existe")
    assert "nao-existe" in str(caught.value)


def test_notes_never_touch_the_transcript(server, store, make_meeting) -> None:
    make_meeting("r1")
    before = [(e.start_ms, e.text) for e in store.timeline("r1")]

    created = _note(server, "r1", conteudo="uma leitura")
    call(server, "atualizar_nota", nota=created["id"], conteudo="outra leitura")
    call(server, "remover_nota", nota=created["id"])

    assert [(e.start_ms, e.text) for e in store.timeline("r1")] == before


def test_a_note_written_through_mcp_reaches_the_export(server, store, make_meeting) -> None:
    """Export files track the revision, so a note-only change is invisible to
    the staleness signal. Every note write has to rebuild them, or what is on
    disk drifts from what the database holds without anything noticing."""
    from voxvault.store import export_paths

    meeting = make_meeting("r1")
    readable, _structured = export_paths(meeting)

    created = _note(server, "r1", tipo="resumo", conteudo="Decidimos adiar o deploy.")
    assert readable.is_file()
    assert "adiar o deploy" in readable.read_text(encoding="utf-8")

    call(server, "atualizar_nota", nota=created["id"], conteudo="Decidimos seguir.")
    assert "Decidimos seguir" in readable.read_text(encoding="utf-8")
    assert "adiar o deploy" not in readable.read_text(encoding="utf-8")

    call(server, "remover_nota", nota=created["id"])
    assert "Decidimos seguir" not in readable.read_text(encoding="utf-8")
