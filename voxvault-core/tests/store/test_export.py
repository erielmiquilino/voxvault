"""Tasks 1.6.6 and 6.5-6.7: exports as derived, self-describing artifacts.

An export file cannot be committed together with the revision that produced
it. So it carries the revision's identifier, and anything that disagrees with
the active revision is stale by definition -- detectable, regenerable, and
never shown as current in the meantime.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import make_meeting, result_of, spawn, transcribe

from voxvault.errors import StorageError
from voxvault.store import (
    READABLE_NAME,
    STRUCTURED_NAME,
    TranscriptStore,
    current_export_paths,
    export_paths,
    export_status,
    load_structured,
    reconcile_exports,
    recorded_revision_uid,
    regenerate_exports,
    timeline_from_structured,
)
from voxvault.types import EngineInfo, Segment, Track


@pytest.fixture
def exported(store: TranscriptStore, tmp_path: Path, engine: EngineInfo):
    make_meeting(store, tmp_path, title="Revisao trimestral", duration_ms=3_723_456)
    transcribe(
        store,
        "reuniao-1",
        engine,
        mic=[Segment(1000, 4000, "vamos revisar o orçamento")],
        system=[Segment(3000, 6000, "posso comentar uma coisa"), Segment(9000, 9500, "obrigado")],
    )
    regenerate_exports(store, "reuniao-1")
    return store


def test_both_exports_land_in_the_meeting_directory(exported, tmp_path: Path) -> None:
    directory = tmp_path / "reuniao-1"
    assert (directory / READABLE_NAME).exists()
    assert (directory / STRUCTURED_NAME).exists()


def test_the_readable_export_carries_title_date_duration_and_the_timeline(
    exported, tmp_path: Path
) -> None:
    text = (tmp_path / "reuniao-1" / READABLE_NAME).read_text(encoding="utf-8")
    assert "# Revisao trimestral" in text
    assert "2026-03-02" in text
    assert "01:02:03" in text
    assert "[00:00:01.000] eu: vamos revisar o orçamento" in text
    assert "[00:00:03.000] outros: posso comentar uma coisa" in text
    assert "[fala sobreposta]" in text


def test_the_structured_export_round_trips_the_timeline_without_loss(
    exported, tmp_path: Path
) -> None:
    payload = load_structured(tmp_path / "reuniao-1" / STRUCTURED_NAME)
    rebuilt = timeline_from_structured(payload)
    assert rebuilt == exported.timeline("reuniao-1")

    for item, entry in zip(payload["segmentos"], rebuilt):
        assert item["trilha"] in {"mic", "system"}
        assert item["inicio_ms"] == entry.start_ms
        assert item["fim_ms"] == entry.end_ms
    assert payload["reuniao"]["duracao_ms"] == 3_723_456
    assert payload["reuniao"]["uid"] == "reuniao-1"
    assert payload["revisao"]["motor"]["modelo"] == "large-v3"


def test_every_export_records_the_revision_that_produced_it(
    exported, tmp_path: Path
) -> None:
    active = exported.active_revision("reuniao-1")
    structured = tmp_path / "reuniao-1" / STRUCTURED_NAME
    readable = tmp_path / "reuniao-1" / READABLE_NAME
    assert recorded_revision_uid(structured) == active.uid
    assert f"voxvault_revisao: {active.uid}" in readable.read_text(encoding="utf-8")


def test_exports_are_regenerated_after_a_new_transcription(
    exported, tmp_path: Path, other_engine: EngineInfo
) -> None:
    transcribe(
        exported,
        "reuniao-1",
        other_engine,
        mic=[Segment(0, 1000, "texto do segundo motor")],
        system=[Segment(2000, 3000, "resposta do segundo motor")],
    )
    assert export_status(exported, "reuniao-1").current is False

    regenerate_exports(exported, "reuniao-1")
    status = export_status(exported, "reuniao-1", verify_contents=True)
    assert status.current is True

    text = (tmp_path / "reuniao-1" / READABLE_NAME).read_text(encoding="utf-8")
    assert "texto do segundo motor" in text
    assert "vamos revisar" not in text
    assert other_engine.identifier() in text


def test_a_deleted_export_is_detected_and_rebuilt(exported, tmp_path: Path) -> None:
    (tmp_path / "reuniao-1" / READABLE_NAME).unlink()
    assert export_status(exported, "reuniao-1").current is False
    reconcile_exports(exported)
    assert (tmp_path / "reuniao-1" / READABLE_NAME).exists()


def test_a_meeting_without_an_active_revision_cannot_be_exported(
    store: TranscriptStore, tmp_path: Path
) -> None:
    make_meeting(store, tmp_path, uid="sem-revisao")
    with pytest.raises(StorageError):
        regenerate_exports(store, "sem-revisao")
    assert current_export_paths(store, "sem-revisao") is None


def test_process_killed_between_publishing_and_regenerating(
    db_path: Path, tmp_path: Path
) -> None:
    """The crash window, with a process that really dies inside it.

    The child publishes a second revision and calls ``os._exit`` before the
    exports are rewritten. Everything after that is what the next startup has
    to cope with.
    """
    directory = tmp_path / "reuniao-queda"
    directory.mkdir()
    with TranscriptStore(db_path) as store:
        store.create_meeting(
            uid="reuniao-queda",
            title="Reuniao interrompida",
            started_at=datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc),
            directory=directory,
        )
    first = result_of(spawn("seed_and_export", str(db_path), "reuniao-queda"))
    old_revision = first["revision_uid"]
    assert recorded_revision_uid(directory / STRUCTURED_NAME) == old_revision

    child = spawn("publish_then_die", str(db_path), "reuniao-queda")
    published = result_of(child)
    assert child.returncode == 9, "o processo filho deveria ter morrido abruptamente"
    new_revision = published["revision_uid"]
    assert new_revision != old_revision

    with TranscriptStore(db_path) as store:
        # The files on disk still describe the revision that is no longer active.
        assert recorded_revision_uid(directory / STRUCTURED_NAME) == old_revision
        assert store.active_revision("reuniao-queda").uid == new_revision

        status = export_status(store, "reuniao-queda", verify_contents=True)
        assert status.current is False
        assert status.reason == "revisao_divergente"

        # No surface hands a divergent export over as though it were current.
        assert current_export_paths(store, "reuniao-queda", regenerate=False) is None

        # Startup finds it and rebuilds it.
        assert reconcile_exports(store) == ["reuniao-queda"]
        assert recorded_revision_uid(directory / STRUCTURED_NAME) == new_revision
        assert export_status(store, "reuniao-queda", verify_contents=True).current

        text = (directory / READABLE_NAME).read_text(encoding="utf-8")
        assert "texto novo do micro" in text
        assert "texto original" not in text

        # A second startup has nothing left to do.
        assert reconcile_exports(store) == []


def test_export_paths_live_beside_the_meeting_audio(
    exported, tmp_path: Path
) -> None:
    meeting = exported.get_meeting("reuniao-1")
    readable, structured = export_paths(meeting)
    assert readable.parent == Path(meeting.directory)
    assert structured.parent == Path(meeting.directory)
