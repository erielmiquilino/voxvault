"""Meeting notes: interpretation, kept next to the record but never inside it.

A segment is evidence -- someone said these words at this instant. A note is a
reading of that evidence: a summary, a list of decisions, a list of pending
items, or a free remark. The two are stored in different tables, indexed in
different FTS tables, and rendered in different sections of an export, because
a model-written summary can be wrong and "this was said" must stay
distinguishable from "this was concluded" months later, at a glance.

What lives here is the vocabulary of a note and the validation that guards it.
The queries live in :mod:`voxvault.store.store`, which owns the connection and
the write-contention policy; splitting the transaction discipline across two
modules is the one thing this package does not do.
"""

from __future__ import annotations

import enum
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from ..errors import StorageError
from .models import NATURE_NOTE, NATURE_TRANSCRIPT, from_ms

#: Re-exported so that everything a caller needs to read a note result can be
#: imported from one place. They are declared in ``models`` because
#: ``SearchHit`` answers with one of them too.
__all__ = [
    "NATURE_NOTE",
    "NATURE_TRANSCRIPT",
    "NOTE_COLUMNS",
    "Note",
    "NoteAuthor",
    "NoteAuthorKind",
    "NoteHit",
    "NoteKind",
    "SearchScope",
    "author_from_row",
    "clean_content",
    "coerce_kind",
    "coerce_scope",
    "note_from_row",
]


class NoteKind(enum.StrEnum):
    """What a note claims to be.

    Closed on purpose: an open text field would let every client invent its
    own label, and the export could no longer promise that a reader can tell a
    list of decisions from a stray remark.
    """

    SUMMARY = "resumo"
    DECISIONS = "decisoes"
    PENDING = "pendencias"
    FREE = "livre"


class NoteAuthorKind(enum.StrEnum):
    """Whether a person or an automated client wrote the note.

    The distinction is the point of recording authorship at all: whether to
    trust a summary months later depends on what produced it.
    """

    USER = "usuario"
    AGENT = "agente"


class SearchScope(enum.StrEnum):
    """Which kinds of content a search reads."""

    TRANSCRIPTS = "transcricoes"
    NOTES = "notas"
    BOTH = "ambos"


@dataclass(frozen=True, slots=True)
class NoteAuthor:
    """Who recorded a note.

    An automated client must name itself. "an agent wrote this" is not enough
    to judge a summary by -- knowing it was Claude Desktop and not some
    throwaway script is the whole value of the field. The database carries the
    same rule as a CHECK constraint, so it holds for rows this class never saw.
    """

    kind: NoteAuthorKind
    client: str = ""

    def __post_init__(self) -> None:
        if self.kind is NoteAuthorKind.AGENT and not self.client.strip():
            raise ValueError(
                "a autoria de um cliente automatizado precisa identificar qual "
                "cliente gravou a nota"
            )

    @classmethod
    def user(cls, client: str = "") -> NoteAuthor:
        return cls(NoteAuthorKind.USER, client)

    @classmethod
    def agent(cls, client: str) -> NoteAuthor:
        return cls(NoteAuthorKind.AGENT, client)

    def describe(self) -> str:
        """One phrase naming the origin, for anything a person reads."""
        if self.client:
            return f"{self.kind.value} {self.client}"
        return self.kind.value


@dataclass(frozen=True, slots=True)
class Note:
    """One note as it is stored.

    ``uid`` is the stable identifier every surface uses; ``id`` is the row
    number and never leaves this package.
    """

    id: int
    uid: str
    meeting_id: int
    meeting_uid: str
    kind: NoteKind
    content: str
    author: NoteAuthor
    created_at_ms: int
    updated_at_ms: int

    @property
    def created_at(self) -> datetime:
        return from_ms(self.created_at_ms)

    @property
    def updated_at(self) -> datetime:
        return from_ms(self.updated_at_ms)

    @property
    def nature(self) -> str:
        return NATURE_NOTE


@dataclass(frozen=True, slots=True)
class NoteHit:
    """A search result that came from a note.

    Deliberately not a :class:`~voxvault.store.models.SearchHit` with empty
    fields: a note has no instant, no track and no speaker, and inventing a
    zero for them is how interpretation ends up rendered as though it were
    something someone said at the start of the meeting. ``excerpt`` and
    ``text`` are named as in ``SearchHit`` so a caller can read both kinds of
    result uniformly where they genuinely have something in common.
    """

    meeting_uid: str
    meeting_title: str
    meeting_started_at_ms: int
    note_uid: str
    kind: NoteKind
    author: NoteAuthor
    created_at_ms: int
    updated_at_ms: int
    excerpt: str
    text: str

    @property
    def meeting_started_at(self) -> datetime:
        return from_ms(self.meeting_started_at_ms)

    @property
    def nature(self) -> str:
        return NATURE_NOTE


#: Columns every note query selects, aliased so one row mapper serves them all.
NOTE_COLUMNS: Final = (
    "n.id, n.uid, n.meeting_id, m.uid AS meeting_uid, n.kind, n.content,"
    " n.author_kind, n.author_client, n.created_at_ms, n.updated_at_ms"
)


def coerce_kind(value: NoteKind | str) -> NoteKind:
    """A note type, or a refusal that lists what is accepted."""
    try:
        return NoteKind(str(value))
    except ValueError:
        accepted = ", ".join(k.value for k in NoteKind)
        raise StorageError(
            f"Tipo de nota invalido: '{value}'. Os tipos aceitos sao: {accepted}."
        ) from None


def coerce_scope(value: SearchScope | str) -> SearchScope:
    try:
        return SearchScope(str(value))
    except ValueError:
        accepted = ", ".join(s.value for s in SearchScope)
        raise StorageError(
            f"Escopo de busca invalido: '{value}'. Os escopos aceitos sao: "
            f"{accepted}."
        ) from None


def clean_content(value: str) -> str:
    """The stored form of a note's text, or a refusal.

    Blank content is refused rather than stored: it indexes to nothing, shows
    as nothing in an export, and is only ever the result of a mistake upstream.
    """
    body = value.strip()
    if not body:
        raise StorageError(
            "O conteudo da nota nao pode ser vazio. Nada foi gravado."
        )
    return body


def author_from_row(row: sqlite3.Row) -> NoteAuthor:
    return NoteAuthor(NoteAuthorKind(row["author_kind"]), row["author_client"])


def note_from_row(row: sqlite3.Row) -> Note:
    return Note(
        id=row["id"],
        uid=row["uid"],
        meeting_id=row["meeting_id"],
        meeting_uid=row["meeting_uid"],
        kind=NoteKind(row["kind"]),
        content=row["content"],
        author=author_from_row(row),
        created_at_ms=row["created_at_ms"],
        updated_at_ms=row["updated_at_ms"],
    )
