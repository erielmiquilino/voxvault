"""The transcript store.

One instance owns one SQLite connection and is **not** thread-safe: give each
thread its own. That is deliberate rather than a limitation -- a shared handle
guarded by a lock would let a slow caller hold the write lock for everyone,
which is the precise failure this layer exists to avoid.

Writes go through :func:`connection.transaction`, which keeps them short and
converts exhausted contention into :class:`StorageBusyError` instead of a bare
SQLite error. Callers on the audio capture path must not use this class
directly at all; they use :class:`voxvault.store.writer.AsyncWriter`.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from ..config import Config
from ..errors import StorageError
from ..types import (
    EngineInfo,
    MeetingState,
    RevisionState,
    Segment,
    Speaker,
    TimelineEntry,
    Track,
)
from . import connection as _conn
from . import schema
from .models import (
    Meeting,
    Origin,
    PublishOutcome,
    Revision,
    RevisionStatus,
    SearchHit,
    TranscriptState,
    now_ms,
    speaker_for,
    to_ms,
)
from .notes import (
    NATURE_NOTE,
    NATURE_TRANSCRIPT,
    NOTE_COLUMNS,
    Note,
    NoteAuthor,
    NoteHit,
    NoteKind,
    SearchScope,
    author_from_row,
    clean_content,
    coerce_kind,
    coerce_scope,
    note_from_row,
)

#: Tracks a meeting is expected to have. Completeness of a revision is decided
#: against this, not against whatever the caller happened to attempt.
EXPECTED_TRACKS: Final[dict[Origin, frozenset[str]]] = {
    Origin.RECORDED: frozenset({Track.MIC.value, Track.SYSTEM.value}),
    Origin.IMPORTED: frozenset({"importada"}),
}

_TIMELINE_COLUMNS = "id, track, speaker, start_ms, end_ms, text"

TimelineCursor = tuple[int, str, int]


class TranscriptStore:
    """Meetings, revisions, segments, timeline, search."""

    def __init__(
        self,
        path: str | Path,
        *,
        busy_timeout_s: float = _conn.DEFAULT_BUSY_TIMEOUT_S,
        migration_timeout_s: float = _conn.DEFAULT_MIGRATION_TIMEOUT_S,
    ) -> None:
        self.path = Path(path)
        self.busy_timeout_s = busy_timeout_s
        self._conn, self.applied_migrations = _conn.open_connection(
            self.path,
            busy_timeout_s=busy_timeout_s,
            migration_timeout_s=migration_timeout_s,
        )
        #: Test seam. Called with a label at named points inside a write
        #: transaction so a test can prove the transaction is the unit of
        #: atomicity. Always ``None`` in production.
        self.fault_hook: Callable[[str], None] | None = None

    @classmethod
    def from_config(cls, config: Config, **kwargs: Any) -> TranscriptStore:
        return cls(config.db_path, **kwargs)

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> TranscriptStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def schema_version(self) -> int:
        return schema.read_version(self._conn)

    def _write(self):  # -> context manager
        return _conn.transaction(self._conn, busy_timeout_s=self.busy_timeout_s)

    def _fault(self, label: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(label)

    # -- meetings --------------------------------------------------------

    def create_meeting(
        self,
        *,
        uid: str,
        title: str,
        started_at: datetime,
        directory: str | Path,
        origin: Origin | str = Origin.RECORDED,
        state: MeetingState | str = MeetingState.RECORDING,
        source_path: str = "",
        duration_ms: int = 0,
    ) -> Meeting:
        moment = now_ms()
        with self._write():
            try:
                self._conn.execute(
                    "INSERT INTO meetings("
                    " uid, title, started_at_ms, duration_ms, state, origin,"
                    " source_path, directory, created_at_ms, updated_at_ms)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        uid,
                        title,
                        to_ms(started_at),
                        duration_ms,
                        str(state),
                        str(origin),
                        source_path,
                        str(directory),
                        moment,
                        moment,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise StorageError(
                    f"Ja existe uma reuniao com o identificador '{uid}': {exc}"
                ) from None
        meeting = self.get_meeting(uid)
        assert meeting is not None
        return meeting

    def get_meeting(self, uid: str) -> Meeting | None:
        row = self._conn.execute(
            "SELECT * FROM meetings WHERE uid = ?", (uid,)
        ).fetchone()
        return None if row is None else _meeting(row)

    def list_meetings(
        self, *, limit: int | None = None, offset: int = 0
    ) -> list[Meeting]:
        sql = "SELECT * FROM meetings ORDER BY started_at_ms DESC, id DESC"
        params: list[Any] = []
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params += [limit, offset]
        return [_meeting(r) for r in self._conn.execute(sql, params)]

    def iter_meetings(self) -> Iterator[Meeting]:
        """Stream every meeting without materialising the list."""
        cursor = self._conn.execute(
            "SELECT * FROM meetings ORDER BY started_at_ms DESC, id DESC"
        )
        for row in cursor:
            yield _meeting(row)

    def rename_meeting(self, uid: str, title: str) -> None:
        with self._write():
            self._update_meeting(uid, title=title)

    def finish_meeting(
        self,
        uid: str,
        *,
        ended_at: datetime,
        duration_ms: int,
        state: MeetingState | str = MeetingState.RECORDED,
    ) -> None:
        with self._write():
            self._update_meeting(
                uid,
                ended_at_ms=to_ms(ended_at),
                duration_ms=duration_ms,
                state=str(state),
            )

    def record_progress(self, uid: str, duration_ms: int) -> None:
        """Persist how much audio a running session has captured so far.

        Small and frequent, and the reason :class:`AsyncWriter` exists: this is
        the write a recording makes while it records, and it must never be on
        the path that would make it wait.
        """
        with self._write():
            self._update_meeting(uid, duration_ms=duration_ms)

    def set_exports_revision(self, uid: str, revision_uid: str) -> None:
        """Record which revision the files on disk were generated from.

        Written only after both files exist. A crash before this point leaves
        the recorded identifier stale, and stale is precisely the signal the
        next startup needs to regenerate.
        """
        with self._write():
            self._update_meeting(uid, exports_revision_uid=revision_uid)

    def set_attempt_state(self, uid: str, attempt_state: str, error: str = "") -> None:
        with self._write():
            self._update_meeting(
                uid, attempt_state=str(attempt_state), attempt_error=error
            )

    def _update_meeting(self, uid: str, **values: Any) -> None:
        values["updated_at_ms"] = now_ms()
        assignments = ", ".join(f"{name} = ?" for name in values)
        cursor = self._conn.execute(
            f"UPDATE meetings SET {assignments} WHERE uid = ?",
            (*values.values(), uid),
        )
        if cursor.rowcount == 0:
            raise StorageError(f"Reuniao '{uid}' nao encontrada no armazenamento.")

    # -- revisions -------------------------------------------------------

    def begin_revision(
        self,
        meeting_uid: str,
        *,
        engine: EngineInfo,
        vocabulary: str = "",
        language: str = "",
        config_fingerprint: str = "",
    ) -> Revision:
        """Open a revision, freezing the engine, configuration and vocabulary.

        Everything that identifies the attempt is captured here and can never
        be rewritten -- a trigger enforces it. An interrupted attempt is
        discarded whole; resuming it means calling this again, which captures
        whatever the configuration is at that later moment.
        """
        meeting = self._require_meeting(meeting_uid)
        uid = uuid.uuid4().hex
        with self._write():
            self._conn.execute(
                "INSERT INTO revisions("
                " uid, meeting_id, status, engine_id, engine_name, engine_model,"
                " engine_device, engine_compute, engine_version,"
                " config_fingerprint, vocabulary, language, started_at_ms)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uid,
                    meeting.id,
                    RevisionStatus.BUILDING.value,
                    engine.identifier(),
                    engine.name,
                    engine.model,
                    engine.device,
                    engine.compute_type,
                    engine.version,
                    config_fingerprint,
                    vocabulary,
                    language,
                    now_ms(),
                ),
            )
        revision = self.revision_by_uid(uid)
        assert revision is not None
        return revision

    def revision(self, revision_id: int) -> Revision | None:
        row = self._conn.execute(
            "SELECT * FROM revisions WHERE id = ?", (revision_id,)
        ).fetchone()
        return None if row is None else _revision(row)

    def revision_by_uid(self, uid: str) -> Revision | None:
        row = self._conn.execute(
            "SELECT * FROM revisions WHERE uid = ?", (uid,)
        ).fetchone()
        return None if row is None else _revision(row)

    def revision_engine(self, revision_id: int) -> EngineInfo:
        """The frozen engine identity of an attempt.

        Every track of an attempt asks the store for this instead of reading
        the live configuration, so a settings change between the first and the
        second track cannot split one revision across two engines.
        """
        revision = self._require_revision(revision_id)
        return EngineInfo(
            name=revision.engine_name,
            model=revision.engine_model,
            compute_type=revision.engine_compute,
            device=revision.engine_device,
            version=revision.engine_version,
        )

    def revisions_of(self, meeting_uid: str) -> list[Revision]:
        meeting = self._require_meeting(meeting_uid)
        return [
            _revision(r)
            for r in self._conn.execute(
                "SELECT * FROM revisions WHERE meeting_id = ? "
                "ORDER BY started_at_ms DESC, id DESC",
                (meeting.id,),
            )
        ]

    def active_revision(self, meeting_uid: str) -> Revision | None:
        row = self._conn.execute(
            "SELECT r.* FROM revisions r"
            " JOIN meetings m ON m.active_revision_id = r.id"
            " WHERE m.uid = ?",
            (meeting_uid,),
        ).fetchone()
        return None if row is None else _revision(row)

    def add_segments(
        self, revision_id: int, track: Track | str, segments: Iterable[Segment]
    ) -> int:
        """Append one track's result to a revision under construction.

        Short by construction: the segments are already in memory when this is
        called. Nothing here reads a file or waits on an engine, because doing
        so would hold the single write lock for the length of that work.
        """
        revision = self._require_revision(revision_id)
        if revision.status is not RevisionStatus.BUILDING:
            raise StorageError(
                f"A revisao {revision.uid} esta em estado '{revision.status}' e "
                f"nao aceita mais segmentos. Uma tentativa concluida e imutavel; "
                f"reprocessar exige uma revisao nova."
            )
        meeting_row = self._conn.execute(
            "SELECT id, origin FROM meetings WHERE id = ?", (revision.meeting_id,)
        ).fetchone()
        speaker = speaker_for(meeting_row["origin"], track)
        rows = [
            (
                revision_id,
                meeting_row["id"],
                str(track),
                speaker,
                s.start_ms,
                s.end_ms,
                s.text,
            )
            for s in segments
        ]
        if not rows:
            return 0
        with self._write():
            # Re-check inside the lock. Another process may have published
            # this revision since the check above, and segments appended to a
            # published revision would change a transcript after the fact.
            status = self._conn.execute(
                "SELECT status FROM revisions WHERE id = ?", (revision_id,)
            ).fetchone()
            if status is None or status[0] != RevisionStatus.BUILDING.value:
                raise StorageError(
                    f"A revisao {revision.uid} deixou de estar em construcao "
                    f"enquanto os segmentos eram gravados. Nada foi gravado."
                )
            self._conn.executemany(
                "INSERT INTO segments("
                " revision_id, meeting_id, track, speaker, start_ms, end_ms, text)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def publish_revision(
        self,
        revision_id: int,
        *,
        tracks_ok: Sequence[Track | str],
        tracks_failed: Sequence[Track | str] = (),
        failure_reason: str = "",
    ) -> PublishOutcome:
        """Make a revision active, atomically, or explain why it was refused.

        Every track's segments start counting at the same instant, because the
        activation is one row update inside one transaction. The search index
        moves in that same transaction, so it can never point at a revision
        that is not the active one.

        A revision that covers less than the active one is **not** published.
        Reprocessing may not destroy the only usable transcript a meeting has;
        the attempt is recorded as failed with its reason and the caller gets a
        message to show the user.
        """
        revision = self._require_revision(revision_id)
        if revision.status is not RevisionStatus.BUILDING:
            raise StorageError(
                f"A revisao {revision.uid} ja foi concluida "
                f"(estado '{revision.status}') e nao pode ser publicada de novo."
            )
        meeting_row = self._conn.execute(
            "SELECT uid, origin, active_revision_id FROM meetings WHERE id = ?",
            (revision.meeting_id,),
        ).fetchone()
        meeting_uid = meeting_row["uid"]
        origin = Origin(meeting_row["origin"])

        new_ok = frozenset(str(t) for t in tracks_ok)
        failed = tuple(sorted(str(t) for t in tracks_failed))
        active = (
            self.revision(meeting_row["active_revision_id"])
            if meeting_row["active_revision_id"] is not None
            else None
        )
        previous_ok = frozenset(active.tracks_ok) if active else frozenset()

        if not new_ok:
            return self._discard(
                revision,
                meeting_uid,
                state=RevisionState.PARTIAL,
                tracks_ok=(),
                tracks_failed=failed,
                reason="todas_as_trilhas_falharam",
                message=(
                    "Nenhuma trilha foi transcrita com sucesso"
                    + (f": {failure_reason}. " if failure_reason else ". ")
                    + (
                        "A revisao anterior permanece ativa e intacta."
                        if active
                        else "Nenhuma revisao foi publicada."
                    )
                ),
                failure_reason=failure_reason,
            )

        expected = EXPECTED_TRACKS[origin]
        state = (
            RevisionState.COMPLETE if new_ok >= expected else RevisionState.PARTIAL
        )

        if not new_ok >= previous_ok:
            missing = ", ".join(sorted(previous_ok - new_ok))
            return self._discard(
                revision,
                meeting_uid,
                state=state,
                tracks_ok=tuple(sorted(new_ok)),
                tracks_failed=failed,
                reason="revisao_anterior_mais_completa",
                message=(
                    f"O reprocessamento nao substituiu a transcricao existente: "
                    f"a tentativa perdeu a(s) trilha(s) {missing}"
                    + (f" ({failure_reason})" if failure_reason else "")
                    + ". A transcricao anterior continua ativa e intacta."
                ),
                failure_reason=failure_reason,
            )

        moment = now_ms()
        with self._write():
            # The index follows the revision inside the same transaction. If
            # anything below fails, the rollback takes the index with it.
            if active is not None:
                self._conn.execute(
                    "DELETE FROM segments_fts WHERE rowid IN "
                    "(SELECT id FROM segments WHERE revision_id = ?)",
                    (active.id,),
                )
                self._conn.execute(
                    "UPDATE revisions SET status = ? WHERE id = ?",
                    (RevisionStatus.SUPERSEDED.value, active.id),
                )
            self._fault("after_index_delete")
            self._conn.execute(
                "INSERT INTO segments_fts(rowid, text, meeting_id)"
                " SELECT id, text, meeting_id FROM segments WHERE revision_id = ?",
                (revision.id,),
            )
            self._fault("after_index_insert")
            self._conn.execute(
                "UPDATE revisions SET status = ?, state = ?, tracks_ok = ?,"
                " tracks_failed = ?, failure_reason = ?, completed_at_ms = ?"
                " WHERE id = ?",
                (
                    RevisionStatus.PUBLISHED.value,
                    state.value,
                    ",".join(sorted(new_ok)),
                    ",".join(failed),
                    failure_reason,
                    moment,
                    revision.id,
                ),
            )
            # Guarded on the revision this decision was made against. If
            # another process activated something else in the meantime, the
            # completeness comparison above was made on stale facts and this
            # publication must not go through on them.
            changed = self._conn.execute(
                "UPDATE meetings SET active_revision_id = ?, transcript_state = ?,"
                " attempt_state = 'nenhuma', attempt_error = '', updated_at_ms = ?"
                " WHERE id = ? AND active_revision_id IS ?",
                (
                    revision.id,
                    (
                        TranscriptState.COMPLETE.value
                        if state is RevisionState.COMPLETE
                        else TranscriptState.PARTIAL.value
                    ),
                    moment,
                    revision.meeting_id,
                    active.id if active else None,
                ),
            ).rowcount
            if changed == 0:
                raise StorageError(
                    f"A revisao ativa da reuniao '{meeting_uid}' mudou durante a "
                    f"publicacao da revisao {revision.uid}. Nada foi publicado; "
                    f"repita a tentativa."
                )
            self._fault("after_activation")

        message = (
            "Transcricao publicada."
            if state is RevisionState.COMPLETE
            else (
                "Transcricao parcial publicada: "
                f"a(s) trilha(s) {', '.join(failed) or 'ausente(s)'} nao foram "
                "transcritas"
                + (f" ({failure_reason})" if failure_reason else "")
                + "."
            )
        )
        return PublishOutcome(
            published=True,
            revision_uid=revision.uid,
            state=state,
            replaced_revision_uid=active.uid if active else None,
            kept_revision_uid=None,
            reason="",
            message=message,
        )

    def _discard(
        self,
        revision: Revision,
        meeting_uid: str,
        *,
        state: RevisionState,
        tracks_ok: tuple[str, ...],
        tracks_failed: tuple[str, ...],
        reason: str,
        message: str,
        failure_reason: str,
    ) -> PublishOutcome:
        """Record an attempt that was not allowed to become active.

        The revision stays in the database as evidence. Nothing about the
        meeting's active revision, its segments or its search index is touched.
        """
        active = self.active_revision(meeting_uid)
        moment = now_ms()
        with self._write():
            self._conn.execute(
                "UPDATE revisions SET status = ?, state = ?, tracks_ok = ?,"
                " tracks_failed = ?, failure_reason = ?, completed_at_ms = ?"
                " WHERE id = ?",
                (
                    RevisionStatus.DISCARDED.value,
                    state.value,
                    ",".join(tracks_ok),
                    ",".join(tracks_failed),
                    failure_reason or message,
                    moment,
                    revision.id,
                ),
            )
            self._conn.execute(
                "UPDATE meetings SET attempt_state = 'falhou', attempt_error = ?,"
                " updated_at_ms = ? WHERE id = ?",
                (message, moment, revision.meeting_id),
            )
        return PublishOutcome(
            published=False,
            revision_uid=revision.uid,
            state=None,
            replaced_revision_uid=None,
            kept_revision_uid=active.uid if active else None,
            reason=reason,
            message=message,
        )

    def discard_revision(self, revision_id: int, reason: str) -> None:
        """Throw away an interrupted attempt whole.

        Resuming is a new attempt with freshly captured configuration, never a
        continuation of this one carrying old settings beside new segments.
        """
        revision = self._require_revision(revision_id)
        with self._write():
            self._conn.execute(
                "DELETE FROM segments WHERE revision_id = ?", (revision.id,)
            )
            self._conn.execute(
                "UPDATE revisions SET status = ?, failure_reason = ?,"
                " completed_at_ms = ? WHERE id = ?",
                (RevisionStatus.DISCARDED.value, reason, now_ms(), revision.id),
            )

    # -- timeline --------------------------------------------------------

    def iter_timeline(
        self,
        meeting_uid: str,
        *,
        after: TimelineCursor | None = None,
        limit: int | None = None,
        revision_id: int | None = None,
    ) -> Iterator[TimelineEntry]:
        """Stream the merged timeline of the active revision.

        A generator over an ordered index scan: a four-hour meeting is read a
        row at a time, and ``after``/``limit`` give keyset pagination on the
        same total order the index provides.

        Exhaust it, or bound it with ``limit``. An abandoned generator leaves a
        read transaction open, and an open read transaction keeps the
        write-ahead log from being checkpointed.
        """
        if revision_id is None:
            row = self._conn.execute(
                "SELECT active_revision_id FROM meetings WHERE uid = ?",
                (meeting_uid,),
            ).fetchone()
            if row is None:
                raise StorageError(
                    f"Reuniao '{meeting_uid}' nao encontrada no armazenamento."
                )
            revision_id = row["active_revision_id"]
            if revision_id is None:
                return
        sql = f"SELECT {_TIMELINE_COLUMNS} FROM segments WHERE revision_id = ?"
        params: list[Any] = [revision_id]
        if after is not None:
            sql += " AND (start_ms, track, id) > (?, ?, ?)"
            params += [after[0], str(after[1]), after[2]]
        sql += " ORDER BY start_ms, track, id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cursor = self._conn.execute(sql, params)
        cursor.arraysize = 256
        for row in cursor:
            yield _entry(row)

    def timeline(
        self, meeting_uid: str, *, revision_id: int | None = None
    ) -> list[TimelineEntry]:
        return list(self.iter_timeline(meeting_uid, revision_id=revision_id))

    @staticmethod
    def cursor_of(entry: TimelineEntry) -> TimelineCursor:
        return (entry.start_ms, str(entry.track), entry.segment_id)

    # -- notes -----------------------------------------------------------

    def create_note(
        self,
        meeting_uid: str,
        *,
        kind: NoteKind | str,
        content: str,
        author: NoteAuthor,
        uid: str | None = None,
    ) -> Note:
        """Record one reading of a meeting, beside the meeting and never in it.

        Nothing about the meeting, its revisions, its segments or its audio is
        touched: the only statements here insert into ``notes`` and its index.

        Several notes of the same type may live on one meeting, and this is
        the reason there is no upsert. A second summary is a second opinion,
        possibly written months later by a different client; making it
        overwrite the first would destroy work that nothing else holds a copy
        of.

        The meeting is looked up inside the write transaction rather than
        before it, so "the meeting exists" is decided at the same instant the
        note is written and not a moment earlier.
        """
        note_kind = coerce_kind(kind)
        body = clean_content(content)
        note_uid = uid or uuid.uuid4().hex
        moment = now_ms()
        with self._write():
            row = self._conn.execute(
                "SELECT id FROM meetings WHERE uid = ?", (meeting_uid,)
            ).fetchone()
            if row is None:
                raise StorageError(
                    f"Reuniao '{meeting_uid}' nao encontrada no armazenamento. "
                    f"Nenhuma nota foi criada."
                )
            try:
                cursor = self._conn.execute(
                    "INSERT INTO notes("
                    " uid, meeting_id, kind, content, author_kind, author_client,"
                    " created_at_ms, updated_at_ms)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        note_uid,
                        row["id"],
                        note_kind.value,
                        body,
                        author.kind.value,
                        author.client,
                        moment,
                        moment,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise StorageError(
                    f"Nao foi possivel gravar a nota '{note_uid}': {exc}"
                ) from None
            # The index moves inside the same transaction as the row, exactly
            # as it does when a revision is published. A rollback takes both.
            self._conn.execute(
                "INSERT INTO notes_fts(rowid, content, meeting_id) VALUES (?, ?, ?)",
                (cursor.lastrowid, body, row["id"]),
            )
        note = self.get_note(note_uid)
        assert note is not None
        return note

    def update_note(self, note_uid: str, *, content: str) -> Note:
        """Replace a note's text, keeping everything that identifies it.

        The UPDATE names three columns and no others, which is what preserves
        the identifier, the original authorship and the creation instant. Only
        the last-changed instant moves.
        """
        body = clean_content(content)
        moment = now_ms()
        with self._write():
            row = self._conn.execute(
                "SELECT id FROM notes WHERE uid = ?", (note_uid,)
            ).fetchone()
            if row is None:
                raise StorageError(
                    f"Nota '{note_uid}' nao encontrada no armazenamento. "
                    f"Nada foi alterado."
                )
            self._conn.execute(
                "UPDATE notes SET content = ?, updated_at_ms = ? WHERE id = ?",
                (body, moment, row["id"]),
            )
            self._conn.execute(
                "DELETE FROM notes_fts WHERE rowid = ?", (row["id"],)
            )
            self._conn.execute(
                "INSERT INTO notes_fts(rowid, content, meeting_id)"
                " SELECT id, content, meeting_id FROM notes WHERE id = ?",
                (row["id"],),
            )
        note = self.get_note(note_uid)
        assert note is not None
        return note

    def delete_note(self, note_uid: str) -> None:
        """Remove one note. The index follows through the delete trigger."""
        with self._write():
            cursor = self._conn.execute(
                "DELETE FROM notes WHERE uid = ?", (note_uid,)
            )
            if cursor.rowcount == 0:
                raise StorageError(
                    f"Nota '{note_uid}' nao encontrada no armazenamento. "
                    f"Nada foi removido."
                )

    def get_note(self, note_uid: str) -> Note | None:
        row = self._conn.execute(
            f"SELECT {NOTE_COLUMNS} FROM notes n"
            f" JOIN meetings m ON m.id = n.meeting_id"
            f" WHERE n.uid = ?",
            (note_uid,),
        ).fetchone()
        return None if row is None else note_from_row(row)

    def notes_of(
        self,
        meeting_uid: str,
        *,
        kind: NoteKind | str | None = None,
        limit: int | None = None,
    ) -> list[Note]:
        """Every note of a meeting, oldest first.

        Chronological and not by relevance: several notes of the same type are
        allowed, so the order in which they were written is the only thing
        that says which reading came after which.
        """
        self._require_meeting(meeting_uid)
        sql = [
            f"SELECT {NOTE_COLUMNS} FROM notes n",
            " JOIN meetings m ON m.id = n.meeting_id",
            " WHERE m.uid = ?",
        ]
        params: list[Any] = [meeting_uid]
        if kind is not None:
            sql.append("   AND n.kind = ?")
            params.append(coerce_kind(kind).value)
        sql.append(" ORDER BY n.created_at_ms, n.id")
        if limit is not None:
            sql.append(" LIMIT ?")
            params.append(limit)
        return [note_from_row(r) for r in self._conn.execute("\n".join(sql), params)]

    def resolve_note_uid(self, prefix: str) -> str:
        """Expand a unique prefix of a note identifier, as git expands a hash.

        ``substr`` and not ``LIKE``: a prefix is a literal, and ``LIKE`` would
        read a stray ``%`` in it as a wildcard.
        """
        if not prefix:
            raise StorageError("Informe ao menos um caractere do identificador.")
        matches = [
            r[0]
            for r in self._conn.execute(
                "SELECT uid FROM notes WHERE substr(uid, 1, ?) = ? ORDER BY uid",
                (len(prefix), prefix),
            )
        ]
        if not matches:
            raise StorageError(f"Nenhuma nota comeca com '{prefix}'.")
        if len(matches) > 1:
            raise StorageError(
                f"'{prefix}' e ambiguo: {len(matches)} notas comecam assim."
            )
        return matches[0]

    # -- search ----------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        scope: SearchScope | str = SearchScope.TRANSCRIPTS,
        meeting_uid: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        context_tokens: int = 12,
    ) -> list[SearchHit | NoteHit]:
        """Full-text search over the history, transcripts and notes alike.

        Case and accents are irrelevant in both directions, for notes exactly
        as for segments: both indexes strip diacritics from the stored text
        and from the query, so "acao" finds "ação" and "ação" finds "acao".

        The user's words are quoted before reaching FTS5, so a hyphen, a colon
        or a stray quote is searched for rather than parsed as operator syntax.

        ``scope`` restricts the search to transcripts, to notes, or takes
        both. The result is a mixed list; every element answers ``nature``
        with ``'transcricao'`` or ``'nota'``, and a note result carries its
        identifier, its type and its authorship. A note result has no instant,
        no track and no speaker, because a note has none.

        The default is transcripts, which is what this method meant before
        notes existed and what the surfaces that already call it render.
        Anything that wants notes asks for them.

        Relevance is bm25 within each index. The two corpora differ in typical
        length, so the two scores are comparable in the loose sense that both
        rank better matches first, not in the strict sense of one scale.
        """
        wanted = coerce_scope(scope)
        match = _fts_query(query)
        if not match:
            return []
        arms: list[str] = []
        params: list[Any] = []
        sources = []
        if wanted is not SearchScope.NOTES:
            sources.append(_TRANSCRIPT_ARM)
        if wanted is not SearchScope.TRANSCRIPTS:
            sources.append(_NOTE_ARM)
        for template in sources:
            arm, arm_params = _search_arm(
                template,
                context_tokens=context_tokens,
                match=match,
                meeting_uid=meeting_uid,
                since=since,
                until=until,
            )
            arms.append(arm)
            params += arm_params
        sql = (
            "SELECT * FROM (\n"
            + "\nUNION ALL\n".join(arms)
            + "\n)"
            # A total order, so two calls never disagree about the page they
            # return: relevance, then recency, then the identifier.
            + "\n ORDER BY relevancia, meeting_started_at_ms DESC, nature,"
            "\n          COALESCE(start_ms, created_at_ms),"
            "\n          COALESCE(segment_id, 0), COALESCE(note_uid, '')"
            "\n LIMIT ?"
        )
        params.append(limit)
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            raise StorageError(
                f"Consulta de busca invalida ({query!r}): {exc}"
            ) from None
        return [_hit(r) for r in rows]

    # -- diagnostics -----------------------------------------------------

    def tables(self) -> set[str]:
        return {
            r[0]
            for r in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }

    def integrity_check(self) -> str:
        return self._conn.execute("PRAGMA integrity_check").fetchone()[0]

    def orphan_index_rows(self) -> list[int]:
        """Rowids in the search index that do not belong to an active revision.

        Must always be empty. If it is not, the index is pointing at text no
        query should ever return.
        """
        return [
            r[0]
            for r in self._conn.execute(
                "SELECT f.rowid FROM segments_fts f"
                " LEFT JOIN segments s ON s.id = f.rowid"
                " LEFT JOIN meetings m ON m.id = s.meeting_id"
                " WHERE s.id IS NULL OR m.active_revision_id IS NOT s.revision_id"
            )
        ]

    def unindexed_active_segments(self) -> list[int]:
        """Active segments missing from the search index. Must always be empty."""
        return [
            r[0]
            for r in self._conn.execute(
                "SELECT s.id FROM segments s"
                " JOIN meetings m ON m.active_revision_id = s.revision_id"
                " LEFT JOIN segments_fts f ON f.rowid = s.id"
                " WHERE f.rowid IS NULL"
            )
        ]

    def orphan_note_index_rows(self) -> list[int]:
        """Rowids in the note index with no note behind them. Always empty.

        An inner join hides an orphan from every query, which is exactly why
        it needs a diagnostic: it would rot unnoticed otherwise.
        """
        return [
            r[0]
            for r in self._conn.execute(
                "SELECT f.rowid FROM notes_fts f"
                " LEFT JOIN notes n ON n.id = f.rowid"
                " WHERE n.id IS NULL"
            )
        ]

    def unindexed_notes(self) -> list[int]:
        """Notes missing from the note index. Must always be empty."""
        return [
            r[0]
            for r in self._conn.execute(
                "SELECT n.id FROM notes n"
                " LEFT JOIN notes_fts f ON f.rowid = n.id"
                " WHERE f.rowid IS NULL"
            )
        ]

    # -- internals -------------------------------------------------------

    def _require_meeting(self, uid: str) -> Meeting:
        meeting = self.get_meeting(uid)
        if meeting is None:
            raise StorageError(f"Reuniao '{uid}' nao encontrada no armazenamento.")
        return meeting

    def _require_revision(self, revision_id: int) -> Revision:
        revision = self.revision(revision_id)
        if revision is None:
            raise StorageError(f"Revisao {revision_id} nao encontrada.")
        return revision


#: The two halves of a search, projected onto one row shape so they can be
#: taken together. Every column is aliased in both arms: in a compound SELECT
#: the names come from the first one, and the first one depends on the scope.
#:
#: The slots a note cannot fill are NULL rather than zero. A note has no
#: instant and no speaker, and a zero there is how a summary ends up rendered
#: as if someone had said it at the start of the meeting.
_TRANSCRIPT_ARM: Final = """
SELECT 'transcricao'           AS nature,
       m.uid                   AS meeting_uid,
       m.title                 AS meeting_title,
       m.started_at_ms         AS meeting_started_at_ms,
       bm25(segments_fts)      AS relevancia,
       s.id                    AS segment_id,
       s.start_ms              AS start_ms,
       s.end_ms                AS end_ms,
       s.track                 AS track,
       s.speaker               AS speaker,
       NULL                    AS note_uid,
       NULL                    AS kind,
       NULL                    AS author_kind,
       NULL                    AS author_client,
       NULL                    AS created_at_ms,
       NULL                    AS updated_at_ms,
       snippet(segments_fts, 0, '[', ']', '...', {context}) AS excerpt,
       s.text                  AS text
  FROM segments_fts
  JOIN segments s ON s.id = segments_fts.rowid
  JOIN meetings m ON m.id = s.meeting_id
 WHERE segments_fts MATCH ?
   AND s.revision_id = m.active_revision_id"""

_NOTE_ARM: Final = """
SELECT 'nota'                  AS nature,
       m.uid                   AS meeting_uid,
       m.title                 AS meeting_title,
       m.started_at_ms         AS meeting_started_at_ms,
       bm25(notes_fts)         AS relevancia,
       NULL                    AS segment_id,
       NULL                    AS start_ms,
       NULL                    AS end_ms,
       NULL                    AS track,
       NULL                    AS speaker,
       n.uid                   AS note_uid,
       n.kind                  AS kind,
       n.author_kind           AS author_kind,
       n.author_client         AS author_client,
       n.created_at_ms         AS created_at_ms,
       n.updated_at_ms         AS updated_at_ms,
       snippet(notes_fts, 0, '[', ']', '...', {context}) AS excerpt,
       n.content               AS text
  FROM notes_fts
  JOIN notes n ON n.id = notes_fts.rowid
  JOIN meetings m ON m.id = n.meeting_id
 WHERE notes_fts MATCH ?"""


def _search_arm(
    template: str,
    *,
    context_tokens: int,
    match: str,
    meeting_uid: str | None,
    since: datetime | None,
    until: datetime | None,
) -> tuple[str, list[Any]]:
    """One half of a search, with the filters that apply to both halves.

    ``context_tokens`` is interpolated because FTS5 takes the snippet width as
    a literal and not as a binding; it is forced through ``int`` first.
    """
    sql = [template.format(context=int(context_tokens))]
    params: list[Any] = [match]
    if meeting_uid is not None:
        sql.append("   AND m.uid = ?")
        params.append(meeting_uid)
    if since is not None:
        sql.append("   AND m.started_at_ms >= ?")
        params.append(to_ms(since))
    if until is not None:
        sql.append("   AND m.started_at_ms <= ?")
        params.append(to_ms(until))
    return "\n".join(sql), params


def _hit(row: sqlite3.Row) -> SearchHit | NoteHit:
    """One search row as whichever kind of result it actually is."""
    if row["nature"] == NATURE_NOTE:
        return NoteHit(
            meeting_uid=row["meeting_uid"],
            meeting_title=row["meeting_title"],
            meeting_started_at_ms=row["meeting_started_at_ms"],
            note_uid=row["note_uid"],
            kind=NoteKind(row["kind"]),
            author=author_from_row(row),
            created_at_ms=row["created_at_ms"],
            updated_at_ms=row["updated_at_ms"],
            excerpt=row["excerpt"],
            text=row["text"],
        )
    return SearchHit(
        meeting_uid=row["meeting_uid"],
        meeting_title=row["meeting_title"],
        meeting_started_at_ms=row["meeting_started_at_ms"],
        segment_id=row["segment_id"],
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
        track=row["track"],
        speaker=row["speaker"],
        excerpt=row["excerpt"],
        text=row["text"],
    )


def _fts_query(raw: str) -> str:
    """Turn a person's words into an FTS5 MATCH expression.

    Each token becomes a quoted string, which makes every character literal.
    Tokens are ANDed, which is what a person means by typing two words. A
    token with nothing a tokenizer would keep -- a lone hyphen, a stray quote
    -- is dropped, because an empty phrase is not a search term.
    """
    quoted = []
    for token in raw.split():
        cleaned = token.replace('"', "")
        if any(ch.isalnum() for ch in cleaned):
            quoted.append('"' + cleaned + '"')
    return " AND ".join(quoted)


def _meeting(row: sqlite3.Row) -> Meeting:
    return Meeting(
        id=row["id"],
        uid=row["uid"],
        title=row["title"],
        started_at_ms=row["started_at_ms"],
        ended_at_ms=row["ended_at_ms"],
        duration_ms=row["duration_ms"],
        state=MeetingState(row["state"]),
        attempt_state=row["attempt_state"],
        attempt_error=row["attempt_error"],
        transcript_state=TranscriptState(row["transcript_state"]),
        origin=Origin(row["origin"]),
        source_path=row["source_path"],
        directory=row["directory"],
        active_revision_id=row["active_revision_id"],
        exports_revision_uid=row["exports_revision_uid"],
    )


def _revision(row: sqlite3.Row) -> Revision:
    raw_state = row["state"]
    return Revision(
        id=row["id"],
        uid=row["uid"],
        meeting_id=row["meeting_id"],
        status=RevisionStatus(row["status"]),
        state=RevisionState(raw_state) if raw_state else None,
        engine_id=row["engine_id"],
        engine_name=row["engine_name"],
        engine_model=row["engine_model"],
        engine_device=row["engine_device"],
        engine_compute=row["engine_compute"],
        engine_version=row["engine_version"],
        config_fingerprint=row["config_fingerprint"],
        vocabulary=row["vocabulary"],
        language=row["language"],
        tracks_ok=tuple(t for t in row["tracks_ok"].split(",") if t),
        tracks_failed=tuple(t for t in row["tracks_failed"].split(",") if t),
        failure_reason=row["failure_reason"],
        started_at_ms=row["started_at_ms"],
        completed_at_ms=row["completed_at_ms"],
    )


def _entry(row: sqlite3.Row) -> TimelineEntry:
    # Imported audio has a track and a speaker that ``types.py`` does not
    # declare, so both fields fall back to the stored text. They compare and
    # render identically -- Track and Speaker are string enums.
    try:
        track: Any = Track(row["track"])
    except ValueError:
        track = row["track"]
    try:
        speaker: Any = Speaker(row["speaker"])
    except ValueError:
        speaker = row["speaker"]
    return TimelineEntry(
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
        text=row["text"],
        track=track,
        speaker=speaker,
        segment_id=row["id"],
    )
