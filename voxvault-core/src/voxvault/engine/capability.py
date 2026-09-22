"""Where inference is allowed to run, and when it is refused.

This module is the single authority for the availability matrix. The engine
defers to it and must not hold a divergent policy of its own -- that split is
how "missing prerequisite blocks the operation" and "no acceleration degrades
to CPU" end up contradicting each other.

| Situation                              | Item    | Transcription            |
|----------------------------------------|---------|--------------------------|
| GPU present, libraries load            | ok      | available, accelerated   |
| No compatible GPU in the hardware      | aviso   | available on CPU, warned |
| GPU present, libraries broken          | falha   | REFUSED unless opted in  |
| Model does not fit in GPU memory       | falha   | refused for that model   |
"""

from __future__ import annotations

import enum
import shutil
import subprocess
from dataclasses import dataclass

from ..config import Config

#: Approximate weight footprint in GPU memory, in MB, at float16. Decoding
#: needs headroom on top, which is why the check adds a margin.
MODEL_VRAM_MB: dict[str, int] = {
    "tiny": 250,
    "base": 400,
    "small": 900,
    "medium": 2400,
    "large-v1": 4700,
    "large-v2": 4700,
    "large-v3": 4700,
    "large-v3-turbo": 1600,
    "distil-large-v3": 1600,
}

#: Headroom for activations and the decoding beam.
VRAM_MARGIN_MB = 900

#: Offered when the requested model does not fit, cheapest adequate first.
_SMALLER = ["large-v3-turbo", "medium", "small", "base"]


class Situation(enum.StrEnum):
    GPU_READY = "gpu_pronta"
    NO_GPU = "sem_gpu"
    GPU_LIBS_BROKEN = "bibliotecas_quebradas"
    MODEL_TOO_LARGE = "modelo_nao_cabe"


@dataclass(slots=True)
class InferenceCapability:
    situation: Situation
    status: str  # "ok" | "aviso" | "falha"
    transcription_available: bool
    device: str  # "cuda" | "cpu" | ""
    compute_type: str  # "float16" | "int8" | ""
    detail: str
    remedy: str = ""
    warning: str = ""
    gpu_name: str = ""
    gpu_total_mb: int = 0
    gpu_free_mb: int = 0


def required_vram_mb(model: str) -> int:
    base = MODEL_VRAM_MB.get(model)
    if base is None:
        # Unknown model name: assume large, which is the safe direction.
        base = MODEL_VRAM_MB["large-v3"]
    return base + VRAM_MARGIN_MB


def suggest_smaller(model: str, available_mb: int) -> str:
    for candidate in _SMALLER:
        if candidate == model:
            continue
        if required_vram_mb(candidate) <= available_mb:
            return candidate
    return ""


def query_nvidia_gpu() -> tuple[str, int, int] | None:
    """Ask the driver about the GPU without importing any inference runtime.

    Returns (name, total MB, free MB), or None when no NVIDIA GPU answers.
    """
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    first = out.stdout.strip().splitlines()[0]
    parts = [p.strip() for p in first.split(",")]
    if len(parts) < 3:
        return None
    try:
        return parts[0], int(float(parts[1])), int(float(parts[2]))
    except ValueError:
        return None


def cuda_libraries_load() -> tuple[bool, str]:
    """Check that the inference runtime can actually reach CUDA.

    Two separate things have to be true, and checking only one of them is how
    this probe lied once already: the driver has to enumerate a device, *and*
    cuBLAS and cuDNN have to load in this process. `get_cuda_device_count()`
    answers the first and says nothing about the second -- it returns a happy
    number while cuBLAS sits somewhere the Windows loader never looks, and the
    truth only surfaces after a multi-gigabyte model download.
    """
    from .cuda_runtime import load_required_dlls  # noqa: PLC0415

    ok, reason = load_required_dlls()
    if not ok:
        return False, reason

    try:
        import ctranslate2  # noqa: PLC0415  (deliberately lazy: ~0.3 s to import)
    except Exception as exc:  # pragma: no cover - environment dependent
        return False, f"nao foi possivel importar ctranslate2: {exc}"
    try:
        count = ctranslate2.get_cuda_device_count()
    except Exception as exc:  # pragma: no cover - environment dependent
        return False, f"ctranslate2 nao conseguiu consultar dispositivos CUDA: {exc}"
    if count < 1:
        return False, (
            "as bibliotecas carregaram, mas ctranslate2 nao enxerga nenhum "
            "dispositivo CUDA"
        )
    return True, ""


def probe_inference(config: Config, *, model: str | None = None) -> InferenceCapability:
    """Resolve the situation this machine is in, and what follows from it."""
    model = model or config.model
    gpu = query_nvidia_gpu()

    if gpu is None:
        # No compatible GPU in the hardware. CPU is a legitimate degradation
        # here, because nothing is broken -- the machine simply has no GPU.
        return InferenceCapability(
            situation=Situation.NO_GPU,
            status="aviso",
            transcription_available=True,
            device="cpu",
            compute_type="int8",
            detail="Nenhuma GPU NVIDIA compativel encontrada no hardware.",
            warning=(
                "A transcricao vai rodar em CPU com int8. Uma reuniao de uma "
                "hora pode levar varias horas para ser transcrita."
            ),
        )

    name, total_mb, free_mb = gpu
    loads, reason = cuda_libraries_load()

    if not loads:
        # GPU present but its libraries are broken. Falling back to CPU here
        # would hide a defect the user needs to fix, so it is refused unless
        # they opted in explicitly.
        if config.allow_cpu_fallback:
            return InferenceCapability(
                situation=Situation.GPU_LIBS_BROKEN,
                status="falha",
                transcription_available=True,
                device="cpu",
                compute_type="int8",
                detail=f"GPU {name} presente, mas {reason}",
                remedy="Instale nvidia-cublas-cu12 e nvidia-cudnn-cu12 no ambiente.",
                warning=(
                    "Execucao em CPU habilitada explicitamente por configuracao, "
                    "apesar da GPU presente com bibliotecas quebradas."
                ),
                gpu_name=name, gpu_total_mb=total_mb, gpu_free_mb=free_mb,
            )
        return InferenceCapability(
            situation=Situation.GPU_LIBS_BROKEN,
            status="falha",
            transcription_available=False,
            device="", compute_type="",
            detail=f"GPU {name} presente, mas {reason}",
            remedy=(
                "Instale nvidia-cublas-cu12 e nvidia-cudnn-cu12, ou habilite "
                "allow_cpu_fallback na configuracao para aceitar a lentidao."
            ),
            gpu_name=name, gpu_total_mb=total_mb, gpu_free_mb=free_mb,
        )

    needed = required_vram_mb(model)
    if needed > total_mb:
        smaller = suggest_smaller(model, total_mb)
        return InferenceCapability(
            situation=Situation.MODEL_TOO_LARGE,
            status="falha",
            transcription_available=False,
            device="", compute_type="",
            detail=(
                f"O modelo '{model}' exige cerca de {needed} MB de memoria de "
                f"GPU, e a {name} tem {total_mb} MB no total."
            ),
            remedy=(
                f"Use o modelo '{smaller}'." if smaller
                else "Use um modelo menor ou habilite a execucao em CPU."
            ),
            gpu_name=name, gpu_total_mb=total_mb, gpu_free_mb=free_mb,
        )

    return InferenceCapability(
        situation=Situation.GPU_READY,
        status="ok",
        transcription_available=True,
        device="cuda",
        compute_type="float16",
        detail=(
            f"{name}, {total_mb} MB no total, {free_mb} MB livres. "
            f"O modelo '{model}' precisa de aproximadamente {needed} MB."
        ),
        gpu_name=name, gpu_total_mb=total_mb, gpu_free_mb=free_mb,
    )
