"""The machine-readable surface the desktop app reads.

These exist because the alternative is parsing human text, and human text is
written to be changed. The one property worth stating plainly: availability of
a transcript and the state of the current attempt are separate fields, so a
*failed* transcription is distinguishable from a *queued* one. A single status
string cannot say both, and the interface has to.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from voxvault.store import Origin, TranscriptStore
from voxvault.types import AttemptState, EngineInfo, MeetingState, Segment, Track

ENGINE = EngineInfo(
    name="falso", model="modelo-de-teste", compute_type="int8",
    device="cpu", version="1.0",
)


def run(data_dir: Path, *args: str) -> tuple[int, str, str]:
    """Invoke the real command line, as the app does."""
    result = subprocess.run(
        [sys.executable, "-m", "voxvault.cli", "--data-dir", str(data_dir), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**_env(), "VOXVAULT_DATA_DIR": str(data_dir)},
    )
    return result.returncode, result.stdout, result.stderr


def _env() -> dict:
    import os

    root = Path(__file__).resolve().parents[2] / "src"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def store(data_dir: Path):
    handle = TranscriptStore(data_dir / "voxvault.db")
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture
def meeting(store, data_dir: Path):
    directory = data_dir / "recordings" / "r1"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "mic.wav").write_bytes(b"RIFF....WAVEfmt ")
    (directory / "system.wav").write_bytes(b"RIFF....WAVEfmt ")
    created = store.create_meeting(
        uid="r1", title="Alinhamento", directory=directory,
        started_at=datetime(2026, 3, 2, 14, 0, tzinfo=timezone.utc),
        origin=Origin.RECORDED, state=MeetingState.RECORDED, duration_ms=60_000,
    )
    revision = store.begin_revision("r1", engine=ENGINE, language="pt")
    store.add_segments(revision.id, Track.MIC.value, [Segment(0, 2000, "bom dia")])
    store.add_segments(
        revision.id, Track.SYSTEM.value, [Segment(2500, 5000, "bom dia a todos")]
    )
    store.publish_revision(
        revision.id, tracks_ok=[Track.MIC.value, Track.SYSTEM.value]
    )
    return created


# -- list --------------------------------------------------------------

def test_list_json_separates_availability_from_attempt(data_dir, meeting, store):
    """The distinction the interface cannot render without this."""
    store.set_attempt_state("r1", AttemptState.RUNNING)
    store.close()

    code, out, _err = run(data_dir, "list", "--json")
    assert code == 0
    item = json.loads(out)["reunioes"][0]

    assert item["transcricao_disponivel"] is True
    assert item["completude"] == "completa"
    assert item["estado_da_tentativa"] == "em_execucao"


def test_a_failed_attempt_is_distinguishable_from_a_queued_one(
    data_dir, meeting, store
):
    store.set_attempt_state("r1", AttemptState.FAILED, "a GPU ficou sem memoria")
    store.close()

    item = json.loads(run(data_dir, "list", "--json")[1])["reunioes"][0]
    assert item["estado_da_tentativa"] == "falhou"
    assert "memoria" in item["motivo_da_falha"]


def test_list_json_says_whether_the_audio_is_still_there(data_dir, meeting, store):
    """Reprocessing needs audio, so a surface offering it has to know."""
    store.close()
    assert json.loads(run(data_dir, "list", "--json")[1])["reunioes"][0]["tem_audio"]

    for leftover in (data_dir / "recordings" / "r1").glob("*.wav"):
        leftover.unlink()
    item = json.loads(run(data_dir, "list", "--json")[1])["reunioes"][0]
    assert item["tem_audio"] is False


def test_an_empty_store_lists_nothing_without_failing(data_dir):
    code, out, _err = run(data_dir, "list", "--json")
    assert code == 0
    assert json.loads(out)["reunioes"] == []


# -- search ------------------------------------------------------------

def test_search_json_reaches_notes_and_transcripts(data_dir, meeting, store):
    from voxvault.store.notes import NoteAuthor

    store.create_note(
        "r1", kind="resumo", content="bom dia foi dito na abertura",
        author=NoteAuthor.user("eu"),
    )
    store.close()

    payload = json.loads(run(data_dir, "search", "bom dia", "--json")[1])
    naturezas = {hit["natureza"] for hit in payload["resultados"]}
    assert naturezas == {"transcricao", "nota"}


def test_a_note_result_carries_no_invented_instant(data_dir, meeting, store):
    from voxvault.store.notes import NoteAuthor

    store.create_note(
        "r1", kind="resumo", content="uma leitura posterior",
        author=NoteAuthor.agent("cliente"),
    )
    store.close()

    payload = json.loads(
        run(data_dir, "search", "leitura", "--json", "--scope", "notas")[1]
    )
    hit = payload["resultados"][0]
    assert "inicio_ms" not in hit
    assert hit["nota"]


def test_search_scope_is_honoured(data_dir, meeting, store):
    store.close()
    payload = json.loads(
        run(data_dir, "search", "bom dia", "--json", "--scope", "notas")[1]
    )
    assert payload["resultados"] == []


# -- doctor, devices, config -------------------------------------------

def test_doctor_json_reports_every_item_with_its_state(data_dir):
    _code, out, _err = run(data_dir, "doctor", "--json")
    payload = json.loads(out)

    chaves = {item["chave"] for item in payload["itens"]}
    assert {"python", "bibliotecas", "data_dir", "ffmpeg"} <= chaves
    assert all(item["estado"] in {"ok", "aviso", "falha"} for item in payload["itens"])
    assert isinstance(payload["falhou"], bool)


def test_doctor_json_reports_where_each_setting_came_from(data_dir):
    payload = json.loads(run(data_dir, "doctor", "--json")[1])
    origem = payload["configuracao"]["data_dir"]["origem"]
    assert origem, "a origem de cada valor tem de ser diagnosticavel"


def test_config_json_carries_value_and_origin(data_dir):
    payload = json.loads(run(data_dir, "config", "--json")[1])
    assert payload["arquivo"]
    assert payload["valores"]["model"]["valor"]
    assert payload["valores"]["model"]["origem"]


# -- rename and remove-audio -------------------------------------------

def test_rename_changes_the_title_and_the_export(data_dir, meeting, store):
    store.close()
    code, _out, _err = run(data_dir, "rename", "r1", "--title", "Retrospectiva")
    assert code == 0

    item = json.loads(run(data_dir, "list", "--json")[1])["reunioes"][0]
    assert item["titulo"] == "Retrospectiva"
    readable = data_dir / "recordings" / "r1" / "transcricao.md"
    assert "Retrospectiva" in readable.read_text(encoding="utf-8")


def test_remove_audio_asks_before_doing_it(data_dir, meeting, store):
    store.close()
    code, out, _err = run(data_dir, "remove-audio", "r1")

    assert code == 0
    assert "Confirme com --yes" in out
    assert (data_dir / "recordings" / "r1" / "mic.wav").is_file(), (
        "nada pode ser removido sem confirmacao"
    )


def test_remove_audio_keeps_the_transcript(data_dir, meeting, store):
    store.close()
    code, _out, _err = run(data_dir, "remove-audio", "r1", "--yes")
    assert code == 0

    assert not list((data_dir / "recordings" / "r1").glob("*.wav"))
    item = json.loads(run(data_dir, "list", "--json")[1])["reunioes"][0]
    assert item["transcricao_disponivel"] is True
    assert item["tem_audio"] is False


def test_remove_audio_is_refused_without_a_transcript(data_dir, store):
    """Removing the audio of a meeting nobody transcribed erases it entirely."""
    directory = data_dir / "recordings" / "sem-texto"
    directory.mkdir(parents=True)
    (directory / "mic.wav").write_bytes(b"RIFF....WAVEfmt ")
    store.create_meeting(
        uid="sem-texto", title="Nunca transcrita", directory=directory,
        started_at=datetime(2026, 3, 2, 14, 0, tzinfo=timezone.utc),
        origin=Origin.RECORDED, state=MeetingState.RECORDED,
    )
    store.close()

    code, _out, err = run(data_dir, "remove-audio", "sem-texto", "--yes")

    assert code == 1
    assert "apagaria a reuniao inteira" in err
    assert (directory / "mic.wav").is_file()
