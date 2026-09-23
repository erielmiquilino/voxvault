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
| Default model, GPU too small for it    | aviso   | available on CPU, warned |

With the model left at its default the model itself follows the hardware
(:func:`default_model_for`): the best one the GPU holds, and the turbo on the
CPU. A model somebody chose is taken as chosen and goes through the matrix.
"""

from __future__ import annotations

import enum
import os
import shutil
import subprocess
from dataclasses import dataclass

from ..config import SOURCE_DEFAULT, Config

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

#: The default by hardware, when nobody chose a model. The best model the GPU
#: holds; and on the CPU the turbo, which measured faster than large-v3 there
#: (1.37x real time against 0.96x on a six-core Ryzen) and produced the most
#: complete transcript of the three configurations compared.
DEFAULT_GPU_MODEL = "large-v3"
DEFAULT_SMALL_GPU_MODEL = "large-v3-turbo"
DEFAULT_CPU_MODEL = "large-v3-turbo"

#: Diagnostic switch: behave as a machine without a GPU. Read here and by the
#: app's own hardware detection, so a CPU-prepared environment on a machine
#: that does have a GPU is exercised end to end.
FORCE_CPU_ENV = "VOXVAULT_FORCAR_CPU"

CPU_WARNING = (
    "Sem GPU NVIDIA, a transcricao roda na CPU, cerca de 1,4x o tempo real num "
    "processador de 6 nucleos -- uma reuniao de 1 h leva por volta de 45 min."
)


def forced_cpu() -> bool:
    return os.environ.get(FORCE_CPU_ENV, "").strip() == "1"


def default_model_for(gpu_total_mb: int) -> tuple[str, str]:
    """The model the defaults pick for a GPU of this size, and where it runs.

    Zero means no GPU. The memory each model needs is the matrix's own, so the
    default is never a model the matrix would then refuse.
    """
    if gpu_total_mb >= required_vram_mb(DEFAULT_GPU_MODEL):
        return DEFAULT_GPU_MODEL, "cuda"
    if gpu_total_mb >= required_vram_mb(DEFAULT_SMALL_GPU_MODEL):
        return DEFAULT_SMALL_GPU_MODEL, "cuda"
    return DEFAULT_CPU_MODEL, "cpu"


def model_is_default(config: Config) -> bool:
    """True when nobody chose the model: not a file, not the environment."""
    return config.source_of("model") == SOURCE_DEFAULT


def effective_model(config: Config) -> str:
    """The model a transcription uses: the chosen one, or the hardware's."""
    if not model_is_default(config):
        return config.model
    gpu = query_nvidia_gpu()
    return default_model_for(gpu[1] if gpu else 0)[0]


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

    Returns (name, total MB, free MB), or None when no NVIDIA GPU answers --
    or when the diagnostic switch asks to behave as if none did.
    """
    if forced_cpu():
        return None
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
    from .cuda_runtime import load_required_dlls

    ok, reason = load_required_dlls()
    if not ok:
        return False, reason

    try:
        import ctranslate2
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


def probe_inference(
    config: Config, *, model: str | None = None, hardware_default: bool = False
) -> InferenceCapability:
    """Resolve the situation this machine is in, and what follows from it.

    ``hardware_default`` says the model is the one the defaults picked for
    this GPU. A GPU too small even for the turbo then means the CPU, as the
    default table says, and not a refusal: nobody asked for a model that does
    not fit.
    """
    model = model or config.model
    gpu = query_nvidia_gpu()
    too_small_for_any = (
        gpu is not None and hardware_default
        and default_model_for(gpu[1])[1] == "cpu"
    )

    if gpu is None or too_small_for_any:
        # No compatible GPU in the hardware. CPU is a legitimate degradation
        # here, because nothing is broken -- the machine simply has no GPU.
        detail = (
            "Nenhuma GPU NVIDIA compativel encontrada no hardware."
            if gpu is None else
            f"A {gpu[0]} tem {gpu[1]} MB, menos do que o menor modelo padrao "
            f"precisa; a transcricao usa a CPU."
        )
        return InferenceCapability(
            situation=Situation.NO_GPU,
            status="aviso",
            transcription_available=True,
            device="cpu",
            compute_type="int8",
            detail=detail,
            warning=CPU_WARNING,
            gpu_name=gpu[0] if gpu else "",
            gpu_total_mb=gpu[1] if gpu else 0,
            gpu_free_mb=gpu[2] if gpu else 0,
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
