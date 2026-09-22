"""Tasks 1.6 to 1.6.5: the revision model.

The rule underneath all of it: reprocessing may improve a transcript or leave
it alone, but it may never make it worse. Every refusal below is that rule.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from conftest import make_meeting, transcribe

from voxvault.errors import StorageError
from voxvault.store import Origin, RevisionStatus, TranscriptState, TranscriptStore
from voxvault.store.exporting import export_status
from voxvault.types import EngineInfo, RevisionState, Segment, Track


# -- identity and freezing ----------------------------------------------


def test_a_revision_records_the_engine_configuration_and_vocabulary(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    revision, outcome = transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 1000, "primeira")],
        system=[Segment(0, 1000, "segunda")],
        vocabulary="VoxVault, WASAPI",
    )
    active = store.active_revision("reuniao-1")
    assert active is not None
    assert active.uid == revision.uid
    assert active.engine_id == engine.identifier()
    assert active.vocabulary == "VoxVault, WASAPI"
    assert active.state is RevisionState.COMPLETE
    assert outcome.published is True


def test_engine_and_vocabulary_stay_frozen_across_the_whole_attempt(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo, other_engine: EngineInfo
) -> None:
    """Task 1.6.4: the configuration changes mid-attempt and is ignored.

    Both tracks ask the store which engine this attempt belongs to. A settings
    change between them must not split one revision across two engines.
    """
    make_meeting(store, tmp_path)
    revision = store.begin_revision(
        "reuniao-1", engine=engine, vocabulary="glossario-a", language="pt"
    )
    frozen_for_mic = store.revision_engine(revision.id)
    store.add_segments(revision.id, Track.MIC, [Segment(0, 1000, "trilha do micro")])

    # The user changes the engine configuration right here.
    changed = other_engine

    frozen_for_system = store.revision_engine(revision.id)
    store.add_segments(revision.id, Track.SYSTEM, [Segment(0, 1000, "trilha do sistema")])
    store.publish_revision(revision.id, tracks_ok=[Track.MIC, Track.SYSTEM])

    assert frozen_for_mic == frozen_for_system == engine
    assert frozen_for_system != changed
    active = store.active_revision("reuniao-1")
    assert active.engine_id == engine.identifier()
    assert active.vocabulary == "glossario-a"

    # The new configuration applies only to attempts started afterwards.
    later = store.begin_revision("reuniao-1", engine=changed, vocabulary="glossario-b")
    assert store.revision_engine(later.id) == changed


def test_the_identity_of_a_revision_cannot_be_rewritten(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    revision = store.begin_revision("reuniao-1", engine=engine, vocabulary="fixo")
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "UPDATE revisions SET engine_id = 'outro' WHERE id = ?", (revision.id,)
        )
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "UPDATE revisions SET vocabulary = 'trocado' WHERE id = ?", (revision.id,)
        )


def test_an_interrupted_attempt_is_discarded_whole(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo, other_engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    abandoned = store.begin_revision("reuniao-1", engine=engine)
    store.add_segments(abandoned.id, Track.MIC, [Segment(0, 1000, "metade de um texto")])
    store.discard_revision(abandoned.id, "processo interrompido")

    assert store.revision(abandoned.id).status is RevisionStatus.DISCARDED
    assert (
        store._conn.execute(
            "SELECT count(*) FROM segments WHERE revision_id = ?", (abandoned.id,)
        ).fetchone()[0]
        == 0
    )
    with pytest.raises(StorageError):
        store.add_segments(abandoned.id, Track.SYSTEM, [Segment(0, 1000, "tarde demais")])

    # Resuming is a new attempt, which captures whatever is configured now.
    resumed = store.begin_revision("reuniao-1", engine=other_engine)
    assert store.revision_engine(resumed.id) == other_engine


# -- atomic publication --------------------------------------------------


def test_a_revision_under_construction_is_invisible_everywhere(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo, other_engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 1000, "texto publicado")],
        system=[Segment(0, 1000, "eco publicado")],
    )
    published = {e.text for e in store.timeline("reuniao-1")}

    building = store.begin_revision("reuniao-1", engine=other_engine)
    store.add_segments(building.id, Track.MIC, [Segment(0, 1000, "texto em construcao")])

    assert {e.text for e in store.timeline("reuniao-1")} == published
    assert store.search("construcao") == []
    status = export_status(store, "reuniao-1")
    assert status.active_revision_uid != building.uid


def test_a_query_during_a_first_transcription_reports_the_processing_state(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    store.set_attempt_state("reuniao-1", "em_execucao")
    building = store.begin_revision("reuniao-1", engine=engine)
    store.add_segments(building.id, Track.MIC, [Segment(0, 1000, "parcialmente pronto")])

    meeting = store.get_meeting("reuniao-1")
    assert store.timeline("reuniao-1") == []
    assert meeting.transcript_state is TranscriptState.NONE
    assert meeting.attempt_state == "em_execucao"


def test_all_tracks_become_valid_at_once(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    revision = store.begin_revision("reuniao-1", engine=engine)
    store.add_segments(revision.id, Track.MIC, [Segment(0, 1000, "micro um")])
    assert store.timeline("reuniao-1") == []
    store.add_segments(revision.id, Track.SYSTEM, [Segment(500, 1500, "sistema um")])
    assert store.timeline("reuniao-1") == []

    store.publish_revision(revision.id, tracks_ok=[Track.MIC, Track.SYSTEM])
    tracks = {str(e.track) for e in store.timeline("reuniao-1")}
    assert tracks == {"mic", "system"}


def test_the_timeline_never_mixes_two_revisions(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo, other_engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    first, _ = transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 1000, "antigo micro")],
        system=[Segment(0, 1000, "antigo sistema")],
    )
    second, _ = transcribe(
        store,
        "reuniao-1",
        other_engine,
        mic=[Segment(0, 1000, "novo micro")],
        system=[Segment(0, 1000, "novo sistema")],
    )
    entries = store.timeline("reuniao-1")
    assert {e.text for e in entries} == {"novo micro", "novo sistema"}

    revision_ids = {
        row[0]
        for row in store._conn.execute(
            "SELECT DISTINCT revision_id FROM segments WHERE id IN "
            "(%s)" % ",".join(str(e.segment_id) for e in entries)
        )
    }
    assert revision_ids == {second.id}
    assert store.revision(first.id).status is RevisionStatus.SUPERSEDED


def test_reprocessing_replaces_the_previous_revision_entirely(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo, other_engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 1000, "texto antigo pterodatilo")],
        system=[Segment(0, 1000, "eco antigo")],
    )
    _, outcome = transcribe(
        store,
        "reuniao-1",
        other_engine,
        mic=[Segment(0, 1000, "texto novo ornitorrinco")],
        system=[Segment(0, 1000, "eco novo")],
    )
    assert outcome.published is True
    assert outcome.replaced_revision_uid is not None
    assert all("antigo" not in e.text for e in store.timeline("reuniao-1"))
    assert store.search("pterodatilo") == []
    assert [h.meeting_uid for h in store.search("ornitorrinco")] == ["reuniao-1"]


# -- partial revision policy ---------------------------------------------


def test_a_first_partial_transcription_becomes_active(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    _, outcome = transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 1000, "so o microfone saiu")],
        tracks_failed=[Track.SYSTEM],
        failure_reason="arquivo da trilha do sistema corrompido",
    )
    assert outcome.published is True
    assert outcome.state is RevisionState.PARTIAL
    assert store.get_meeting("reuniao-1").transcript_state is TranscriptState.PARTIAL
    assert [e.text for e in store.timeline("reuniao-1")] == ["so o microfone saiu"]
    assert "system" in outcome.message


def test_a_partial_reprocess_does_not_evict_a_complete_revision(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo, other_engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    good, _ = transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 1000, "micro completo")],
        system=[Segment(0, 1000, "sistema completo")],
    )
    worse, outcome = transcribe(
        store,
        "reuniao-1",
        other_engine,
        mic=[Segment(0, 1000, "micro sozinho")],
        tracks_failed=[Track.SYSTEM],
        failure_reason="motor caiu na trilha do sistema",
    )

    assert outcome.published is False
    assert outcome.reason == "revisao_anterior_mais_completa"
    assert outcome.kept_revision_uid == good.uid
    assert "nao substituiu" in outcome.message
    assert "system" in outcome.message

    active = store.active_revision("reuniao-1")
    assert active.uid == good.uid
    assert {e.text for e in store.timeline("reuniao-1")} == {
        "micro completo",
        "sistema completo",
    }
    meeting = store.get_meeting("reuniao-1")
    assert meeting.transcript_state is TranscriptState.COMPLETE
    assert meeting.attempt_state == "falhou"
    assert meeting.attempt_error == outcome.message
    assert store.revision(worse.id).status is RevisionStatus.DISCARDED


def test_failure_on_every_track_publishes_nothing(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo, other_engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    good, _ = transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 1000, "micro completo")],
        system=[Segment(0, 1000, "sistema completo")],
    )
    failed = store.begin_revision("reuniao-1", engine=other_engine)
    outcome = store.publish_revision(
        failed.id,
        tracks_ok=[],
        tracks_failed=[Track.MIC, Track.SYSTEM],
        failure_reason="modelo nao pode ser carregado",
    )

    assert outcome.published is False
    assert outcome.reason == "todas_as_trilhas_falharam"
    assert store.active_revision("reuniao-1").uid == good.uid
    assert {e.text for e in store.timeline("reuniao-1")} == {
        "micro completo",
        "sistema completo",
    }
    assert store.get_meeting("reuniao-1").transcript_state is TranscriptState.COMPLETE


def test_a_first_attempt_failing_on_every_track_leaves_no_revision(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    attempt = store.begin_revision("reuniao-1", engine=engine)
    outcome = store.publish_revision(
        attempt.id, tracks_ok=[], tracks_failed=[Track.MIC, Track.SYSTEM]
    )
    assert outcome.published is False
    assert store.active_revision("reuniao-1") is None
    assert store.get_meeting("reuniao-1").transcript_state is TranscriptState.NONE


def test_a_partial_revision_that_recovers_the_missing_track_is_published(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo, other_engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 1000, "so o micro")],
        tracks_failed=[Track.SYSTEM],
    )
    _, outcome = transcribe(
        store,
        "reuniao-1",
        other_engine,
        mic=[Segment(0, 1000, "micro de novo")],
        system=[Segment(0, 1000, "sistema recuperado")],
    )
    assert outcome.published is True
    assert outcome.state is RevisionState.COMPLETE
    assert store.get_meeting("reuniao-1").transcript_state is TranscriptState.COMPLETE


def test_a_published_revision_cannot_be_published_again(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    revision, _ = transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 1000, "a")],
        system=[Segment(0, 1000, "b")],
    )
    with pytest.raises(StorageError):
        store.publish_revision(revision.id, tracks_ok=[Track.MIC, Track.SYSTEM])


# -- total deterministic ordering ---------------------------------------


def test_two_successive_queries_return_byte_identical_order(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    """Task 1.6.3, including segments starting in the same millisecond.

    Cursor pagination is built on this order later; an unstable one would
    silently drop or repeat segments across pages.
    """
    make_meeting(store, tmp_path)
    same_instant_mic = [Segment(1000, 1500, f"micro {i}") for i in range(20)]
    same_instant_system = [Segment(1000, 1500, f"sistema {i}") for i in range(20)]
    transcribe(
        store, "reuniao-1", engine, mic=same_instant_mic, system=same_instant_system
    )

    first = store.timeline("reuniao-1")
    second = store.timeline("reuniao-1")
    assert first == second
    assert [e.segment_id for e in first] == [e.segment_id for e in second]


def test_ties_are_broken_by_track_then_by_identifier(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(500, 900, "micro a"), Segment(500, 900, "micro b")],
        system=[Segment(500, 900, "sistema a")],
    )
    entries = store.timeline("reuniao-1")
    assert [str(e.track) for e in entries] == ["mic", "mic", "system"]
    assert [e.text for e in entries] == ["micro a", "micro b", "sistema a"]
    keys = [(e.start_ms, str(e.track), e.segment_id) for e in entries]
    assert keys == sorted(keys)


def test_cursor_pagination_reproduces_the_full_order(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    make_meeting(store, tmp_path)
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(i * 10, i * 10 + 5, f"micro {i}") for i in range(50)],
        system=[Segment(i * 10, i * 10 + 5, f"sistema {i}") for i in range(50)],
    )
    expected = store.timeline("reuniao-1")

    paged = []
    cursor = None
    while True:
        page = list(store.iter_timeline("reuniao-1", after=cursor, limit=7))
        if not page:
            break
        paged.extend(page)
        cursor = TranscriptStore.cursor_of(page[-1])

    assert paged == expected
    assert len(paged) == 100


# -- the index moves with the revision ----------------------------------


def test_the_search_index_only_ever_points_at_active_revisions(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo, other_engine: EngineInfo
) -> None:
    for index in range(3):
        uid = f"reuniao-{index}"
        make_meeting(store, tmp_path, uid=uid)
        transcribe(
            store,
            uid,
            engine,
            mic=[Segment(0, 1000, f"primeira versao {index}")],
            system=[Segment(0, 1000, f"eco {index}")],
        )
    transcribe(
        store,
        "reuniao-1",
        other_engine,
        mic=[Segment(0, 1000, "segunda versao 1")],
        system=[Segment(0, 1000, "eco novo 1")],
    )
    assert store.orphan_index_rows() == []
    assert store.unindexed_active_segments() == []


@pytest.mark.parametrize(
    "label", ["after_index_delete", "after_index_insert", "after_activation"]
)
def test_a_failure_while_publishing_rolls_the_index_back_with_it(
    store: TranscriptStore,
    tmp_path: Path,
    engine: EngineInfo,
    other_engine: EngineInfo,
    label: str,
) -> None:
    """Task 1.6.5: index and activation are the same transaction.

    The fault is injected at every point the publication exposes, including
    the one right after the meeting has been pointed at the new revision. A
    fault there is what separates the two designs: if the index were rewritten
    in a transaction of its own, the activation would already be committed and
    the search would be left describing a revision that is not the active one.
    """
    make_meeting(store, tmp_path)
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 1000, "texto estavel dinossauro")],
        system=[Segment(0, 1000, "eco estavel")],
    )
    before = [h.segment_id for h in store.search("dinossauro")]
    assert before

    doomed = store.begin_revision("reuniao-1", engine=other_engine)
    store.add_segments(doomed.id, Track.MIC, [Segment(0, 1000, "texto novo mamute")])
    store.add_segments(doomed.id, Track.SYSTEM, [Segment(0, 1000, "eco novo")])

    def explode(reached: str) -> None:
        if reached == label:
            raise RuntimeError("queda simulada no meio da publicacao")

    store.fault_hook = explode
    with pytest.raises(RuntimeError):
        store.publish_revision(doomed.id, tracks_ok=[Track.MIC, Track.SYSTEM])
    store.fault_hook = None

    assert store.orphan_index_rows() == [], "o indice aponta para revisao nao ativa"
    assert store.unindexed_active_segments() == [], "a revisao ativa saiu do indice"
    assert [h.segment_id for h in store.search("dinossauro")] == before
    assert store.search("mamute") == []
    assert store.revision(doomed.id).status is RevisionStatus.BUILDING
    assert store.active_revision("reuniao-1").engine_id == engine.identifier()


def test_publication_is_refused_if_the_active_revision_moved_underneath_it(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo, other_engine: EngineInfo
) -> None:
    """Defence in depth for two processes publishing the same meeting.

    The completeness comparison is made before the write lock is taken. The
    activation is therefore guarded on the revision it was decided against.
    The hook here stands in for the other process, changing the active
    revision from inside the transaction so the guard has something to catch.
    """
    make_meeting(store, tmp_path)
    first, _ = transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(0, 1000, "micro original")],
        system=[Segment(0, 1000, "sistema original")],
    )
    second = store.begin_revision("reuniao-1", engine=other_engine)
    store.add_segments(second.id, Track.MIC, [Segment(0, 1000, "micro novo")])
    store.add_segments(second.id, Track.SYSTEM, [Segment(0, 1000, "sistema novo")])

    def move_the_target(label: str) -> None:
        if label == "after_index_delete":
            store._conn.execute(
                "UPDATE meetings SET active_revision_id = NULL WHERE uid = 'reuniao-1'"
            )

    store.fault_hook = move_the_target
    with pytest.raises(StorageError, match="mudou durante a"):
        store.publish_revision(second.id, tracks_ok=[Track.MIC, Track.SYSTEM])
    store.fault_hook = None

    assert store.active_revision("reuniao-1").uid == first.uid
    assert store.orphan_index_rows() == []
    assert store.unindexed_active_segments() == []


def test_segments_are_refused_by_a_revision_that_is_no_longer_building(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    """A published transcript is immutable.

    The same check runs a second time inside the write lock, which is what
    covers another process publishing between this caller's check and its
    write. That window is also closed upstream, by the pipeline refusing a
    second attempt for a meeting that already has one.
    """
    make_meeting(store, tmp_path)
    revision = store.begin_revision("reuniao-1", engine=engine)
    store.add_segments(revision.id, Track.MIC, [Segment(0, 1000, "micro")])
    store.publish_revision(
        revision.id, tracks_ok=[Track.MIC], tracks_failed=[Track.SYSTEM]
    )
    with pytest.raises(StorageError, match="nao aceita mais segmentos"):
        store.add_segments(revision.id, Track.SYSTEM, [Segment(0, 1000, "tarde demais")])
    assert len(store.timeline("reuniao-1")) == 1


def test_completeness_of_an_imported_meeting_needs_only_its_own_track(
    store: TranscriptStore, tmp_path: Path, engine: EngineInfo
) -> None:
    from voxvault.store import TRACK_IMPORTED

    make_meeting(store, tmp_path, uid="importada", origin=Origin.IMPORTED)
    revision = store.begin_revision("importada", engine=engine)
    store.add_segments(revision.id, TRACK_IMPORTED, [Segment(0, 1000, "conteudo")])
    outcome = store.publish_revision(revision.id, tracks_ok=[TRACK_IMPORTED])
    assert outcome.state is RevisionState.COMPLETE
