"""Opaque cursors, bound to one tool and to one base.

Pagination here is by cursor and never by time window. A window would put a
segment that straddles its boundary into two pages, and would lose segments
that share a start instant between them -- both of which corrupt a transcript
for the agent reading it without anything looking wrong.

Every cursor carries three things: which tool issued it, the position in that
tool's total ordering, and a fingerprint of the base it was built over. The
fingerprint is what lets the server refuse a cursor whose ground has shifted,
rather than silently stitching two pages from two different revisions.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from enum import StrEnum

from ..errors import VoxVaultError


class Tool(StrEnum):
    """Each paginated tool and its declared total ordering.

    A cursor is accepted only by the tool that issued it. Without that, a
    listing cursor handed to the timeline would be read as a segment position
    and would silently return the wrong window.
    """

    TIMELINE = "linha_do_tempo"        # start_ms, track, segment id
    MEETINGS = "reunioes"              # started_at desc, meeting id
    NOTES = "notas"                    # created_at, note id
    SEARCH = "busca"                   # relevance desc, meeting id, item id
    NOTE_CONTENT = "conteudo_da_nota"  # character offset


class CursorError(VoxVaultError):
    """A cursor was malformed, meant for another tool, or has gone stale."""


class CursorInvalidated(CursorError):
    """The base the cursor was issued over has changed since."""

    def __init__(self, tool: Tool, detail: str) -> None:
        super().__init__(
            f"O cursor de '{tool}' nao vale mais: {detail}. "
            f"Reinicie a leitura desde a primeira pagina; devolver a proxima "
            f"pagina agora misturaria conteudos de bases diferentes."
        )


@dataclass(frozen=True, slots=True)
class Cursor:
    tool: Tool
    position: tuple
    base: str

    def encode(self) -> str:
        payload = json.dumps(
            {"f": str(self.tool), "p": list(self.position), "b": self.base},
            separators=(",", ":"), ensure_ascii=False,
        )
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode(raw: str, *, expected: Tool, base: str) -> Cursor:
    """Decode a cursor, refusing one from another tool or another base."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise CursorError(
            "O cursor apresentado nao e legivel. Ele deve ser passado "
            "exatamente como veio na resposta anterior."
        ) from None

    try:
        tool = Tool(payload["f"])
        position = tuple(payload["p"])
        issued_base = str(payload["b"])
    except (KeyError, TypeError, ValueError):
        raise CursorError("O cursor apresentado esta incompleto.") from None

    if tool is not expected:
        raise CursorError(
            f"Este cursor foi emitido por '{tool}' e nao pode ser usado em "
            f"'{expected}'. Cada ferramenta tem sua propria ordenacao, e "
            f"reaproveitar o cursor devolveria a janela errada."
        )

    if issued_base != base:
        raise CursorInvalidated(tool, _explain(tool))

    return Cursor(tool=tool, position=position, base=base)


def _explain(tool: Tool) -> str:
    return {
        Tool.TIMELINE: "a revisao ativa da reuniao mudou",
        Tool.SEARCH: "a revisao ativa de alguma reuniao do resultado mudou",
        Tool.NOTE_CONTENT: "a nota foi alterada ou removida",
    }.get(tool, "a base mudou")


def issue(tool: Tool, position: tuple, base: str) -> str:
    return Cursor(tool=tool, position=position, base=base).encode()


#: Listings are deliberately exempt from invalidation. A meeting created while
#: someone pages through the list must not break the read in progress: items
#: added later may not appear and removed ones may vanish, and neither is an
#: error. So their base is a constant rather than a fingerprint of content.
STABLE_BASE = "estavel"
