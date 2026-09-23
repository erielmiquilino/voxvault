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


# -- the default model follows the hardware --------------------------------

def _placement(monkeypatch, gpu, *, config: Config):
    """Where the real engine decides to run, with the GPU probe simulated."""
    from voxvault.engine.local_whisper import LocalWhisperEngine

    monkeypatch.setattr(cap, "query_nvidia_gpu", lambda: gpu)
    monkeypatch.setattr(cap, "cuda_libraries_load", lambda: (True, ""))
    import voxvault.engine.local_whisper as lw

    monkeypatch.setattr(lw, "query_nvidia_gpu", lambda: gpu)
    info = LocalWhisperEngine(config).info()
    return info.model, info.device, info.compute_type


@pytest.fixture
def default_config() -> Config:
    """No model configured anywhere: every field at its built-in default."""
    return Config(data_dir=Path(r"D:\VoxVault"))


@pytest.mark.parametrize(
    ("gpu", "expected"),
    [
        (("RTX 4060 Ti", 8188, 7000), ("large-v3", "cuda", "float16")),
        (("GTX 1650", 4096, 3800), ("large-v3-turbo", "cuda", "float16")),
        (("GT 1030", 2048, 1900), ("large-v3-turbo", "cpu", "int8")),
        (None, ("large-v3-turbo", "cpu", "int8")),
    ],
    ids=["gpu-8gb", "gpu-4gb", "gpu-2gb", "sem-gpu"],
)
def test_the_default_model_follows_the_spec_table(
    monkeypatch, default_config: Config, gpu, expected
) -> None:
    assert _placement(monkeypatch, gpu, config=default_config) == expected


def test_a_chosen_model_prevails_over_the_hardware_default(monkeypatch) -> None:
    """Scenario: Modelo configurado explicitamente em CPU."""
    chosen = Config(
        data_dir=Path(r"D:\VoxVault"), model="large-v3",
        sources={"model": "arquivo de configuracao C:/x/config.json"},
    )
    model, device, compute = _placement(monkeypatch, None, config=chosen)
    assert (model, device, compute) == ("large-v3", "cpu", "int8")
    warning = cap.probe_inference(chosen).warning
    assert "1,4x o tempo real" in warning and "45 min" in warning


def test_the_thresholds_are_the_matrix_own(default_config: Config) -> None:
    """The default never picks a model the matrix would refuse."""
    limite_large = cap.required_vram_mb("large-v3")
    limite_turbo = cap.required_vram_mb("large-v3-turbo")
    assert (limite_large, limite_turbo) == (5600, 2500)
    assert cap.default_model_for(limite_large) == ("large-v3", "cuda")
    assert cap.default_model_for(limite_large - 1) == ("large-v3-turbo", "cuda")
    assert cap.default_model_for(limite_turbo - 1) == ("large-v3-turbo", "cpu")


def test_the_diagnostic_switch_hides_the_gpu(monkeypatch) -> None:
    monkeypatch.setenv(cap.FORCE_CPU_ENV, "1")
    assert cap.query_nvidia_gpu() is None
