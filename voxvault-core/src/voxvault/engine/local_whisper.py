"""Local transcription with faster-whisper (CTranslate2).

Two settings here are not tuning knobs but correctness requirements:

``vad_filter``
    Whisper fills silence with text learned from subtitle corpora. In pt-BR the
    classic artefact is a credits line for a subtitle community. Invented text
    is indistinguishable from real speech to whoever reads the transcript
    later, so voice activity detection runs before decoding.

``condition_on_previous_text=False``
    Conditioning each window on the previous decode lets a single error loop
    through the rest of the audio. A meeting is long enough for that to ruin
    everything after the first mistake.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from ..config import Config
from ..types import EngineInfo, Segment, TranscriptionResult
from .base import (
    InsufficientResourceError,
    MissingPrerequisiteError,
    ProviderFailureError,
    StopCheck,
    TranscriptionInterrupted,
)
from .capability import (
    Situation,
    default_model_for,
    model_is_default,
    probe_inference,
    query_nvidia_gpu,
    required_vram_mb,
    suggest_smaller,
)
from .media import normalized_audio, probe_duration_ms

ENGINE_NAME = "faster-whisper"


def _model_not_on_disk(exc: Exception) -> bool:
    """The Hugging Face client's way of saying the files are not in the cache."""
    names = {type(e).__name__ for e in (exc, exc.__cause__, exc.__context__) if e}
    return bool(names & {"LocalEntryNotFoundError", "EntryNotFoundError"}) or (
        "local_files_only" in str(exc) or "cannot find the requested files" in str(exc).lower()
    )


class LocalWhisperEngine:
    """faster-whisper behind the transcription contract.

    The model is loaded lazily and kept for the lifetime of the instance:
    loading `large-v3` costs seconds, and the resident service transcribes a
    queue, not a single file.
    """

    def __init__(self, config: Config, *, model: str | None = None) -> None:
        self._config = config
        #: Nobody chose a model: the hardware decides which (see
        #: :func:`capability.default_model_for`), once, at placement.
        self._hardware_default = model is None and model_is_default(config)
        self._model_name = model or config.model
        self._model = None
        self._device = ""
        self._compute_type = ""
        self._version = ""
        self._load_seconds = 0.0

    # -- contract ------------------------------------------------------

    def info(self) -> EngineInfo:
        if not self._device:
            self._resolve_placement()
        return EngineInfo(
            name=ENGINE_NAME,
            model=self._model_name,
            compute_type=self._compute_type,
            device=self._device,
            version=self._version,
        )

    def warm_up(self) -> None:
        """Load the model now rather than on the first transcription.

        The resident service calls this so the first meeting in a queue does
        not pay the load cost as if it were decoding cost, and the benchmark
        calls it to time loading separately from decoding.
        """
        self._ensure_model()

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str = "pt",
        vocabulary: str = "",
        should_stop: StopCheck | None = None,
    ) -> TranscriptionResult:
        source = Path(audio_path)
        model = self._ensure_model()

        with normalized_audio(source) as usable:
            duration_ms = probe_duration_ms(usable) or 0
            try:
                raw_segments, _info = model.transcribe(
                    str(usable),
                    language=language or None,
                    initial_prompt=vocabulary.strip() or None,
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 500},
                    condition_on_previous_text=False,
                    beam_size=5,
                    word_timestamps=False,
                )
            except RuntimeError as exc:
                raise self._translate_runtime_error(exc) from exc

            collected: list[Segment] = []
            try:
                # faster-whisper yields lazily; decoding happens as we iterate,
                # which is what makes interruption possible at all.
                for item in raw_segments:
                    if should_stop is not None and should_stop():
                        raise TranscriptionInterrupted(
                            f"Transcricao de '{source}' interrompida antes de "
                            f"processar todo o audio. Nenhum resultado parcial "
                            f"e devolvido como sucesso."
                        )
                    text = (item.text or "").strip()
                    if not text:
                        continue  # the contract forbids empty segments
                    start_ms = max(0, round(item.start * 1000))
                    end_ms = round(item.end * 1000)
                    if end_ms <= start_ms:
                        end_ms = start_ms + 1
                    collected.append(Segment(start_ms, end_ms, text))
            except TranscriptionInterrupted:
                raise
            except RuntimeError as exc:
                raise self._translate_runtime_error(exc) from exc

        collected.sort(key=lambda s: s.start_ms)
        return TranscriptionResult(
            segments=tuple(collected),
            engine=self.info(),
            language=language,
            duration_ms=duration_ms,
        )

    # -- internals -----------------------------------------------------

    def _resolve_placement(self) -> None:
        """Decide device and precision strictly by the availability matrix.

        With the model left at its default, the model itself is decided here
        too, from the GPU the machine has -- and recorded as such in every
        revision, so the transcript says what produced it.
        """
        if self._hardware_default:
            gpu = query_nvidia_gpu()
            self._model_name = default_model_for(gpu[1] if gpu else 0)[0]
        capability = probe_inference(
            self._config, model=self._model_name,
            hardware_default=self._hardware_default,
        )

        if not capability.transcription_available:
            if capability.situation is Situation.MODEL_TOO_LARGE:
                raise InsufficientResourceError(
                    self._model_name,
                    required_mb=required_vram_mb(self._model_name),
                    available_mb=capability.gpu_total_mb,
                    suggestion=suggest_smaller(
                        self._model_name, capability.gpu_total_mb
                    ),
                )
            raise MissingPrerequisiteError("CUDA", capability.detail)

        self._device = capability.device
        self._compute_type = capability.compute_type

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        if not self._device:
            self._resolve_placement()

        if self._device == "cuda":
            # Must happen before the runtime is imported: on Windows the
            # pip-shipped cuBLAS and cuDNN are invisible to the loader
            # otherwise, and the failure only appears at model construction.
            from .cuda_runtime import prepare_cuda_dll_path

            prepare_cuda_dll_path()

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise MissingPrerequisiteError("faster-whisper", str(exc)) from exc

        try:
            import ctranslate2

            self._version = getattr(ctranslate2, "__version__", "")
        except ImportError:
            self._version = ""

        download_root = self._config.models_dir
        download_root.mkdir(parents=True, exist_ok=True)

        # The model cache symlinks by default, and creating a symlink on
        # Windows needs Developer Mode or elevation. Without this, downloading
        # a model fails with "the client does not have the required privilege"
        # -- on a machine where everything else works. Copying costs disk and
        # always succeeds, which is the right trade for a cache.
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

        from .models import REPOSITORIES, installed, missing_model

        if self._model_name in REPOSITORIES and installed(
            download_root, self._model_name
        ) is None:
            # Absent, or left half-downloaded: either way not loadable, and
            # said as such before the runtime fails on a missing weights file.
            raise missing_model(self._model_name, download_root)

        started = time.monotonic()
        try:
            # From the disk only. A transcription never goes online: getting
            # the model is `voxvault models download`, and nothing else.
            self._model = WhisperModel(
                self._model_name,
                device=self._device,
                compute_type=self._compute_type,
                download_root=str(download_root),
                local_files_only=True,
            )
        except Exception as exc:
            if _model_not_on_disk(exc):
                raise missing_model(self._model_name, download_root) from exc
            raise self._translate_runtime_error(exc) from exc
        self._load_seconds = time.monotonic() - started
        return self._model

    def _translate_runtime_error(self, exc: Exception) -> Exception:
        """Turn a runtime failure into the contract's typed categories."""
        text = str(exc).lower()
        if "out of memory" in text or "cuda_error_out_of_memory" in text:
            capability = probe_inference(self._config, model=self._model_name)
            return InsufficientResourceError(
                self._model_name,
                required_mb=required_vram_mb(self._model_name),
                available_mb=capability.gpu_free_mb,
                suggestion=suggest_smaller(self._model_name, capability.gpu_free_mb),
            )
        if "cudnn" in text or "cublas" in text or "libcu" in text:
            return MissingPrerequisiteError("CUDA (cuBLAS/cuDNN)", str(exc))
        return ProviderFailureError(
            f"O motor '{ENGINE_NAME}' falhou com o modelo "
            f"'{self._model_name}': {exc}"
        )
