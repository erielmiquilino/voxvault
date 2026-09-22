"""The MCP server: the meeting history, readable by an agent.

Runs as its own process, reading the store directly. The main application does
not have to be open, and nothing here imports the inference runtime or the
capture layer -- a client that launches this server on every session start
would feel every second of that.

It opens no network port. Everything travels over stdio.

The one rule that shapes the whole surface: **an agent may read everything and
may write notes, and may not touch the record of what was said.** There is no
tool to delete a meeting, alter a segment, remove audio, start a recording or
trigger a reprocess. A transcript is evidence of a conversation that happened,
and an agent editing it would make it worthless as evidence.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from pydantic import Field

from ..config import Config, load_config
from .cursors import STABLE_BASE, CursorError, Tool, decode, issue
from .pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_RESPONSE_CHARS,
    Page,
    build_page,
    clamp_page_size,
    paginate_text,
)

SERVER_NAME = "voxvault"
EXCERPT_CHARS = 280


class _Session:
    """Holds the store open for the life of the process.

    The client name arrives at initialization and is recorded as the
    authorship of every note written, so a summary written by one agent is
    distinguishable from one written by another months later.
    """

    def __init__(self, config: Config | None = None) -> None:
        # Resolved through the shared configuration, never from the working
        # directory: an MCP client launches its servers from wherever it
        # happens to be, and deriving the store from that is how two processes
        # end up quietly pointed at two different databases.
        self.config = config or load_config()
        # The protocol layer runs each synchronous tool on a worker thread,
        # and a sqlite3 connection belongs to the thread that opened it.
        self._handles = None
        self.client = "cliente-mcp"

    @property
    def store(self):
        if self._handles is None:
            from ..store import ThreadLocalStore  # noqa: PLC0415

            self.config.data_dir.mkdir(parents=True, exist_ok=True)
            self._handles = ThreadLocalStore(self.config.db_path)
        return self._handles.handle

    def close(self) -> None:
        if self._handles is not None:
            self._handles.close_all()
            self._handles = None


SESSION = _Session()


# -- helpers -----------------------------------------------------------

def _meeting_or_fail(uid: str):
    meeting = SESSION.store.get_meeting(uid)
    if meeting is None:
        # Accept a unique prefix, the way git accepts a short hash: an agent
        # that read a listing has the full id, but a person pasting one often
        # does not.
        matches = [m for m in SESSION.store.iter_meetings() if m.uid.startswith(uid)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(
                f"O identificador '{uid}' corresponde a {len(matches)} reunioes. "
                f"Use o identificador completo."
            )
        raise ValueError(f"Nao existe reuniao com o identificador '{uid}'.")
    return meeting


def _availability(meeting) -> dict:
    """Transcript availability and attempt state, as two separate things.

    A meeting being reprocessed has a complete transcript available AND a
    running attempt. One field would have to lie about one of them, so the
    response carries both and never collapses them.
    """
    revision = SESSION.store.active_revision(meeting.uid)
    return {
        "transcricao_disponivel": revision is not None,
        "revisao_ativa": (
            {"id": revision.uid, "completude": str(revision.state),
             "motor": revision.engine_id}
            if revision else None
        ),
        "tentativa_corrente": {
            "estado": str(meeting.attempt_state),
            "motivo": meeting.attempt_error or None,
        },
    }


def _timeline_base(meeting) -> str:
    """A cursor over a timeline is only valid for one revision."""
    revision = SESSION.store.active_revision(meeting.uid)
    return revision.uid if revision else "sem-revisao"


def _meeting_summary(meeting) -> dict:
    revision = SESSION.store.active_revision(meeting.uid)
    return {
        "id": meeting.uid,
        "titulo": meeting.title,
        "inicio": meeting.started_at.isoformat(),
        "duracao_ms": meeting.duration_ms,
        "origem": str(meeting.origin),
        "estado_da_gravacao": str(meeting.state),
        "transcricao_disponivel": revision is not None,
        "completude": str(revision.state) if revision else None,
        "estado_da_tentativa": str(meeting.attempt_state),
        "motivo_da_falha": meeting.attempt_error or None,
        "notas": _note_count(meeting.uid),
    }


def _note_count(meeting_uid: str) -> int:
    lister = getattr(SESSION.store, "notes_of", None)
    if lister is None:
        return 0
    try:
        return len(lister(meeting_uid))
    except Exception:
        return 0


def _entry_payload(entry) -> dict:
    return {
        "inicio_ms": entry.start_ms,
        "fim_ms": entry.end_ms,
        "falante": str(entry.speaker),
        "trilha": str(entry.track),
        "texto": entry.text,
        "sobreposto": bool(getattr(entry, "overlaps", False)),
    }


def _cut(text: str, limit: int = EXCERPT_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _speaks_to_the_agent(function):
    """Let a tool's own message reach the client.

    The protocol layer deliberately replaces an unexpected exception with a
    generic one, so nothing internal leaks. That protection also swallows the
    messages this server writes on purpose -- which name the identifier or the
    parameter at fault, and are the difference between an agent that can
    correct itself and one that just sees "error". So the errors meant for a
    reader are re-raised as the protocol's own type, which is passed through,
    and everything unexpected is left alone to be hidden as it should be.
    """
    import functools

    from mcp.server.mcpserver.exceptions import ToolError  # noqa: PLC0415

    from ..errors import VoxVaultError  # noqa: PLC0415

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (VoxVaultError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def _register(server) -> None:
    """Declare every tool. Kept in one place so the surface is auditable."""

    @server.tool(
        name="listar_reunioes",
        description=(
            "Lista as reuniões gravadas, da mais recente para a mais antiga. "
            "Cada reunião traz identificador, título, início, duração, origem, "
            "a disponibilidade da transcrição e o estado da tentativa corrente "
            "como atributos separados."
        ),
    )
    @_speaks_to_the_agent
    def listar_reunioes(
        desde: Annotated[str, Field(description="Data inicial ISO, ex. 2026-09-01")] = "",
        ate: Annotated[str, Field(description="Data final ISO")] = "",
        ultimos_dias: Annotated[int, Field(description="Atalho: últimos N dias")] = 0,
        titulo_contem: Annotated[str, Field(description="Filtra por trecho do título")] = "",
        estado: Annotated[str, Field(description="Filtra pelo estado da tentativa")] = "",
        tamanho_da_pagina: int = DEFAULT_PAGE_SIZE,
        cursor: str = "",
    ) -> dict:
        after = None
        if cursor:
            after = tuple(decode(cursor, expected=Tool.MEETINGS, base=STABLE_BASE).position)

        since = _parse_moment(desde)
        until = _parse_moment(ate)
        if ultimos_dias > 0:
            since = datetime.now(timezone.utc) - timedelta(days=ultimos_dias)

        def source():
            for meeting in SESSION.store.iter_meetings():
                key = (-meeting.started_at_ms, meeting.uid)
                if after is not None and key <= tuple(after):
                    continue
                if since and meeting.started_at < since:
                    continue
                if until and meeting.started_at > until:
                    continue
                if titulo_contem and titulo_contem.lower() not in meeting.title.lower():
                    continue
                if estado and str(meeting.attempt_state) != estado:
                    continue
                yield meeting

        page = build_page(
            source(),
            render=_meeting_summary,
            text_of=lambda r: r["titulo"] or "",
            cursor_of=lambda m: issue(
                Tool.MEETINGS, (-m.started_at_ms, m.uid), STABLE_BASE
            ),
            page_size=tamanho_da_pagina,
        )
        return page.as_dict()

    @server.tool(
        name="ler_transcricao",
        description=(
            "Devolve a linha de tempo de uma reunião: instante, quem falou e o "
            "texto de cada segmento. Informa separadamente se há transcrição "
            "disponível e se há uma tentativa em andamento — uma reunião sendo "
            "reprocessada tem as duas coisas ao mesmo tempo."
        ),
    )
    @_speaks_to_the_agent
    def ler_transcricao(
        reuniao: Annotated[str, Field(description="Identificador da reunião")],
        tamanho_da_pagina: int = DEFAULT_PAGE_SIZE,
        cursor: str = "",
    ) -> dict:
        meeting = _meeting_or_fail(reuniao)
        base = _timeline_base(meeting)
        estado = _availability(meeting)

        if not estado["transcricao_disponivel"]:
            # Not an error: a meeting waiting in the queue is a normal state,
            # and reporting it as a failure would make the agent give up.
            return {
                "reuniao": _meeting_summary(meeting),
                **estado,
                "itens": [],
                "ha_mais": False,
                "observacao": (
                    "Esta reunião ainda não tem revisão ativa. O estado da "
                    "tentativa corrente diz em que ponto ela está."
                ),
            }

        after = None
        if cursor:
            after = tuple(decode(cursor, expected=Tool.TIMELINE, base=base).position)

        page = build_page(
            SESSION.store.iter_timeline(meeting.uid, after=after),
            render=_entry_payload,
            text_of=lambda r: r["texto"],
            cursor_of=lambda e: issue(
                Tool.TIMELINE, SESSION.store.cursor_of(e), base
            ),
            page_size=tamanho_da_pagina,
        )
        return {"reuniao": _meeting_summary(meeting), **estado, **page.as_dict()}

    @server.tool(
        name="recortar_transcricao",
        description=(
            "Devolve apenas os segmentos que se sobrepõem a um intervalo de "
            "tempo da reunião. Útil para ler um trecho específico sem carregar "
            "a reunião inteira."
        ),
    )
    @_speaks_to_the_agent
    def recortar_transcricao(
        reuniao: Annotated[str, Field(description="Identificador da reunião")],
        inicio_ms: Annotated[int, Field(description="Início do intervalo, em ms")],
        fim_ms: Annotated[int, Field(description="Fim do intervalo, em ms")],
        tamanho_da_pagina: int = DEFAULT_PAGE_SIZE,
        cursor: str = "",
    ) -> dict:
        meeting = _meeting_or_fail(reuniao)
        base = _timeline_base(meeting)
        estado = _availability(meeting)
        if not estado["transcricao_disponivel"]:
            return {"reuniao": _meeting_summary(meeting), **estado,
                    "itens": [], "ha_mais": False}

        after = None
        if cursor:
            after = tuple(decode(cursor, expected=Tool.TIMELINE, base=base).position)

        def overlapping():
            for entry in SESSION.store.iter_timeline(meeting.uid, after=after):
                if entry.start_ms >= fim_ms:
                    break  # the timeline is ordered; nothing later can overlap
                if entry.end_ms > inicio_ms:
                    yield entry

        page = build_page(
            overlapping(),
            render=_entry_payload,
            text_of=lambda r: r["texto"],
            cursor_of=lambda e: issue(
                Tool.TIMELINE, SESSION.store.cursor_of(e), base
            ),
            page_size=tamanho_da_pagina,
        )
        return {
            "reuniao": _meeting_summary(meeting),
            "intervalo": {"inicio_ms": inicio_ms, "fim_ms": fim_ms},
            **estado, **page.as_dict(),
        }

    @server.tool(
        name="buscar",
        description=(
            "Busca textual em todo o histórico. Insensível a maiúsculas e a "
            "acentuação nos dois sentidos: buscar 'reuniao' encontra 'reunião'. "
            "O escopo pode ser restrito a transcrições, a notas ou a ambos."
        ),
    )
    @_speaks_to_the_agent
    def buscar(
        termo: Annotated[str, Field(description="O que procurar")],
        escopo: Annotated[
            str, Field(description="'transcricoes', 'notas' ou 'ambos'")
        ] = "ambos",
        reuniao: Annotated[str, Field(description="Restringe a uma reunião")] = "",
        limite: int = 50,
    ) -> dict:
        # Always passed, including "ambos". The store defaults to transcripts
        # only, so omitting it would quietly drop every note from a search the
        # caller did not restrict -- which is the opposite of what asking for
        # no restriction means.
        kwargs: dict[str, Any] = {
            "limit": clamp_page_size(limite),
            "scope": escopo or "ambos",
        }
        if reuniao:
            kwargs["meeting_uid"] = _meeting_or_fail(reuniao).uid

        hits = SESSION.store.search(termo, **kwargs)

        resultados = []
        for hit in hits:
            # A note has no instant, no track and no speaker. Filling those
            # with zeros would render an interpretation as though it were
            # something someone said at the start of the meeting.
            item = {
                "reuniao": hit.meeting_uid,
                "titulo": hit.meeting_title,
                "natureza": getattr(hit, "nature", "transcricao"),
                "recorte": hit.excerpt,
            }
            if hasattr(hit, "start_ms"):
                item["inicio_ms"] = hit.start_ms
                item["falante"] = hit.speaker
            note_uid = getattr(hit, "note_uid", None) or getattr(hit, "uid", None)
            if note_uid:
                # The identifier is what makes a found note readable in full,
                # updatable and removable.
                item["nota"] = note_uid
                item["tipo"] = str(getattr(hit, "kind", ""))
            resultados.append(item)

        return {"termo": termo, "escopo": kwargs["scope"], "itens": resultados,
                "total": len(resultados), "ha_mais": False}

    @server.tool(
        name="estado_do_acervo",
        description=(
            "Resumo do acervo: quantas reuniões existem, quantas estão "
            "transcritas, o que está na fila e onde os dados ficam. Útil como "
            "primeira chamada para saber com o que se está lidando."
        ),
    )
    @_speaks_to_the_agent
    def estado_do_acervo() -> dict:
        total = transcritas = na_fila = falhas = 0
        for meeting in SESSION.store.iter_meetings():
            total += 1
            if SESSION.store.active_revision(meeting.uid) is not None:
                transcritas += 1
            estado = str(meeting.attempt_state)
            if estado in {"na_fila", "em_execucao"}:
                na_fila += 1
            elif estado == "falhou":
                falhas += 1
        return {
            "reunioes": total,
            "com_transcricao": transcritas,
            "aguardando_transcricao": na_fila,
            "com_falha": falhas,
            "diretorio_de_dados": str(SESSION.config.data_dir),
            "origem_do_diretorio": SESSION.config.source_of("data_dir"),
            "limite_por_resposta": {
                "itens_padrao": DEFAULT_PAGE_SIZE,
                "caracteres": MAX_RESPONSE_CHARS,
            },
        }

    _register_notes(server)


def _register_notes(server) -> None:
    """Note tools. Reading comes first, deliberately.

    Update and delete take an identifier, so a surface that offers them
    without a way to obtain one would be offering operations nobody can
    reach. Listing and creation both hand the identifier back.
    """

    @server.tool(
        name="listar_notas",
        description=(
            "Lista as notas de uma reunião, com identificador, tipo, autoria, "
            "instantes e um recorte inicial do conteúdo. O identificador é o "
            "que permite ler, atualizar ou remover a nota depois."
        ),
    )
    @_speaks_to_the_agent
    def listar_notas(
        reuniao: Annotated[str, Field(description="Identificador da reunião")],
        tamanho_da_pagina: int = DEFAULT_PAGE_SIZE,
        cursor: str = "",
    ) -> dict:
        meeting = _meeting_or_fail(reuniao)
        notes = _notes_api().notes_of(meeting.uid)

        after = None
        if cursor:
            after = tuple(decode(cursor, expected=Tool.NOTES, base=STABLE_BASE).position)

        def source():
            for note in notes:
                key = (note.created_at_ms, note.uid)
                if after is not None and key <= tuple(after):
                    continue
                yield note

        page = build_page(
            source(),
            render=lambda n: {**_note_payload(n), "recorte": _cut(n.content)},
            text_of=lambda r: r["recorte"],
            cursor_of=lambda n: issue(
                Tool.NOTES, (n.created_at_ms, n.uid), STABLE_BASE
            ),
            page_size=tamanho_da_pagina,
        )
        return {"reuniao": meeting.uid, **page.as_dict()}

    @server.tool(
        name="ler_nota",
        description=(
            "Devolve o conteúdo integral de uma nota pelo identificador. "
            "Conteúdo longo vem paginado por posição no texto."
        ),
    )
    @_speaks_to_the_agent
    def ler_nota(
        nota: Annotated[str, Field(description="Identificador da nota")],
        cursor: str = "",
    ) -> dict:
        note = _notes_api().get_note(nota)
        if note is None:
            raise ValueError(f"Nao existe nota com o identificador {nota}.")

        base = hashlib.sha256(note.content.encode("utf-8")).hexdigest()[:16]
        offset = 0
        if cursor:
            offset = int(decode(cursor, expected=Tool.NOTE_CONTENT, base=base).position[0])

        window, next_offset = paginate_text(note.content, offset)
        payload = {
            **_note_payload(note, content=window),
            "ha_mais": next_offset is not None,
        }
        if next_offset is not None:
            payload["proximo_cursor"] = issue(
                Tool.NOTE_CONTENT, (next_offset,), base
            )
        return payload

    @server.tool(
        name="criar_nota",
        description=(
            "Grava uma nota numa reunião — um resumo, pendências, decisões. "
            "Devolve o identificador, que é o suficiente para atualizá-la ou "
            "removê-la depois. Não altera a transcrição."
        ),
    )
    @_speaks_to_the_agent
    def criar_nota(
        reuniao: Annotated[str, Field(description="Identificador da reunião")],
        tipo: Annotated[str, Field(description="Tipo da nota, ex. resumo")],
        conteudo: Annotated[str, Field(description="Texto da nota")],
    ) -> dict:
        from ..store.notes import NoteAuthor  # noqa: PLC0415

        meeting = _meeting_or_fail(reuniao)
        note = _notes_api().create_note(
            meeting.uid,
            kind=tipo,
            content=conteudo,
            # An automated client has to name itself: "an agent wrote this" is
            # not enough to judge a summary by months later.
            author=NoteAuthor.agent(SESSION.client),
        )
        _refresh_exports(meeting.uid)
        return _note_payload(note)

    @server.tool(
        name="atualizar_nota",
        description=(
            "Substitui o conteúdo de uma nota, preservando seu identificador e "
            "sua autoria original."
        ),
    )
    @_speaks_to_the_agent
    def atualizar_nota(
        nota: Annotated[str, Field(description="Identificador da nota")],
        conteudo: Annotated[str, Field(description="Novo texto")],
    ) -> dict:
        updated = _notes_api().update_note(nota, content=conteudo)
        _refresh_exports(updated.meeting_uid)
        return _note_payload(updated)

    @server.tool(
        name="remover_nota",
        description="Remove uma nota pelo identificador. Não afeta as demais.",
    )
    @_speaks_to_the_agent
    def remover_nota(
        nota: Annotated[str, Field(description="Identificador da nota")],
    ) -> dict:
        store = _notes_api()
        existing = store.get_note(nota)
        store.delete_note(nota)
        if existing is not None:
            _refresh_exports(existing.meeting_uid)
        return {"id": nota, "removida": True}


def _refresh_exports(meeting_uid: str) -> None:
    """Rebuild a meeting's export files after its notes changed.

    The staleness signal on disk tracks the *revision*, so a note-only change
    is invisible to it and the reconciliation at startup would never notice.
    Until that signal becomes note-aware -- a real gap in the specification,
    not something to paper over silently -- every note write regenerates here.

    Derived files never fail the operation that produced them: the note is
    already stored, and an export that could not be rewritten is a smaller
    problem than losing the write.
    """
    try:
        from ..store import regenerate_exports  # noqa: PLC0415

        store = SESSION.store
        meeting = store.get_meeting(meeting_uid)
        if meeting is not None and meeting.active_revision_id is not None:
            regenerate_exports(store, meeting_uid)
    except Exception:
        pass


def _notes_api():
    """The store's note API, with a clear message when it is not there yet."""
    store = SESSION.store
    if not hasattr(store, "notes_of"):
        raise RuntimeError(
            "Este armazenamento ainda nao tem suporte a notas. Atualize o "
            "VoxVault para uma versao que inclua a migracao de notas."
        )
    return store


def _note_payload(note, *, content: str | None = None) -> dict:
    payload = {
        "id": note.uid,
        "reuniao": note.meeting_uid,
        "tipo": str(note.kind),
        "autoria": note.author.describe(),
        "criada_em": note.created_at.isoformat(),
        "alterada_em": note.updated_at.isoformat(),
    }
    if content is not None:
        payload["conteudo"] = content
    return payload


def _parse_moment(text: str) -> datetime | None:
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"Data invalida: '{text}'. Use o formato ISO, por exemplo "
            f"2026-09-01 ou 2026-09-01T14:30:00."
        ) from None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def build_server(config: Config | None = None):
    """Construct the server with every tool registered."""
    from mcp.server.mcpserver import MCPServer  # noqa: PLC0415

    if config is not None:
        SESSION.config = config

    server = MCPServer(
        name=SERVER_NAME,
        title="VoxVault",
        instructions=(
            "Acesso ao histórico de reuniões gravadas e transcritas localmente "
            "pelo VoxVault. Você pode ler transcrições, buscar no histórico e "
            "gravar notas. Você não pode alterar nem apagar a transcrição: ela "
            "é o registro do que foi efetivamente dito.\n\n"
            "Comece por 'estado_do_acervo' para saber o tamanho do acervo, "
            "depois 'listar_reunioes'. Respostas longas vêm paginadas por "
            "cursor: passe o 'proximo_cursor' recebido para continuar, e trate "
            "'ha_mais' como a indicação de que ainda falta conteúdo."
        ),
        version="0.1.0",
    )
    _register(server)
    return server
