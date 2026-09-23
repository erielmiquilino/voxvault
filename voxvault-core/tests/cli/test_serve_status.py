"""`voxvault serve --status --json`: what the uninstaller asks first.

Covers the uninstall requirement of specs/app-distribution: a recording in
progress refuses the uninstall, and everything else lets it continue. The
command runs for real, in its own process, against a rendezvous in a
temporary profile -- with no service at all, with one that answers, and with
one whose process is gone.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from voxvault.service import _make_handler
from voxvault.service.rendezvous import Rendezvous, publish

SECRET = "segredo-de-teste"


def _status(profile: Path, data_dir: Path) -> tuple[int, dict]:
    root = Path(__file__).resolve().parents[2] / "src"
    env = {
        **os.environ,
        "USERPROFILE": str(profile),
        "VOXVAULT_DATA_DIR": str(data_dir),
        "PYTHONPATH": str(root),
        "PYTHONIOENCODING": "utf-8",
    }
    result = subprocess.run(
        [sys.executable, "-m", "voxvault.cli", "serve", "--status", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=60,
    )
    return result.returncode, json.loads(result.stdout)


class _Service:
    """Just enough of the resident service for its HTTP handler."""

    _secret = SECRET

    def __init__(self, health: dict) -> None:
        self._health = health

    def touch(self) -> None:
        pass

    def health(self) -> dict:
        return self._health

    def release_thread_resources(self) -> None:
        pass


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    folder = tmp_path / "perfil"
    folder.mkdir()
    return folder


@pytest.fixture
def running(profile: Path, tmp_path: Path):
    """A service answering on loopback, published where the CLI looks."""

    def start(health: dict, data_dir: Path) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(_Service(health)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        publish(
            Rendezvous(
                endereco=f"127.0.0.1:{server.server_address[1]}",
                segredo=SECRET,
                pid=os.getpid(),
                diretorio_de_dados=str(data_dir),
            ),
            profile / ".voxvault" / "servico.json",
        )

    servers: list[ThreadingHTTPServer] = []
    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_without_a_service_it_answers_not_running(profile: Path, tmp_path: Path) -> None:
    code, answer = _status(profile, tmp_path / "dados")

    assert code == 0
    assert answer == {
        "em_execucao": False,
        "gravacao_ativa": False,
        "fila_pendente": 0,
        "diretorio_de_dados": str(tmp_path / "dados"),
    }


def test_a_recording_in_progress_is_reported(profile: Path, tmp_path: Path, running) -> None:
    servico = tmp_path / "dados-do-servico"
    running({"gravacao_ativa": True, "fila_pendente": 2}, servico)

    code, answer = _status(profile, tmp_path / "outra")

    assert code == 0
    assert answer == {
        "em_execucao": True,
        "gravacao_ativa": True,
        "fila_pendente": 2,
        # The service's own data directory, not the one this process resolved.
        "diretorio_de_dados": str(servico),
    }


def test_an_idle_service_is_running_and_not_recording(
    profile: Path, tmp_path: Path, running
) -> None:
    running({"gravacao_ativa": False, "fila_pendente": 0}, tmp_path / "dados")

    code, answer = _status(profile, tmp_path / "dados")

    assert code == 0
    assert answer["em_execucao"] is True
    assert answer["gravacao_ativa"] is False


def test_a_rendezvous_left_by_a_dead_process_is_not_a_service(
    profile: Path, tmp_path: Path
) -> None:
    publish(
        Rendezvous(endereco="127.0.0.1:9", segredo=SECRET, pid=2**31 - 7,
                   diretorio_de_dados=str(tmp_path / "antigo")),
        profile / ".voxvault" / "servico.json",
    )

    code, answer = _status(profile, tmp_path / "dados")

    assert code == 0
    assert answer["em_execucao"] is False
    assert answer["diretorio_de_dados"] == str(tmp_path / "dados")


def test_a_live_process_with_nobody_listening_is_not_a_service(
    profile: Path, tmp_path: Path
) -> None:
    """The published process id now belongs to somebody else."""
    publish(
        Rendezvous(endereco="127.0.0.1:9", segredo=SECRET, pid=os.getpid(),
                   diretorio_de_dados=str(tmp_path / "antigo")),
        profile / ".voxvault" / "servico.json",
    )

    code, answer = _status(profile, tmp_path / "dados")

    assert code == 0
    assert answer["em_execucao"] is False
