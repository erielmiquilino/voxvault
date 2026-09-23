"""Export files: derived artifacts that must never lie about their source.

An export cannot be published atomically with the revision that produced it --
one is a row in a transaction, the other is a file on disk, and no amount of
care makes those one operation. So the files do not pretend: each one records
the identifier of the revision it was generated from, and any file whose
identifier differs from the meeting's active revision is stale by definition.

That turns "the process died between publishing and regenerating" from a
corruption into a detectable state. :func:`reconcile_exports` runs at startup,
finds every meeting in that state, and regenerates. Until it does,
:func:`current_export_paths` refuses to hand the stale file to anyone.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from ..errors import StorageError
from ..types import TimelineEntry
from .models import ExportStatus, Meeting, Revision, from_ms
from .notes import Note, NoteAuthor, NoteAuthorKind, NoteKind
from .store import TranscriptStore
from .timeline import format_duration, format_offset, overlapping_ids

READABLE_NAME: Final = "transcricao.md"
STRUCTURED_NAME: Final = "transcricao.json"

#: Bumped when the structured shape changes in a way a reader must notice.
#:
#: Version 2 added ``notas``. The key is always present, empty list included,
#: so a reader can tell "this meeting has no notes" from "this file predates
#: notes" -- which is the whole reason the version moved rather than the key
#: simply appearing when there happened to be something to put in it.
STRUCTURED_FORMAT: Final = "voxvault-transcricao"
STRUCTURED_VERSION: Final = 2

#: Heading of the notes section in the readable export. Notes go above the
#: timeline because a summary is what a reader came for, and the section is
#: named and captioned so that no line of it can be mistaken for something
#: that was said.
NOTES_HEADING: Final = "## Notas"
TIMELINE_HEADING: Final = "## Linha de tempo"

_REASON_NO_REVISION: Final = "sem_revisao_ativa"
_REASON_MISSING_FILE: Final = "arquivo_ausente"
_REASON_DIVERGENT: Final = "revisao_divergente"
_REASON_CURRENT: Final = ""


def export_paths(meeting: Meeting) -> tuple[Path, Path]:
    directory = Path(meeting.directory)
    return directory / READABLE_NAME, directory / STRUCTURED_NAME


def export_status(
    store: TranscriptStore, meeting_uid: str, *, verify_contents: bool = False
) -> ExportStatus:
    """Whether the files on disk correspond to the active revision.

    ``verify_contents`` opens the structured file and reads the identifier it
    actually carries. The recorded column is enough to catch a crash between
    publication and regeneration -- it is only written after both files are --
    so the cheap check is the default and the thorough one is for the moment a
    surface is about to show the file to someone.
    """
    meeting = _require(store, meeting_uid)
    readable, structured = export_paths(meeting)
    active = store.active_revision(meeting_uid)
    if active is None:
        return ExportStatus(
            meeting_uid=meeting_uid,
            active_revision_uid=None,
            exported_revision_uid=meeting.exports_revision_uid,
            readable_path=str(readable),
            structured_path=str(structured),
            current=False,
            reason=_REASON_NO_REVISION,
        )
    reason = _REASON_CURRENT
    if meeting.exports_revision_uid != active.uid:
        reason = _REASON_DIVERGENT
    elif not readable.exists() or not structured.exists():
        reason = _REASON_MISSING_FILE
    elif verify_contents and recorded_revision_uid(structured) != active.uid:
        reason = _REASON_DIVERGENT
    return ExportStatus(
        meeting_uid=meeting_uid,
        active_revision_uid=active.uid,
        exported_revision_uid=meeting.exports_revision_uid,
        readable_path=str(readable),
        structured_path=str(structured),
        current=reason == _REASON_CURRENT,
        reason=reason,
    )


def recorded_revision_uid(structured_path: Path) -> str:
    """The revision identifier a structured export carries, or ''."""
    try:
        payload = json.loads(Path(structured_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    revision = payload.get("revisao")
    if isinstance(revision, dict):
        return str(revision.get("uid", ""))
    return ""


def regenerate_exports(store: TranscriptStore, meeting_uid: str) -> ExportStatus:
    """Rebuild both files from the active revision.

    Order matters: both files land first, and only then is the meeting's
    recorded identifier updated. A crash anywhere in between leaves the
    recorded identifier stale, which is exactly the signal that makes the next
    startup do this again.
    """
    meeting = _require(store, meeting_uid)
    active = store.active_revision(meeting_uid)
    if active is None:
        raise_no_revision(meeting_uid)
    entries = store.timeline(meeting_uid)
    notes = store.notes_of(meeting_uid)
    readable, structured = export_paths(meeting)
    if not readable.parent.is_dir():
        # Never recreated here. A meeting's directory only goes away when the
        # meeting is being deleted, and exports written into a fresh one would
        # outlive the deletion as a transcript nobody asked to keep.
        raise StorageError(
            f"O diretorio da reuniao '{meeting_uid}' nao existe mais "
            f"({readable.parent}). As exportacoes nao foram geradas."
        )
    _write_atomic(readable, render_readable(meeting, active, entries, notes=notes))
    _write_atomic(
        structured, render_structured(meeting, active, entries, notes=notes)
    )
    store.set_exports_revision(meeting_uid, active.uid)
    return export_status(store, meeting_uid)


def current_export_paths(
    store: TranscriptStore, meeting_uid: str, *, regenerate: bool = True
) -> tuple[Path, Path] | None:
    """The export files, but only if they correspond to the active revision.

    Returns ``None`` rather than a stale file. A divergent export is never
    handed to a surface as though it were current.
    """
    status = export_status(store, meeting_uid, verify_contents=True)
    if status.current:
        return Path(status.readable_path), Path(status.structured_path)
    if status.reason == _REASON_NO_REVISION or not regenerate:
        return None
    status = regenerate_exports(store, meeting_uid)
    if not status.current:
        return None
    return Path(status.readable_path), Path(status.structured_path)


def reconcile_exports(store: TranscriptStore) -> list[str]:
    """Regenerate every export that diverges from its meeting's revision.

    Meant for startup. Returns the meetings that were regenerated.
    """
    regenerated: list[str] = []
    for meeting in store.iter_meetings():
        if meeting.active_revision_id is None:
            continue
        status = export_status(store, meeting.uid)
        if not status.current:
            regenerate_exports(store, meeting.uid)
            regenerated.append(meeting.uid)
    return regenerated


# -- rendering -----------------------------------------------------------


def render_readable(
    meeting: Meeting,
    revision: Revision,
    entries: Iterable[TimelineEntry],
    *,
    notes: Iterable[Note] = (),
) -> str:
    """The meeting as a person reads it: metadata, then notes, then the record.

    The notes get a section of their own with a caption saying what they are,
    and no note text is ever emitted inside the timeline. Someone skimming
    this file in a year has to be able to tell, without thinking about it,
    which lines are what was said and which are what somebody concluded.
    """
    entries = list(entries)
    notes = list(notes)
    flagged = overlapping_ids(entries)
    started = from_ms(meeting.started_at_ms).astimezone()
    lines = [
        "---",
        "voxvault_formato: transcricao-legivel",
        f"voxvault_reuniao: {meeting.uid}",
        f"voxvault_revisao: {revision.uid}",
        f"voxvault_motor: {revision.engine_id}",
        "---",
        "",
        f"# {meeting.title}",
        "",
        f"- Data: {started.strftime('%Y-%m-%d %H:%M')}",
        f"- Duracao: {format_duration(meeting.duration_ms)}",
        f"- Transcricao: {revision.state or 'indefinida'} "
        f"(motor {revision.engine_id})",
    ]
    if revision.tracks_failed:
        lines.append(
            f"- Trilhas nao transcritas: {', '.join(revision.tracks_failed)}"
        )
    if notes:
        lines += ["", NOTES_HEADING, ""]
        lines.append(
            "_Interpretacao registrada depois da reuniao. O que foi dito esta "
            "na linha de tempo, mais abaixo._"
        )
        for note in notes:
            lines += ["", _note_heading(note), ""]
            lines.append(note.content.strip())
    lines += ["", TIMELINE_HEADING, ""]
    if not entries:
        lines.append("_Sem segmentos transcritos._")
    for entry in entries:
        mark = "  [fala sobreposta]" if entry.segment_id in flagged else ""
        lines.append(
            f"[{format_offset(entry.start_ms)}] {entry.speaker}: {entry.text}{mark}"
        )
    lines.append("")
    return "\n".join(lines)


def render_structured(
    meeting: Meeting,
    revision: Revision,
    entries: Iterable[TimelineEntry],
    *,
    notes: Iterable[Note] = (),
) -> str:
    entries = list(entries)
    notes = list(notes)
    flagged = overlapping_ids(entries)
    payload: dict[str, Any] = {
        "formato": STRUCTURED_FORMAT,
        "versao": STRUCTURED_VERSION,
        "gerado_em": datetime.now(UTC).isoformat(),
        "revisao": {
            "uid": revision.uid,
            "estado": str(revision.state) if revision.state else "",
            "motor": {
                "identificador": revision.engine_id,
                "nome": revision.engine_name,
                "modelo": revision.engine_model,
                "dispositivo": revision.engine_device,
                "precisao": revision.engine_compute,
                "versao": revision.engine_version,
            },
            "configuracao": revision.config_fingerprint,
            "vocabulario": revision.vocabulary,
            "idioma": revision.language,
            "trilhas_ok": list(revision.tracks_ok),
            "trilhas_falhas": list(revision.tracks_failed),
            "concluida_em_ms": revision.completed_at_ms,
        },
        "reuniao": {
            "uid": meeting.uid,
            "titulo": meeting.title,
            "inicio_ms": meeting.started_at_ms,
            "fim_ms": meeting.ended_at_ms,
            "inicio": from_ms(meeting.started_at_ms).isoformat(),
            "duracao_ms": meeting.duration_ms,
            "estado": str(meeting.state),
            "transcricao": str(meeting.transcript_state),
            "origem": str(meeting.origin),
            "caminho_origem": meeting.source_path,
            "diretorio": meeting.directory,
        },
        "segmentos": [
            {
                "id": e.segment_id,
                "trilha": str(e.track),
                "falante": str(e.speaker),
                "inicio_ms": e.start_ms,
                "fim_ms": e.end_ms,
                "texto": e.text,
                "sobreposto": e.segment_id in flagged,
            }
            for e in entries
        ],
        # A key of its own, never entries mixed into "segmentos". A consumer
        # that reads only the timeline gets only what was said, which is the
        # property the whole notes feature is built around.
        "notas": [
            {
                "uid": n.uid,
                "tipo": str(n.kind),
                "conteudo": n.content,
                "autoria": {
                    "tipo": str(n.author.kind),
                    "cliente": n.author.client,
                },
                "criada_em_ms": n.created_at_ms,
                "criada_em": from_ms(n.created_at_ms).isoformat(),
                "alterada_em_ms": n.updated_at_ms,
                "alterada_em": from_ms(n.updated_at_ms).isoformat(),
            }
            for n in notes
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def load_structured(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def timeline_from_structured(payload: dict[str, Any]) -> list[TimelineEntry]:
    """Rebuild the timeline from a structured export, without loss."""
    return [
        TimelineEntry(
            start_ms=item["inicio_ms"],
            end_ms=item["fim_ms"],
            text=item["texto"],
            track=item["trilha"],
            speaker=item["falante"],
            segment_id=item["id"],
        )
        for item in payload["segmentos"]
    ]


def notes_from_structured(payload: dict[str, Any]) -> list[Note]:
    """Rebuild the notes from a structured export, without loss.

    ``id`` and ``meeting_id`` come back as zero: they are row numbers that
    never left the database, and a file that carried them would be inviting
    someone to write them back somewhere they do not belong.
    """
    return [
        Note(
            id=0,
            uid=item["uid"],
            meeting_id=0,
            meeting_uid=payload["reuniao"]["uid"],
            kind=NoteKind(item["tipo"]),
            content=item["conteudo"],
            author=NoteAuthor(
                NoteAuthorKind(item["autoria"]["tipo"]), item["autoria"]["cliente"]
            ),
            created_at_ms=item["criada_em_ms"],
            updated_at_ms=item["alterada_em_ms"],
        )
        for item in payload.get("notas", [])
    ]


# -- internals -----------------------------------------------------------


def _note_heading(note: Note) -> str:
    """One note's own heading: what it claims to be, and who claimed it."""
    moment = from_ms(note.created_at_ms).astimezone().strftime("%Y-%m-%d %H:%M")
    line = f"### {note.kind} ({note.author.describe()}, {moment})"
    if note.updated_at_ms != note.created_at_ms:
        altered = (
            from_ms(note.updated_at_ms).astimezone().strftime("%Y-%m-%d %H:%M")
        )
        line += f" -- alterada em {altered}"
    return line


def _write_atomic(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _require(store: TranscriptStore, meeting_uid: str) -> Meeting:
    meeting = store.get_meeting(meeting_uid)
    if meeting is None:
        raise StorageError(f"Reuniao '{meeting_uid}' nao encontrada.")
    return meeting


def raise_no_revision(meeting_uid: str) -> None:
    raise StorageError(
        f"A reuniao '{meeting_uid}' nao tem revisao ativa: nao ha o que exportar."
    )
