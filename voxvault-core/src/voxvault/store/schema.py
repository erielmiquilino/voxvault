"""Database schema and the migrations that build it.

Each migration is a tuple of individual statements, never a script: a script
would issue an implicit COMMIT and break the single transaction that makes
migration atomic and serialisable between processes.

No migration may drop a meeting, a segment, a note or an audio reference.
Adding columns and rebuilding derived indexes is allowed; discarding recorded
material is not.
"""

from __future__ import annotations

import sqlite3
from typing import Final

#: Highest schema version this build understands. A database above it is
#: refused, because a newer build may have given an existing column a new
#: meaning that this one would silently misread.
SCHEMA_VERSION: Final = 3

_V1_CORE: Final[tuple[str, ...]] = (
    """
    CREATE TABLE meetings (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        uid                  TEXT    NOT NULL UNIQUE,
        title                TEXT    NOT NULL,
        started_at_ms        INTEGER NOT NULL,
        ended_at_ms          INTEGER,
        duration_ms          INTEGER NOT NULL DEFAULT 0,
        state                TEXT    NOT NULL,
        attempt_state        TEXT    NOT NULL DEFAULT 'nenhuma',
        attempt_error        TEXT    NOT NULL DEFAULT '',
        transcript_state     TEXT    NOT NULL DEFAULT 'nenhuma'
                                     CHECK (transcript_state IN
                                            ('nenhuma', 'parcial', 'completa')),
        origin               TEXT    NOT NULL
                                     CHECK (origin IN ('gravada', 'importada')),
        source_path          TEXT    NOT NULL DEFAULT '',
        directory            TEXT    NOT NULL,
        active_revision_id   INTEGER REFERENCES revisions(id) ON DELETE SET NULL,
        exports_revision_uid TEXT    NOT NULL DEFAULT '',
        created_at_ms        INTEGER NOT NULL,
        updated_at_ms        INTEGER NOT NULL
    )
    """,
    "CREATE INDEX idx_meetings_started ON meetings(started_at_ms DESC)",
    "CREATE INDEX idx_meetings_attempt ON meetings(attempt_state)",
    """
    CREATE TABLE revisions (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        uid                TEXT    NOT NULL UNIQUE,
        meeting_id         INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        status             TEXT    NOT NULL
                                   CHECK (status IN ('em_construcao', 'publicada',
                                                     'substituida', 'descartada')),
        state              TEXT    NOT NULL DEFAULT ''
                                   CHECK (state IN ('', 'completa', 'parcial')),
        engine_id          TEXT    NOT NULL,
        engine_name        TEXT    NOT NULL DEFAULT '',
        engine_model       TEXT    NOT NULL DEFAULT '',
        engine_device      TEXT    NOT NULL DEFAULT '',
        engine_compute     TEXT    NOT NULL DEFAULT '',
        engine_version     TEXT    NOT NULL DEFAULT '',
        config_fingerprint TEXT    NOT NULL DEFAULT '',
        vocabulary         TEXT    NOT NULL DEFAULT '',
        language           TEXT    NOT NULL DEFAULT '',
        tracks_ok          TEXT    NOT NULL DEFAULT '',
        tracks_failed      TEXT    NOT NULL DEFAULT '',
        failure_reason     TEXT    NOT NULL DEFAULT '',
        started_at_ms      INTEGER NOT NULL,
        completed_at_ms    INTEGER
    )
    """,
    "CREATE INDEX idx_revisions_meeting ON revisions(meeting_id, started_at_ms DESC)",
    # The identity of a revision is frozen at the start of the attempt. A
    # trigger makes that structural: no code path, present or future, can
    # rewrite it halfway through and leave the revision describing itself
    # wrongly.
    """
    CREATE TRIGGER revisions_identity_immutable
    BEFORE UPDATE ON revisions
    FOR EACH ROW WHEN
           OLD.engine_id          IS NOT NEW.engine_id
        OR OLD.engine_name        IS NOT NEW.engine_name
        OR OLD.engine_model       IS NOT NEW.engine_model
        OR OLD.engine_device      IS NOT NEW.engine_device
        OR OLD.engine_compute     IS NOT NEW.engine_compute
        OR OLD.engine_version     IS NOT NEW.engine_version
        OR OLD.config_fingerprint IS NOT NEW.config_fingerprint
        OR OLD.vocabulary         IS NOT NEW.vocabulary
        OR OLD.language           IS NOT NEW.language
    BEGIN
        SELECT RAISE(ABORT, 'revisao: motor, configuracao e vocabulario sao congelados no inicio da tentativa e nao podem ser alterados');
    END
    """,
    """
    CREATE TABLE segments (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        revision_id INTEGER NOT NULL REFERENCES revisions(id) ON DELETE CASCADE,
        meeting_id  INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        track       TEXT    NOT NULL
                            CHECK (track IN ('mic', 'system', 'importada')),
        speaker     TEXT    NOT NULL,
        start_ms    INTEGER NOT NULL CHECK (start_ms >= 0),
        end_ms      INTEGER NOT NULL,
        text        TEXT    NOT NULL,
        CHECK (end_ms > start_ms)
    )
    """,
    # The ordering index is the whole ordering contract in one line: start
    # instant, then track, then id. It is covering, so a page of the timeline
    # is an index-only range scan and never a sort.
    "CREATE INDEX idx_segments_order ON segments(revision_id, start_ms, track, id)",
    "CREATE INDEX idx_segments_meeting ON segments(meeting_id, revision_id)",
    """
    CREATE TABLE schema_migrations (
        version        INTEGER PRIMARY KEY,
        applied_at_ms  INTEGER NOT NULL,
        applied_by_pid INTEGER NOT NULL
    )
    """,
)

#: The search index is a derived artifact: it can always be rebuilt from the
#: segments of the active revisions, which is exactly what this migration does
#: for a database created before it existed.
#:
#: ``remove_diacritics 2`` strips every diacritic, cedilla included, from both
#: the indexed text and the query. That is what makes "acao" find "ação" and
#: "ação" find "acao" -- in Portuguese both spellings show up in transcribed
#: speech, and a search that only works one way is a search that fails.
_V2_SEARCH: Final[tuple[str, ...]] = (
    """
    CREATE VIRTUAL TABLE segments_fts USING fts5(
        text,
        meeting_id UNINDEXED,
        tokenize = 'unicode61 remove_diacritics 2'
    )
    """,
    """
    INSERT INTO segments_fts(rowid, text, meeting_id)
    SELECT s.id, s.text, s.meeting_id
      FROM segments s
      JOIN meetings m ON m.active_revision_id = s.revision_id
    """,
)

#: Notes are purely additive: nothing above is touched, so a database that
#: already holds meetings and segments crosses this migration unchanged.
#:
#: Notes get an index of their own rather than sharing ``segments_fts``. The
#: two tables number their rows independently, so one shared index would need
#: a composite rowid to tell note 7 from segment 7 -- and bolting that onto an
#: index that already works is a good way to lose a transcript to a collision.
#:
#: The tokenizer is copied from the segment index on purpose, not by accident:
#: a note that only answers to accented spelling while the transcript answers
#: to both would be a search that behaves differently depending on what it
#: finds, which is worse than one that simply fails.
#:
#: Two rules are CHECK constraints rather than Python: the closed set of note
#: types, and the requirement that an automated author name its client. Both
#: have to hold for rows written by builds that do not exist yet.
_V3_NOTES: Final[tuple[str, ...]] = (
    """
    CREATE TABLE notes (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        uid           TEXT    NOT NULL UNIQUE,
        meeting_id    INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
        kind          TEXT    NOT NULL
                              CHECK (kind IN ('resumo', 'decisoes',
                                              'pendencias', 'livre')),
        content       TEXT    NOT NULL,
        author_kind   TEXT    NOT NULL
                              CHECK (author_kind IN ('usuario', 'agente')),
        author_client TEXT    NOT NULL DEFAULT '',
        created_at_ms INTEGER NOT NULL,
        updated_at_ms INTEGER NOT NULL,
        CHECK (author_kind <> 'agente' OR author_client <> '')
    )
    """,
    # Covering for the listing order the notes of a meeting are read in:
    # creation instant, tie-broken by row, which is total.
    "CREATE INDEX idx_notes_meeting ON notes(meeting_id, created_at_ms, id)",
    """
    CREATE VIRTUAL TABLE notes_fts USING fts5(
        content,
        meeting_id UNINDEXED,
        tokenize = 'unicode61 remove_diacritics 2'
    )
    """,
    # The index follows a deletion structurally. The store deletes notes one
    # at a time and could do this itself, but the foreign key above also
    # deletes them in bulk when a meeting goes, and that path runs inside
    # SQLite where no Python statement of ours is involved.
    """
    CREATE TRIGGER notes_fts_follows_delete
    AFTER DELETE ON notes
    FOR EACH ROW
    BEGIN
        DELETE FROM notes_fts WHERE rowid = OLD.id;
    END
    """,
)

MIGRATIONS: Final[tuple[tuple[int, tuple[str, ...]], ...]] = (
    (1, _V1_CORE),
    (2, _V2_SEARCH),
    (3, _V3_NOTES),
)

#: Tables a fully migrated database must contain. Used by the store's own
#: consistency check and by the schema test.
EXPECTED_TABLES: Final = frozenset(
    {
        "meetings",
        "revisions",
        "segments",
        "segments_fts",
        "notes",
        "notes_fts",
        "schema_migrations",
    }
)


def read_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def apply_migrations(
    conn: sqlite3.Connection, *, target: int = SCHEMA_VERSION, pid: int = 0
) -> list[int]:
    """Apply every pending migration up to ``target``.

    The caller owns the transaction. This function never begins or commits
    one, because migration atomicity and the cross-process lock are the same
    transaction and splitting them would open a window where a second process
    sees a half-migrated schema.
    """
    current = read_version(conn)
    applied: list[int] = []
    for version, statements in MIGRATIONS:
        if version <= current or version > target:
            continue
        for statement in statements:
            conn.execute(statement)
        # PRAGMA user_version takes no parameter binding; the value is an int
        # literal from this module, never from input.
        conn.execute(f"PRAGMA user_version = {int(version)}")
        applied.append(version)
    if applied:
        now = _now_ms()
        conn.executemany(
            "INSERT INTO schema_migrations(version, applied_at_ms, applied_by_pid) "
            "VALUES (?, ?, ?)",
            [(v, now, pid) for v in applied],
        )
    return applied


def _now_ms() -> int:
    from .models import now_ms

    return now_ms()
