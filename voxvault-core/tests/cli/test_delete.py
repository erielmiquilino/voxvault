"""``voxvault delete``: the command line the desktop app deletes through.

The JSON printed here is a contract -- the app deserializes it into typed
structures -- so its exact shape is asserted, not just a few keys of it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from voxvault.layout import meeting_dir
from voxvault.store import NoteAuthor, TranscriptStore
from voxvault.types import AttemptState, EngineInfo, MeetingState, Segment, Track

ENGINE = EngineInfo(
    name="falso", model="modelo-de-teste", compute_type="int8",
    device="cpu", version="1.0",
)
STARTED = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)


def run(data_dir: Path, *args: str) -> tuple[int, str, str]:
    root = Path(__file__).resolve().parents[2] / "src"
    env = {
        **os.environ,
        "PYTHONPATH": str(root),
        "PYTHONIOENCODING": "utf-8",
        "VOXVAULT_DATA_DIR": str(data_dir),
    }
    result = subprocess.run(
        [sys.executable, "-m", "voxvault.cli", "--data-dir", str(data_dir), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    return result.returncode, result.stdout, result.stderr


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


def _meeting(store: TranscriptStore, data_dir: Path, uid: str, *, notes: int = 1):
    directory = meeting_dir(data_dir, uid)
    directory.mkdir(parents=True)
    (directory / "mic.flac").write_bytes(b"m" * 2048)
    store.create_meeting(
        uid=uid, title=f"Reuniao {uid}", started_at=STARTED, directory=directory,
    )
    store.finish_meeting(
        uid, ended_at=STARTED, duration_ms=90_000, state=MeetingState.RECORDED
    )
    revision = store.begin_revision(uid, engine=ENGINE)
    store.add_segments(revision.id, Track.MIC, [Segment(0, 1000, "fala")])
    store.publish_revision(revision.id, tracks_ok=[Track.MIC])
    for index in range(notes):
        store.create_note(
            uid, kind="livre", content=f"nota {index}", author=NoteAuthor.user()
        )
    return directory


def test_without_yes_the_preview_changes_nothing(store, data_dir) -> None:
    """Scenario: Linha de comando sem confirmacao."""
    directory = _meeting(store, data_dir, "abc123", notes=2)

    code, out, err = run(data_dir, "delete", "abc")

    assert code == 0, err
    assert "Reuniao abc123" in out
    assert "2 nota(s)" in out
    assert "--yes" in out
    assert "nao pode ser desfeita" in out
    assert (directory / "mic.flac").is_file()
    assert store.get_meeting("abc123") is not None


def test_with_yes_the_meeting_is_gone(store, data_dir) -> None:
    directory = _meeting(store, data_dir, "abc123")

    code, out, err = run(data_dir, "delete", "abc123", "--yes")

    assert code == 0, err
    assert "excluida" in out
    assert not directory.exists()
    assert store.get_meeting("abc123") is None


def test_an_ambiguous_prefix_refuses_only_that_item(store, data_dir) -> None:
    first = _meeting(store, data_dir, "aa11")
    second = _meeting(store, data_dir, "aa22")
    third = _meeting(store, data_dir, "bb33")

    code, out, err = run(data_dir, "delete", "aa", "bb", "--yes", "--json")

    assert code == 2, err
    payload = json.loads(out)
    assert [(i["uid"], i["resultado"]) for i in payload["itens"]] == [
        ("aa", "recusada"), ("bb33", "excluida"),
    ]
    assert "ambiguo" in payload["itens"][0]["motivo"]
    assert payload["itens"][0]["causa"] == "identificador"
    assert first.exists() and second.exists()
    assert not third.exists()


def test_a_batch_with_a_refusal_deletes_the_rest(store, data_dir) -> None:
    """Scenario: Varias reunioes com uma recusa."""
    kept = _meeting(store, data_dir, "r1")
    _meeting(store, data_dir, "r2")
    _meeting(store, data_dir, "r3")
    store.set_attempt_state("r1", AttemptState.RUNNING)

    code, out, err = run(data_dir, "delete", "r1", "r2", "r3", "--yes", "--json")

    assert code == 2, err
    itens = json.loads(out)["itens"]
    assert [(i["uid"], i["resultado"]) for i in itens] == [
        ("r1", "recusada"), ("r2", "excluida"), ("r3", "excluida"),
    ]
    assert "em execucao" in itens[0]["motivo"]
    assert itens[0]["causa"] == "transcrevendo"
    assert (kept / "mic.flac").is_file()
    assert store.get_meeting("r1") is not None


def test_the_json_contract_is_exact(store, data_dir) -> None:
    directory = _meeting(store, data_dir, "abc123", notes=3)
    size = sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())
    expected_item = {
        "uid": "abc123",
        "titulo": "Reuniao abc123",
        "inicio": STARTED.isoformat(),
        "duracao_ms": 90_000,
        "revisoes": 1,
        "notas": 3,
        "diretorio": str(directory),
        "arquivos": [{"caminho": "mic.flac", "bytes": 2048}],
        "bytes": size,
        "motivo": "",
        "causa": "",
    }
    expected_total = {
        "reunioes": 1, "duracao_ms": 90_000, "revisoes": 1, "notas": 3,
        "bytes": size,
    }

    code, out, err = run(data_dir, "delete", "abc123", "--json")
    assert code == 0, err
    assert json.loads(out) == {"itens": [expected_item], "total": expected_total}

    code, out, err = run(data_dir, "delete", "abc123", "--json", "--yes")
    assert code == 0, err
    assert json.loads(out) == {
        "itens": [{**expected_item, "resultado": "excluida"}],
        "total": expected_total,
    }


def test_a_preview_of_a_meeting_being_recorded_says_why(store, data_dir) -> None:
    _meeting(store, data_dir, "gravando1")
    store.finish_meeting(
        "gravando1", ended_at=STARTED, duration_ms=1, state=MeetingState.RECORDING
    )

    code, out, err = run(data_dir, "delete", "gravando1", "--json")

    assert code == 2, err
    item = json.loads(out)["itens"][0]
    assert "sendo gravada" in item["motivo"]
    assert item["causa"] == "gravando"
    assert json.loads(out)["total"]["reunioes"] == 0


def test_a_usage_error_exits_one(data_dir) -> None:
    code, _out, err = run(data_dir, "delete")
    assert code == 1
    assert "uid" in err
