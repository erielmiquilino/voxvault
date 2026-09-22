"""Tasks 1.5, 6.3 and 6.4: full-text search over the whole history.

Portuguese is the point. The same word shows up accented and unaccented in
transcribed speech, so a search that only works in one direction is a search
that fails half the time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import make_meeting, transcribe

from voxvault.store import Origin, TranscriptStore
from voxvault.types import EngineInfo, Segment, Track


@pytest.fixture
def history(store: TranscriptStore, tmp_path: Path, engine: EngineInfo):
    """Three meetings across three months, each mentioning the same subject."""
    moments = [
        datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
        datetime(2026, 2, 15, 9, 0, tzinfo=UTC),
        datetime(2026, 3, 15, 9, 0, tzinfo=UTC),
    ]
    texts = [
        ("a reunião de janeiro tratou do orçamento", "concordamos com a proposta"),
        ("na reuniao de fevereiro revimos o orcamento", "ficou pendente a ação"),
        ("a REUNIÃO de março fechou o Orçamento", "obrigado a todos"),
    ]
    for index, (moment, (mic_text, system_text)) in enumerate(
        zip(moments, texts, strict=False), start=1
    ):
        uid = f"reuniao-{index}"
        make_meeting(store, tmp_path, uid=uid, started_at=moment, title=f"Encontro {index}")
        transcribe(
            store,
            uid,
            engine,
            mic=[Segment(0, 2000, mic_text)],
            system=[Segment(2500, 4000, system_text)],
        )
    return store


def uids(hits) -> list[str]:
    return sorted({h.meeting_uid for h in hits})


def test_term_present_in_three_meetings_identifies_all_three(history) -> None:
    hits = history.search("reuniao")
    assert uids(hits) == ["reuniao-1", "reuniao-2", "reuniao-3"]


def test_each_hit_carries_instant_speaker_and_context(history) -> None:
    hits = history.search("orcamento")
    assert hits
    for hit in hits:
        assert hit.meeting_uid
        assert hit.meeting_title
        assert hit.start_ms >= 0
        assert hit.speaker in {"eu", "outros"}
        assert "[" in hit.excerpt and "]" in hit.excerpt


def test_unaccented_query_finds_accented_text(history) -> None:
    assert uids(history.search("reuniao")) == uids(history.search("reunião"))
    assert "reuniao-1" in uids(history.search("orcamento"))


def test_accented_query_finds_unaccented_text(history) -> None:
    # "reuniao de fevereiro" is stored without accents.
    hits = history.search("reunião fevereiro")
    assert uids(hits) == ["reuniao-2"]


def test_cedilla_is_ignored_in_both_directions(history) -> None:
    assert uids(history.search("acao")) == ["reuniao-2"]
    assert uids(history.search("ação")) == ["reuniao-2"]


def test_search_is_case_insensitive(history) -> None:
    assert uids(history.search("REUNIÃO")) == uids(history.search("reuniao"))
    assert uids(history.search("Orçamento")) == uids(history.search("orcamento"))


def test_search_can_be_restricted_to_a_date_range(history) -> None:
    hits = history.search(
        "reuniao",
        since=datetime(2026, 2, 1, tzinfo=UTC),
        until=datetime(2026, 2, 28, tzinfo=UTC),
    )
    assert uids(hits) == ["reuniao-2"]


def test_search_can_be_restricted_to_one_meeting(history) -> None:
    assert uids(history.search("reuniao", meeting_uid="reuniao-3")) == ["reuniao-3"]


def test_several_words_are_combined_with_and(history) -> None:
    assert uids(history.search("reuniao marco")) == ["reuniao-3"]
    assert history.search("reuniao inexistente") == []


def test_punctuation_in_the_query_is_searched_not_parsed(history) -> None:
    """A stray operator character must not become FTS5 syntax."""
    assert history.search('reuniao - "') == history.search("reuniao")
    assert history.search("NEAR(") == []


def test_segments_of_an_unpublished_revision_are_not_searchable(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path, uid="em-curso")
    revision = store.begin_revision("em-curso", engine=engine)
    store.add_segments(
        revision.id, Track.MIC, [Segment(0, 1000, "palavra invisivel ainda")]
    )
    assert store.search("invisivel") == []


def test_superseded_text_stops_being_found_after_reprocessing(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo, other_engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path, uid="reprocessada")
    transcribe(
        store,
        "reprocessada",
        engine,
        mic=[Segment(0, 1000, "transcricao antiga zebra")],
        system=[Segment(0, 1000, "eco antigo")],
    )
    assert uids(store.search("zebra")) == ["reprocessada"]

    transcribe(
        store,
        "reprocessada",
        other_engine,
        mic=[Segment(0, 1000, "transcricao nova girafa")],
        system=[Segment(0, 1000, "eco novo")],
    )
    assert store.search("zebra") == []
    assert uids(store.search("girafa")) == ["reprocessada"]
    assert store.orphan_index_rows() == []
    assert store.unindexed_active_segments() == []


def test_imported_segments_are_attributed_to_an_unknown_speaker(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    from voxvault.store import SPEAKER_UNKNOWN, TRACK_IMPORTED

    make_meeting(store, tmp_path, uid="importada", origin=Origin.IMPORTED)
    revision = store.begin_revision("importada", engine=engine)
    store.add_segments(
        revision.id, TRACK_IMPORTED, [Segment(0, 1000, "audio importado de video")]
    )
    store.publish_revision(revision.id, tracks_ok=[TRACK_IMPORTED])

    hits = store.search("importado")
    assert [h.speaker for h in hits] == [SPEAKER_UNKNOWN]
    assert all(e.speaker == SPEAKER_UNKNOWN for e in store.timeline("importada"))
