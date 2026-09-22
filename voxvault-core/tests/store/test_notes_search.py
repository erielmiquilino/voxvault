"""Tasks 1.6 and 1.7: notes in the history search, told apart from transcripts.

The point of a search over both is that a term said in the meeting and a term
written about the meeting are both findable -- and that no reader can confuse
one for the other afterwards. So every assertion here is about a result's
nature as much as about its presence.

Portuguese accents get the same treatment as in ``test_search.py``: a note that
only answered to one spelling would be a search that works for transcripts and
half-works for notes, which is worse than one that plainly does not exist.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import make_meeting, transcribe

from voxvault.errors import StorageError
from voxvault.store import (
    NATURE_NOTE,
    NATURE_TRANSCRIPT,
    NoteAuthor,
    NoteAuthorKind,
    NoteHit,
    NoteKind,
    SearchHit,
    SearchScope,
    TranscriptStore,
)
from voxvault.types import EngineInfo, Segment

AGENT = NoteAuthor.agent("claude-desktop")


@pytest.fixture
def history(store: TranscriptStore, tmp_path: Path, engine: EngineInfo):
    """Two meetings. "orcamento" is said in one and written about in both.

    The word is deliberately planted on both sides so that a scope of "both"
    has something to keep apart, and so that restricting the scope has
    something to leave out.
    """
    make_meeting(
        store,
        tmp_path,
        uid="reuniao-1",
        title="Planejamento",
        started_at=datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
    )
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 2000, "precisamos revisar o orçamento de janeiro")],
        system=[Segment(2500, 4000, "combinado, revisamos na sexta")],
    )
    make_meeting(
        store,
        tmp_path,
        uid="reuniao-2",
        title="Retrospectiva",
        started_at=datetime(2026, 2, 15, 9, 0, tzinfo=UTC),
    )
    transcribe(
        store,
        "reuniao-2",
        engine,
        mic=[Segment(0, 2000, "nada a declarar sobre o tema")],
        system=[Segment(2500, 4000, "encerrado")],
    )

    store.create_note(
        "reuniao-1",
        kind=NoteKind.SUMMARY,
        content="O orcamento de janeiro ficou para a sexta.",
        author=AGENT,
    )
    store.create_note(
        "reuniao-2",
        kind=NoteKind.PENDING,
        content="Ninguem trouxe o orçamento revisado.",
        author=NoteAuthor.user(),
    )
    return store


def natures(hits) -> list[str]:
    return sorted(h.nature for h in hits)


# -- one term, two places ------------------------------------------------


def test_a_term_in_a_note_and_in_a_transcript_comes_back_from_both(
    history: TranscriptStore,
) -> None:
    hits = history.search("orcamento", scope=SearchScope.BOTH)

    transcripts = [h for h in hits if h.nature == NATURE_TRANSCRIPT]
    notes = [h for h in hits if h.nature == NATURE_NOTE]
    assert len(transcripts) == 1, "a ocorrencia na transcricao sumiu"
    assert len(notes) == 2, "as ocorrencias nas notas sumiram"

    assert transcripts[0].meeting_uid == "reuniao-1"
    assert {h.meeting_uid for h in notes} == {"reuniao-1", "reuniao-2"}


def test_a_result_says_whether_it_is_a_note_or_a_transcript(
    history: TranscriptStore,
) -> None:
    hits = history.search("orcamento", scope="ambos")
    assert natures(hits) == [NATURE_NOTE, NATURE_NOTE, NATURE_TRANSCRIPT]
    for hit in hits:
        assert isinstance(hit, NoteHit if hit.nature == NATURE_NOTE else SearchHit)


def test_a_note_result_carries_its_type_its_authorship_and_its_identifier(
    history: TranscriptStore,
) -> None:
    by_meeting = {
        h.meeting_uid: h
        for h in history.search("orcamento", scope=SearchScope.NOTES)
    }

    first = by_meeting["reuniao-1"]
    assert first.kind is NoteKind.SUMMARY
    assert first.author.kind is NoteAuthorKind.AGENT
    assert first.author.client == "claude-desktop"

    second = by_meeting["reuniao-2"]
    assert second.kind is NoteKind.PENDING
    assert second.author.kind is NoteAuthorKind.USER

    # The identifier is what makes the note reachable for an update later.
    stored = {n.uid for n in history.notes_of("reuniao-1")}
    assert first.note_uid in stored
    assert history.get_note(first.note_uid).content == first.text


def test_a_note_result_carries_the_meeting_and_a_marked_excerpt(
    history: TranscriptStore,
) -> None:
    hit = history.search("orcamento", scope="notas", meeting_uid="reuniao-1")[0]
    assert hit.meeting_uid == "reuniao-1"
    assert hit.meeting_title == "Planejamento"
    assert hit.meeting_started_at.year == 2026
    assert "[" in hit.excerpt and "]" in hit.excerpt
    assert hit.created_at_ms > 0
    assert hit.updated_at_ms == hit.created_at_ms


def test_a_note_result_has_no_instant_track_or_speaker(
    history: TranscriptStore,
) -> None:
    """A note was not said at a moment by somebody, and does not pretend to be.

    A zero here is how a summary ends up rendered as a line of the timeline.
    """
    hit = history.search("orcamento", scope="notas")[0]
    for absent in ("start_ms", "end_ms", "track", "speaker", "segment_id"):
        assert not hasattr(hit, absent), f"nota nao deveria expor '{absent}'"


# -- the three scopes ----------------------------------------------------


def test_scope_transcripts_returns_only_transcripts(
    history: TranscriptStore,
) -> None:
    hits = history.search("orcamento", scope=SearchScope.TRANSCRIPTS)
    assert natures(hits) == [NATURE_TRANSCRIPT]
    assert hits[0].meeting_uid == "reuniao-1"


def test_scope_notes_returns_only_notes(history: TranscriptStore) -> None:
    hits = history.search("orcamento", scope=SearchScope.NOTES)
    assert natures(hits) == [NATURE_NOTE, NATURE_NOTE]


def test_scope_both_returns_both(history: TranscriptStore) -> None:
    hits = history.search("orcamento", scope=SearchScope.BOTH)
    assert natures(hits) == [NATURE_NOTE, NATURE_NOTE, NATURE_TRANSCRIPT]


def test_the_scope_accepts_the_plain_strings_a_client_would_send(
    history: TranscriptStore,
) -> None:
    assert history.search("orcamento", scope="transcricoes") == history.search(
        "orcamento", scope=SearchScope.TRANSCRIPTS
    )
    assert history.search("orcamento", scope="notas") == history.search(
        "orcamento", scope=SearchScope.NOTES
    )
    assert history.search("orcamento", scope="ambos") == history.search(
        "orcamento", scope=SearchScope.BOTH
    )


def test_an_unknown_scope_is_refused_listing_the_accepted_ones(
    history: TranscriptStore,
) -> None:
    with pytest.raises(StorageError) as caught:
        history.search("orcamento", scope="tudo")
    message = str(caught.value)
    assert "tudo" in message
    for scope in SearchScope:
        assert scope.value in message


def test_the_default_scope_is_transcripts(history: TranscriptStore) -> None:
    """Documented, because the surfaces that predate notes rely on it.

    ``voxvault search`` renders a result as an instant plus a speaker, which a
    note does not have. Changing this default is a change to those surfaces
    too, and should fail here rather than at the terminal.
    """
    assert history.search("orcamento") == history.search(
        "orcamento", scope=SearchScope.TRANSCRIPTS
    )


# -- accents, in both directions -----------------------------------------


def test_an_unaccented_query_finds_accented_note_text(
    history: TranscriptStore,
) -> None:
    # "orçamento revisado" is stored accented, in a note.
    hits = history.search("orcamento revisado", scope="notas")
    assert [h.meeting_uid for h in hits] == ["reuniao-2"]


def test_an_accented_query_finds_unaccented_note_text(
    history: TranscriptStore,
) -> None:
    # "O orcamento de janeiro" is stored unaccented, in a note.
    hits = history.search("orçamento janeiro", scope="notas")
    assert [h.meeting_uid for h in hits] == ["reuniao-1"]


def test_a_cedilla_in_a_note_is_ignored_in_both_directions(
    store: TranscriptStore, tmp_path: Path
) -> None:
    make_meeting(store, tmp_path, uid="reuniao-c")
    store.create_note(
        "reuniao-c", kind="livre", content="ficou pendente a ação de cobrança",
        author=AGENT,
    )
    assert len(store.search("acao cobranca", scope="notas")) == 1
    assert len(store.search("ação cobrança", scope="notas")) == 1


def test_note_search_is_case_insensitive(history: TranscriptStore) -> None:
    assert history.search("ORÇAMENTO", scope="notas") == history.search(
        "orcamento", scope="notas"
    )


def test_punctuation_in_the_query_is_searched_not_parsed(
    history: TranscriptStore,
) -> None:
    assert history.search('orcamento - "', scope="notas") == history.search(
        "orcamento", scope="notas"
    )
    assert history.search("NEAR(", scope="ambos") == []


# -- filters and bookkeeping ---------------------------------------------


def test_the_meeting_filter_applies_to_both_scopes(history: TranscriptStore) -> None:
    hits = history.search("orcamento", scope="ambos", meeting_uid="reuniao-2")
    assert natures(hits) == [NATURE_NOTE]
    assert hits[0].meeting_uid == "reuniao-2"


def test_the_date_filter_applies_to_notes_through_their_meeting(
    history: TranscriptStore,
) -> None:
    hits = history.search(
        "orcamento",
        scope="notas",
        since=datetime(2026, 2, 1, tzinfo=UTC),
        until=datetime(2026, 2, 28, tzinfo=UTC),
    )
    assert [h.meeting_uid for h in hits] == ["reuniao-2"]


def test_the_limit_bounds_the_whole_mixed_result(history: TranscriptStore) -> None:
    assert len(history.search("orcamento", scope="ambos", limit=2)) == 2
    assert len(history.search("orcamento", scope="ambos", limit=1)) == 1


def test_an_edited_note_is_found_by_its_new_text_and_not_by_the_old(
    history: TranscriptStore,
) -> None:
    note = history.notes_of("reuniao-1")[0]
    history.update_note(note.uid, content="Agora fala de ornitorrinco.")

    assert history.search("orcamento", scope="notas", meeting_uid="reuniao-1") == []
    found = history.search("ornitorrinco", scope="notas")
    assert [h.note_uid for h in found] == [note.uid]
    assert history.unindexed_notes() == []
    assert history.orphan_note_index_rows() == []


def test_a_removed_note_stops_being_found(history: TranscriptStore) -> None:
    note = history.notes_of("reuniao-2")[0]
    history.delete_note(note.uid)
    assert history.search("orcamento", scope="notas", meeting_uid="reuniao-2") == []
    assert history.orphan_note_index_rows() == []


def test_the_search_index_never_confuses_a_note_with_a_segment(
    history: TranscriptStore,
) -> None:
    """Segment ids and note ids are numbered independently and do collide.

    They live in separate indexes for exactly that reason, and this is the
    assertion that would fail if they were ever merged into one.
    """
    note_ids = {r[0] for r in history._conn.execute("SELECT id FROM notes")}
    segment_ids = {r[0] for r in history._conn.execute("SELECT id FROM segments")}
    assert note_ids & segment_ids, "o teste precisa de identificadores que colidem"

    hits = history.search("orcamento", scope="ambos")
    for hit in hits:
        if hit.nature == NATURE_NOTE:
            assert history.get_note(hit.note_uid).content == hit.text
        else:
            assert hit.text in {e.text for e in history.timeline(hit.meeting_uid)}
