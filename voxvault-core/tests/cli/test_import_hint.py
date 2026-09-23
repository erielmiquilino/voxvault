"""What ``voxvault import`` says comes next.

With the resident service running, the import is transcribed in seconds with
nothing else to do, and the command used to tell the person to run the queue
by hand anyway.
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from voxvault.service.rendezvous import Rendezvous, publish

BY_HAND = "Rode 'voxvault queue --run'."
BY_THE_SERVICE = "O servico residente vai transcreve-la."


def _tone(path: Path) -> Path:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16_000)
        out.writeframes(
            b"".join(
                struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 16_000)))
                for i in range(16_000)
            )
        )
    return path


def _import(profile: Path, data_dir: Path, media: Path) -> str:
    root = Path(__file__).resolve().parents[2] / "src"
    env = {
        **os.environ,
        "USERPROFILE": str(profile),
        "VOXVAULT_DATA_DIR": str(data_dir),
        "PYTHONPATH": str(root),
        "PYTHONIOENCODING": "utf-8",
    }
    result = subprocess.run(
        [sys.executable, "-m", "voxvault.cli", "import", str(media)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _service_serving(profile: Path, data_dir: Path) -> None:
    """A rendezvous whose process is alive: this one."""
    publish(
        Rendezvous(
            endereco="127.0.0.1:9",
            segredo="segredo-de-teste",
            pid=os.getpid(),
            diretorio_de_dados=str(data_dir),
        ),
        profile / ".voxvault" / "servico.json",
    )


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    folder = tmp_path / "perfil"
    folder.mkdir()
    return folder


def test_without_a_service_it_points_to_the_queue(profile: Path, tmp_path: Path) -> None:
    said = _import(profile, tmp_path / "dados", _tone(tmp_path / "fala.wav"))

    assert BY_HAND in said


def test_with_the_service_running_nothing_is_left_to_do(
    profile: Path, tmp_path: Path
) -> None:
    _service_serving(profile, tmp_path / "dados")

    said = _import(profile, tmp_path / "dados", _tone(tmp_path / "fala.wav"))

    assert BY_THE_SERVICE in said
    assert "queue --run" not in said


def test_a_service_on_another_data_folder_does_not_count(
    profile: Path, tmp_path: Path
) -> None:
    _service_serving(profile, tmp_path / "dados-do-servico")

    said = _import(profile, tmp_path / "outros-dados", _tone(tmp_path / "fala.wav"))

    assert BY_HAND in said
