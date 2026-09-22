"""The MCP tools, called the way a client calls them.

Covers specs/mcp-server/spec.md. The properties under test are the ones that
decide whether an agent can trust what it reads: every segment appears exactly
once across pages, a cursor is refused when its ground shifts, nothing is
truncated silently, and no tool can damage the record of what was said.
"""

from __future__ import annotations

import pytest

from voxvault.mcp.cursors import Tool, issue
from voxvault.mcp.pagination import MAX_RESPONSE_CHARS
from voxvault.types import Segment, Track

from .conftest import ENGINE, call

# -- the surface -------------------------------------------------------

def test_no_tool_can_damage_the_record(server) -> None:
    """The whole point of the boundary: an agent reads, and writes only notes.

    A transcript is evidence of a conversation that happened. An agent able to
    edit it would make it worthless as evidence.
    """
    import asyncio

    names = {t.name for t in asyncio.run(server.list_tools())}
    forbidden = {
        "remover_reuniao", "apagar_reuniao", "alterar_segmento",
        "editar_transcricao", "remover_audio", "iniciar_gravacao",
        "gravar", "reprocessar", "transcrever",
    }
    assert not (names & forbidden)
    # Everything that writes must be about notes, nothing else.
    writers = {n for n in names if n.startswith(("criar", "atualizar", "remover"))}
    assert all("nota" in n for n in writers), writers


def test_every_write_tool_has_a_read_path_for_its_identifiers(server) -> None:
    """Update and delete take a note id, so something must hand one out."""
    import asyncio

    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"atualizar_nota", "remover_nota"} <= names
    assert {"listar_notas", "ler_nota"} <= names, (
        "sem caminho de leitura, atualizar e remover seriam inalcancaveis"
    )


# -- listing -----------------------------------------------------------

def test_listing_reports_availability_and_attempt_separately(
    server, make_meeting
) -> None:
    make_meeting("r1")
    payload = call(server, "listar_reunioes")
    item = payload["itens"][0]

    assert item["transcricao_disponivel"] is True
    assert item["estado_da_tentativa"] == "nenhuma"
    assert {"id", "titulo", "inicio", "duracao_ms", "origem", "notas"} <= set(item)


def test_listing_is_newest_first(server, make_meeting) -> None:
    make_meeting("antiga", title="Antiga", minutes_ago=600)
    make_meeting("recente", title="Recente", minutes_ago=0)

    payload = call(server, "listar_reunioes")
    assert [i["id"] for i in payload["itens"]] == ["recente", "antiga"]


def test_listing_filters_by_title(server, make_meeting) -> None:
    make_meeting("a", title="Retrospectiva")
    make_meeting("b", title="Planejamento")

    payload = call(server, "listar_reunioes", titulo_contem="retro")
    assert [i["id"] for i in payload["itens"]] == ["a"]


def test_listing_paginates_without_loss_or_duplication(server, make_meeting) -> None:
    for index in range(7):
        make_meeting(f"r{index}", minutes_ago=index * 10)

    seen: list[str] = []
    cursor = ""
    for _ in range(10):
        payload = call(server, "listar_reunioes", tamanho_da_pagina=2, cursor=cursor)
        seen.extend(i["id"] for i in payload["itens"])
        if not payload["ha_mais"]:
            break
        cursor = payload["proximo_cursor"]

    assert len(seen) == 7
    assert len(set(seen)) == 7, "nenhuma reuniao pode aparecer duas vezes"


def test_a_meeting_created_during_listing_does_not_break_the_cursor(
    server, make_meeting
) -> None:
    """Scenario: Nova reuniao criada durante a listagem paginada."""
    for index in range(4):
        make_meeting(f"r{index}", minutes_ago=index * 10)

    first = call(server, "listar_reunioes", tamanho_da_pagina=2)
    make_meeting("nova", minutes_ago=1)

    second = call(
        server, "listar_reunioes", tamanho_da_pagina=2,
        cursor=first["proximo_cursor"],
    )
    assert second["itens"], "a leitura em curso tem de prosseguir sem erro"


# -- timeline ----------------------------------------------------------

def test_timeline_returns_speaker_and_instant(server, make_meeting) -> None:
    make_meeting("r1")
    payload = call(server, "ler_transcricao", reuniao="r1")

    assert payload["transcricao_disponivel"] is True
    assert len(payload["itens"]) == 3
    first = payload["itens"][0]
    assert {"inicio_ms", "fim_ms", "falante", "trilha", "texto"} <= set(first)
    assert first["falante"] in {"eu", "outros"}


def test_meeting_never_transcribed_is_not_an_error(server, make_meeting) -> None:
    """Scenario: Reuniao sem transcricao alguma."""
    make_meeting("r1", segments=[])

    payload = call(server, "ler_transcricao", reuniao="r1")

    assert payload["transcricao_disponivel"] is False
    assert payload["itens"] == []
    assert payload["tentativa_corrente"]["estado"] is not None
    assert "observacao" in payload


def test_reprocessing_keeps_the_previous_transcript_readable(
    server, store, make_meeting
) -> None:
    """Scenario: Reuniao transcrita e sendo reprocessada.

    The two attributes exist precisely for this: a complete transcript is
    available AND an attempt is running, at the same time.
    """
    make_meeting("r1")
    from voxvault.types import AttemptState

    store.set_attempt_state("r1", AttemptState.RUNNING)

    payload = call(server, "ler_transcricao", reuniao="r1")

    assert payload["transcricao_disponivel"] is True
    assert len(payload["itens"]) == 3, "o resultado anterior segue legivel"
    assert payload["tentativa_corrente"]["estado"] == "em_execucao"


def test_timeline_pages_cover_every_segment_exactly_once(
    server, store, make_meeting
) -> None:
    """Scenario: Leitura completa de uma transcricao longa."""
    segments = [
        (Track.MIC.value if n % 2 == 0 else Track.SYSTEM.value,
         n * 1000, n * 1000 + 800, f"segmento {n}")
        for n in range(50)
    ]
    make_meeting("r1", segments=segments)

    seen: list[str] = []
    cursor = ""
    for _ in range(40):
        payload = call(
            server, "ler_transcricao", reuniao="r1",
            tamanho_da_pagina=7, cursor=cursor,
        )
        seen.extend(i["texto"] for i in payload["itens"])
        if not payload["ha_mais"]:
            break
        cursor = payload["proximo_cursor"]

    assert len(seen) == 50
    assert len(set(seen)) == 50


def test_segments_sharing_a_start_instant_survive_a_page_boundary(
    server, make_meeting
) -> None:
    """Scenario: Segmentos com o mesmo instante de inicio na fronteira.

    Two tracks speaking in the same millisecond is the case a time-window
    pagination loses. The cursor's total ordering is what prevents it.
    """
    segments = [
        (Track.MIC.value, 1000, 2000, "eu falando"),
        (Track.SYSTEM.value, 1000, 2000, "outros falando"),
        (Track.MIC.value, 3000, 4000, "depois"),
    ]
    make_meeting("r1", segments=segments)

    first = call(server, "ler_transcricao", reuniao="r1", tamanho_da_pagina=1)
    assert first["ha_mais"] is True
    rest: list[str] = [i["texto"] for i in first["itens"]]

    cursor = first["proximo_cursor"]
    while cursor:
        payload = call(
            server, "ler_transcricao", reuniao="r1",
            tamanho_da_pagina=1, cursor=cursor,
        )
        rest.extend(i["texto"] for i in payload["itens"])
        cursor = payload["proximo_cursor"] if payload["ha_mais"] else ""

    assert sorted(rest) == sorted(["eu falando", "outros falando", "depois"])
    assert len(rest) == len(set(rest))


def test_cursor_is_refused_when_the_revision_changes(
    server, store, make_meeting
) -> None:
    """Scenario: Reprocessamento durante uma leitura paginada."""
    make_meeting("r1")
    first = call(server, "ler_transcricao", reuniao="r1", tamanho_da_pagina=1)
    cursor = first["proximo_cursor"]

    # Publish a new revision under the reader's feet.
    revision = store.begin_revision("r1", engine=ENGINE, language="pt")
    store.add_segments(revision.id, Track.MIC.value, [Segment(0, 1000, "novo")])
    store.add_segments(revision.id, Track.SYSTEM.value, [Segment(2000, 3000, "novo 2")])
    store.publish_revision(
        revision.id, tracks_ok=[Track.MIC.value, Track.SYSTEM.value]
    )

    with pytest.raises(Exception) as caught:
        call(server, "ler_transcricao", reuniao="r1", tamanho_da_pagina=1,
             cursor=cursor)
    assert "cursor" in str(caught.value).lower()


def test_a_cursor_from_another_tool_is_refused(server, make_meeting) -> None:
    """Scenario: Cursor usado na ferramenta errada."""
    make_meeting("r1")
    listing = call(server, "listar_reunioes", tamanho_da_pagina=1)
    foreign = listing.get("proximo_cursor") or issue(
        Tool.MEETINGS, (0, "x"), "estavel"
    )

    with pytest.raises(Exception, match=r"cursor|emitido"):
        call(server, "ler_transcricao", reuniao="r1", cursor=foreign)


# -- time slice --------------------------------------------------------

def test_slice_returns_overlapping_segments_only(server, make_meeting) -> None:
    """Scenario: Recorte por intervalo de tempo."""
    segments = [
        (Track.MIC.value, 0, 1000, "antes"),
        (Track.MIC.value, 900, 2000, "cruza a borda inicial"),
        (Track.MIC.value, 2000, 3000, "dentro"),
        (Track.MIC.value, 4900, 6000, "cruza a borda final"),
        (Track.MIC.value, 7000, 8000, "depois"),
    ]
    make_meeting("r1", segments=segments)

    payload = call(
        server, "recortar_transcricao", reuniao="r1",
        inicio_ms=1000, fim_ms=5000,
    )
    textos = [i["texto"] for i in payload["itens"]]

    assert "cruza a borda inicial" in textos
    assert "dentro" in textos
    assert "cruza a borda final" in textos
    assert "antes" not in textos
    assert "depois" not in textos


# -- size limits -------------------------------------------------------

def test_page_ends_at_the_last_whole_item_within_the_character_limit(
    server, make_meeting
) -> None:
    """Scenario: Pagina encerrada pelo limite de caracteres."""
    chunk = "palavra " * 2000  # ~16 000 characters each
    segments = [
        (Track.MIC.value, n * 1000, n * 1000 + 900, chunk) for n in range(10)
    ]
    make_meeting("r1", segments=segments)

    payload = call(server, "ler_transcricao", reuniao="r1", tamanho_da_pagina=200)

    assert payload["ha_mais"] is True, "o limite de caracteres tem de encerrar a pagina"
    total = sum(len(i["texto"]) for i in payload["itens"])
    assert total <= MAX_RESPONSE_CHARS
    assert all(i["texto"] == chunk for i in payload["itens"]), (
        "os itens da pagina tem de estar inteiros"
    )


def test_an_oversized_single_item_is_truncated_and_declared(
    server, make_meeting
) -> None:
    """Scenario: Segmento isolado maior que o limite.

    Omitting it would lose content; refusing it would wedge the pagination on
    an item it can never get past. So it is cut, and the cut is declared.
    """
    giant = "x" * (MAX_RESPONSE_CHARS + 5000)
    segments = [
        (Track.MIC.value, 0, 1000, giant),
        (Track.MIC.value, 2000, 3000, "o seguinte"),
    ]
    make_meeting("r1", segments=segments)

    payload = call(server, "ler_transcricao", reuniao="r1")

    assert payload["itens_truncados"] == 1
    assert payload["itens"][0]["truncado"] is True
    assert "truncado" in payload["itens"][0]["texto"]
    assert payload["ha_mais"] is True, "o cursor tem de avancar para o seguinte"
    assert "aviso" in payload, "nenhum truncamento pode ser silencioso"

    seguinte = call(
        server, "ler_transcricao", reuniao="r1",
        cursor=payload["proximo_cursor"],
    )
    assert [i["texto"] for i in seguinte["itens"]] == ["o seguinte"]


def test_page_size_is_capped(server, make_meeting) -> None:
    segments = [
        (Track.MIC.value, n * 100, n * 100 + 50, f"s{n}") for n in range(30)
    ]
    make_meeting("r1", segments=segments)
    payload = call(server, "ler_transcricao", reuniao="r1", tamanho_da_pagina=99999)
    assert len(payload["itens"]) <= 1000


# -- search ------------------------------------------------------------

def test_search_ignores_accents_in_both_directions(server, make_meeting) -> None:
    make_meeting("r1", segments=[
        (Track.MIC.value, 0, 1000, "vamos falar da migracao do banco"),
        (Track.MIC.value, 2000, 3000, "e da autenticação do módulo"),
    ])

    assert call(server, "buscar", termo="migração")["itens"]
    assert call(server, "buscar", termo="autenticacao")["itens"]


def test_search_result_carries_enough_to_decide(server, make_meeting) -> None:
    make_meeting("r1", title="Retrospectiva")
    payload = call(server, "buscar", termo="migracao")
    hit = payload["itens"][0]
    assert hit["reuniao"] == "r1"
    assert hit["titulo"] == "Retrospectiva"
    assert hit["recorte"]
    assert "natureza" in hit


def test_search_can_be_restricted_to_one_meeting(server, make_meeting) -> None:
    make_meeting("r1", segments=[(Track.MIC.value, 0, 1000, "assunto comum")])
    make_meeting("r2", segments=[(Track.MIC.value, 0, 1000, "assunto comum")])

    todos = call(server, "buscar", termo="assunto")
    um = call(server, "buscar", termo="assunto", reuniao="r1")

    assert len(todos["itens"]) == 2
    assert len(um["itens"]) == 1


# -- errors ------------------------------------------------------------

def test_unknown_meeting_names_the_identifier(server) -> None:
    with pytest.raises(Exception) as caught:
        call(server, "ler_transcricao", reuniao="nao-existe")
    assert "nao-existe" in str(caught.value)


def test_a_unique_prefix_resolves(server, make_meeting) -> None:
    make_meeting("abcdef123456")
    payload = call(server, "ler_transcricao", reuniao="abcdef")
    assert payload["reuniao"]["id"] == "abcdef123456"


def test_collection_state_reports_where_the_data_lives(server, make_meeting) -> None:
    make_meeting("r1")
    payload = call(server, "estado_do_acervo")
    assert payload["reunioes"] == 1
    assert payload["com_transcricao"] == 1
    assert payload["diretorio_de_dados"]
    assert payload["origem_do_diretorio"], (
        "a origem do valor tem de ser diagnosticavel"
    )
