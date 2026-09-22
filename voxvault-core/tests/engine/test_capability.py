"""The availability matrix.

Covers specs/environment-check/spec.md "Matriz de disponibilidade por
capacidade" and the degradation policy in specs/transcription-engine/spec.md.

The one rule worth stating plainly: with a GPU present and its libraries
broken, transcription is REFUSED. Falling back to CPU there would turn a
one-hour meeting into hours of processing and hide a defect the user needs
to fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voxvault.config import Config
from voxvault.engine import capability as cap


@pytest.fixture
def config() -> Config:
    return Config(data_dir=Path(r"D:\VoxVault"), model="large-v3")


def test_no_gpu_degrades_to_cpu_with_a_warning(monkeypatch, config: Config) -> None:
    """Scenario: Maquina sem GPU -- aviso, e transcricao segue disponivel."""
    monkeypatch.setattr(cap, "query_nvidia_gpu", lambda: None)

    result = cap.probe_inference(config)

    assert result.situation is cap.Situation.NO_GPU
    assert result.status == "aviso"
    assert result.transcription_available is True
    assert result.device == "cpu"
    assert result.compute_type == "int8"
    assert result.warning, "a perda de desempenho tem de ser dita explicitamente"


def test_gpu_with_broken_libraries_refuses(monkeypatch, config: Config) -> None:
    """Scenario: GPU presente com bibliotecas quebradas -- recusa, nao CPU."""
    monkeypatch.setattr(cap, "query_nvidia_gpu", lambda: ("RTX 4060 Ti", 8188, 7000))
    monkeypatch.setattr(
        cap, "cuda_libraries_load", lambda: (False, "cuDNN nao carregou")
    )

    result = cap.probe_inference(config)

    assert result.status == "falha"
    assert result.transcription_available is False
    assert result.device == "", "nenhuma degradacao silenciosa para CPU"
    assert "cuDNN" in result.detail, "a biblioteca que falhou tem de ser nomeada"


def test_broken_libraries_run_on_cpu_only_when_opted_in(monkeypatch) -> None:
    config = Config(data_dir=Path(r"D:\VoxVault"), allow_cpu_fallback=True)
    monkeypatch.setattr(cap, "query_nvidia_gpu", lambda: ("RTX 4060 Ti", 8188, 7000))
    monkeypatch.setattr(cap, "cuda_libraries_load", lambda: (False, "cuBLAS ausente"))

    result = cap.probe_inference(config)

    assert result.transcription_available is True
    assert result.device == "cpu"
    assert result.status == "falha", "o ambiente segue quebrado, ainda que utilizavel"


def test_model_too_large_names_numbers_and_suggests_smaller(
    monkeypatch, config: Config
) -> None:
    """Scenario: Memoria de GPU insuficiente para o modelo pedido."""
    monkeypatch.setattr(cap, "query_nvidia_gpu", lambda: ("GTX 1050", 2048, 1800))
    monkeypatch.setattr(cap, "cuda_libraries_load", lambda: (True, ""))

    result = cap.probe_inference(config)

    assert result.situation is cap.Situation.MODEL_TOO_LARGE
    assert result.transcription_available is False
    assert "large-v3" in result.detail
    assert "2048" in result.detail
    assert result.remedy, "tem de sugerir um modelo menor"


def test_healthy_gpu_runs_float16(monkeypatch, config: Config) -> None:
    """Scenario: GPU disponivel."""
    monkeypatch.setattr(cap, "query_nvidia_gpu", lambda: ("RTX 4060 Ti", 8188, 7000))
    monkeypatch.setattr(cap, "cuda_libraries_load", lambda: (True, ""))

    result = cap.probe_inference(config)

    assert result.status == "ok"
    assert (result.device, result.compute_type) == ("cuda", "float16")


def test_suggestion_only_offers_a_model_that_actually_fits() -> None:
    assert cap.suggest_smaller("large-v3", 2600) == "large-v3-turbo"
    assert cap.suggest_smaller("large-v3", 200) == ""


def test_unknown_model_is_sized_as_large_not_as_free() -> None:
    """Guessing small for an unknown name would fail at load time instead."""
    assert cap.required_vram_mb("modelo-inventado") == cap.required_vram_mb("large-v3")
