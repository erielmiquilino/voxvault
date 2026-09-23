"""Models on disk, the one download, and a core that never goes online.

Covers specs/app-distribution "Nenhum acesso a rede fora do preparo" and the
model download of the first-use preparation: progress as JSON lines every
quarter of a second, a missing model as a typed error that names the fix, and
a transcription that completes with every proxy pointing at a dead port.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

from voxvault.cli.__main__ import _build_parser, _offline_unless_downloading, main
from voxvault.engine import models

DEAD_PROXY = "http://127.0.0.1:9"


@pytest.fixture(autouse=True)
def _hub_environment(monkeypatch):
    """The entry points switch the client offline for their whole process.

    In the test process that would leak from one test into the next.
    """
    for name in ("HF_HUB_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "HF_HUB_DISABLE_XET",
                 "HF_HUB_DISABLE_SYMLINKS", "HF_HUB_DISABLE_SYMLINKS_WARNING"):
        monkeypatch.delenv(name, raising=False)


def _json_lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# -- the download ---------------------------------------------------------

COMMIT = "abc123"


def _git_blob(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


class _Resposta:
    """A server response: the rest of the file from ``start``, slowly."""

    def __init__(self, data: bytes, start: int, pause_s: float, honours_range: bool):
        self.status = 206 if start and honours_range else 200
        self._buffer = io.BytesIO(data[start:] if self.status == 206 else data)
        self._pause_s = pause_s

    def read(self, size: int) -> bytes:
        block = self._buffer.read(size)
        if block:
            time.sleep(self._pause_s)
        return block

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


def _fake_repository(
    monkeypatch, *, weights: bytes, pause_s: float = 0.0, honours_range: bool = True,
    served: bytes | None = None,
) -> dict:
    """A repository with a small config and ``weights`` as model.bin."""
    config = b'{"alignment_heads": []}'
    files = {"config.json": config, "model.bin": weights, "tokenizer.json": b"{}",
             "vocabulary.json": b"[]"}
    remote = models.RemoteModel(commit=COMMIT, files=tuple(
        models.RemoteFile(
            name=name, size=len(content),
            sha256=hashlib.sha256(content).hexdigest() if name == "model.bin" else None,
            blob_id=_git_blob(content),
        )
        for name, content in files.items()
    ))
    calls = {"starts": {}, "remote": 0}

    def remote_model(repo):
        calls["remote"] += 1
        calls["repo"] = repo
        return remote

    def open_(url, start):
        calls["starts"][url] = start
        data = served if (served is not None and url == "model.bin") else files[url]
        return _Resposta(data, start, pause_s if url == "model.bin" else 0.0, honours_range)

    monkeypatch.setattr(models, "_remote_model", remote_model)
    monkeypatch.setattr(models, "_file_url", lambda repo, commit, name: name)
    monkeypatch.setattr(models, "_open", open_)
    monkeypatch.setattr(models, "CHUNK", 1000)
    return calls


def _download(tmp_path: Path) -> int:
    return main(["--data-dir", str(tmp_path), "models", "download",
                 "--model", "tiny", "--json"])


def _snapshot_of(tmp_path: Path) -> Path:
    return tmp_path / "models" / "models--Systran--faster-whisper-tiny" / "snapshots" / COMMIT


def test_the_download_reports_json_lines_four_times_a_second(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    weights = bytes(range(256)) * 24  # 6144 bytes, in chunks of 1000
    calls = _fake_repository(monkeypatch, weights=weights, pause_s=0.2)

    assert _download(tmp_path) == 0

    lines = _json_lines(capsys.readouterr().out)
    progress, last = lines[:-1], lines[-1]
    total = len(weights) + len(b'{"alignment_heads": []}') + 4
    assert all(set(line) == {"baixado", "total"} for line in progress)
    assert all(line["total"] == total for line in progress)
    feitos = [line["baixado"] for line in progress]
    assert feitos == sorted(feitos), "o progresso nunca anda para tras"
    assert feitos[-1] == total
    # 1.4 s of transfer at one line per 250 ms, give or take the scheduler.
    assert 3 <= len(progress) <= 9
    assert last == {"concluido": True, "modelo": "tiny", "caminho": str(_snapshot_of(tmp_path))}
    # Every file in the snapshot, and the branch reference pointing at it.
    assert (_snapshot_of(tmp_path) / "model.bin").read_bytes() == weights
    refs = tmp_path / "models" / "models--Systran--faster-whisper-tiny" / "refs" / "main"
    assert refs.read_text(encoding="utf-8") == COMMIT
    assert calls["repo"] == "Systran/faster-whisper-tiny"
    assert models.installed(tmp_path / "models", "tiny") == _snapshot_of(tmp_path)


def test_an_interrupted_download_continues_from_its_partial(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Scenario: conexao perdida no meio do modelo -- a retomada nao recomeca."""
    weights = bytes(range(256)) * 24
    calls = _fake_repository(monkeypatch, weights=weights, pause_s=0.05)
    partial = (tmp_path / "models" / "models--Systran--faster-whisper-tiny"
               / models.PARTIAL_DIR / "model.bin.incomplete")
    partial.parent.mkdir(parents=True)
    partial.write_bytes(weights[:2500])

    assert _download(tmp_path) == 0

    assert calls["starts"]["model.bin"] == 2500, "pediu o arquivo inteiro de novo"
    assert (_snapshot_of(tmp_path) / "model.bin").read_bytes() == weights
    progress = _json_lines(capsys.readouterr().out)[:-1]
    assert progress[0]["baixado"] >= 2500
    assert not partial.exists()


def test_a_server_that_ignores_the_range_starts_the_file_over(
    tmp_path: Path, monkeypatch
) -> None:
    weights = bytes(range(256)) * 24
    _fake_repository(monkeypatch, weights=weights, honours_range=False)
    partial = (tmp_path / "models" / "models--Systran--faster-whisper-tiny"
               / models.PARTIAL_DIR / "model.bin.incomplete")
    partial.parent.mkdir(parents=True)
    partial.write_bytes(weights[:2500])

    assert _download(tmp_path) == 0

    # Whole, not the partial with the whole file appended to it.
    assert (_snapshot_of(tmp_path) / "model.bin").read_bytes() == weights


def test_a_file_that_arrives_different_is_discarded(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    weights = bytes(range(256)) * 24
    _fake_repository(monkeypatch, weights=weights, served=b"\0" * len(weights))

    assert _download(tmp_path) == 1

    [line] = [x for x in _json_lines(capsys.readouterr().out) if "erro" in x]
    assert "model.bin chegou diferente" in line["erro"]
    repo = tmp_path / "models" / "models--Systran--faster-whisper-tiny"
    assert not (repo / models.PARTIAL_DIR / "model.bin.incomplete").exists()
    assert not (repo / "refs" / "main").exists(), "o modelo nao pode parecer completo"
    assert models.installed(tmp_path / "models", "tiny") is None


def test_an_unknown_model_is_refused_without_going_online(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(models, "_remote_model", lambda repo: pytest.fail("rede"))

    code = main(["--data-dir", str(tmp_path), "models", "download", "--model", "enorme"])

    assert code == 1
    erro = capsys.readouterr().err
    assert "Modelo desconhecido: 'enorme'" in erro
    assert "large-v3-turbo" in erro


def test_a_download_without_a_connection_says_so(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    def offline(repo):
        raise ConnectionError("Failed to resolve 'huggingface.co' (getaddrinfo failed)")

    monkeypatch.setattr(models, "_remote_model", offline)

    assert _download(tmp_path) == 1

    [line] = _json_lines(capsys.readouterr().out)
    assert line["erro"].startswith("Sem conexao com a internet")
    assert "huggingface.co" in line["erro"]


# -- offline everywhere else -------------------------------------------


@pytest.mark.parametrize("argv", [
    ["doctor"], ["serve"], ["list"], ["transcribe", "a.wav"], ["queue"],
])
def test_every_other_command_runs_with_the_client_offline(argv, monkeypatch) -> None:
    _offline_unless_downloading(_build_parser().parse_args(argv))

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_the_download_is_the_one_command_allowed_online(monkeypatch) -> None:
    _offline_unless_downloading(_build_parser().parse_args(["models", "download"]))

    assert "HF_HUB_OFFLINE" not in os.environ
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"


def test_a_model_that_is_not_on_disk_is_a_typed_error_with_no_connection(
    tmp_path: Path, monkeypatch
) -> None:
    """Scenario: modelo ausente -- erro tipado com a acao, sem tentar a rede."""
    pytest.importorskip("faster_whisper")
    from voxvault.config import load_config
    from voxvault.engine.base import MissingPrerequisiteError
    from voxvault.engine.local_whisper import LocalWhisperEngine

    monkeypatch.setenv("VOXVAULT_FORCAR_CPU", "1")
    monkeypatch.setenv("HTTPS_PROXY", DEAD_PROXY)
    monkeypatch.setenv("HTTP_PROXY", DEAD_PROXY)
    attempts: list[object] = []
    real_connect = socket.socket.connect

    def connect(self, address):
        attempts.append(address)
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    config = load_config({"data_dir": str(tmp_path)}, env={},
                         config_path=tmp_path / "ausente.json")

    with pytest.raises(MissingPrerequisiteError) as caught:
        LocalWhisperEngine(config, model="tiny")._ensure_model()

    mensagem = str(caught.value)
    assert "modelo tiny" in mensagem
    assert "voxvault models download --model tiny" in mensagem
    assert attempts == [], f"tentou a rede: {attempts}"


def test_installed_answers_from_the_disk_alone(tmp_path: Path) -> None:
    pytest.importorskip("huggingface_hub")

    assert models.installed(tmp_path, "tiny") is None


def _snapshot(models_dir: Path, *files: str) -> Path:
    """The cache layout the client leaves: a branch ref and a snapshot."""
    repo = models_dir / "models--Systran--faster-whisper-tiny"
    (repo / "refs").mkdir(parents=True)
    (repo / "refs" / "main").write_text("abc123", encoding="utf-8")
    snapshot = repo / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    for name in files:
        (snapshot / name).write_bytes(b"{}")
    return snapshot


def test_an_interrupted_download_is_not_an_installed_model(tmp_path: Path) -> None:
    pytest.importorskip("huggingface_hub")
    _snapshot(tmp_path, "config.json", "tokenizer.json", "vocabulary.txt")
    (tmp_path / "models--Systran--faster-whisper-tiny" / "blobs").mkdir()
    (tmp_path / "models--Systran--faster-whisper-tiny" / "blobs" / "x.incomplete").write_bytes(
        b"\0" * 10
    )

    assert models.installed(tmp_path, "tiny") is None


def test_a_complete_model_is_found_and_not_downloaded_again(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    pytest.importorskip("huggingface_hub")
    snapshot = _snapshot(tmp_path / "models", "config.json", "model.bin",
                         "tokenizer.json", "vocabulary.txt")
    monkeypatch.setattr(models, "_remote_model", lambda repo: pytest.fail("rede"))
    monkeypatch.setattr(models, "download", lambda *a, **k: pytest.fail("baixou de novo"))

    code = main(["--data-dir", str(tmp_path), "models", "download",
                 "--model", "tiny", "--json"])

    assert code == 0
    [line] = _json_lines(capsys.readouterr().out)
    assert line == {"concluido": True, "modelo": "tiny", "caminho": str(snapshot),
                    "ja_presente": True}


# -- a real transcription, with the network made unusable ---------------


def _tone(path: Path, seconds: float = 3.0) -> Path:
    import math

    rate = 16_000
    frames = bytearray()
    for n in range(int(rate * seconds)):
        value = int(6000 * math.sin(2 * math.pi * 220 * n / rate))
        frames += value.to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(frames))
    return path


@pytest.mark.gpu
def test_a_transcription_completes_with_every_proxy_dead(tmp_path: Path) -> None:
    """Scenario: sem rede depois do preparo -- a transcricao conclui.

    Uses the models this machine already has, from the effective data
    directory, and runs the real command line in its own process, where the
    proxies point at a port nothing listens on.
    """
    from voxvault.config import load_config
    from voxvault.engine.capability import effective_model

    config = load_config()
    model = effective_model(config)
    if models.installed(config.models_dir, model) is None:
        pytest.skip(f"o modelo {model} nao esta em {config.models_dir}")

    env = {**os.environ, "HTTPS_PROXY": DEAD_PROXY, "HTTP_PROXY": DEAD_PROXY,
           "ALL_PROXY": DEAD_PROXY, "PYTHONIOENCODING": "utf-8"}
    env.pop("HF_HUB_OFFLINE", None)
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-m", "voxvault.cli", "transcribe",
         str(_tone(tmp_path / "tom.wav")), "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=600,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert f"/{model}/" in f"/{payload['motor']}/" or model in payload["motor"]
    assert time.monotonic() - started < 600


def test_the_progress_watcher_stops_with_the_download(tmp_path: Path, monkeypatch) -> None:
    """No thread is left counting after the download returns."""
    _fake_repository(monkeypatch, weights=b"x" * 3000, pause_s=0.01)
    before = {t.name for t in threading.enumerate()}

    models.download(tmp_path, "tiny", on_progress=lambda done, total: None)

    assert "voxvault-download" not in {t.name for t in threading.enumerate()} - before
