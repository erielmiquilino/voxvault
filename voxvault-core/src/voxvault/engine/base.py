"""The boundary between VoxVault and any transcription engine.

Everything else in the system depends on this contract and never on a specific
implementation, so that replacing local transcription with an online provider
is writing a new class -- not a redesign.

Failures are typed by category because consumers act on them differently: a
corrupt file is the user's problem, a missing prerequisite is the setup's, and
insufficient hardware means "pick a smaller model".
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..errors import EngineError
from ..types import EngineInfo, TranscriptionResult


class UnreadableInputError(EngineError):
    """The decoder could not read the input file. Names the path."""

    def __init__(self, path: Path | str, reason: str = "") -> None:
        self.path = Path(path)
        suffix = f": {reason}" if reason else ""
        super().__init__(f"Nao foi possivel ler o arquivo '{self.path}'{suffix}")


class MissingPrerequisiteError(EngineError):
    """A required library or external program is absent or will not load."""

    def __init__(self, component: str, reason: str = "") -> None:
        self.component = component
        suffix = f": {reason}" if reason else ""
        super().__init__(f"Pre-requisito ausente ou nao carregavel: {component}{suffix}")


class InsufficientResourceError(EngineError):
    """The hardware cannot hold what was asked for. Names required vs available."""

    def __init__(
        self, model: str, required_mb: int, available_mb: int, suggestion: str = ""
    ) -> None:
        self.model = model
        self.required_mb = required_mb
        self.available_mb = available_mb
        hint = f" Sugestao: use o modelo '{suggestion}'." if suggestion else ""
        super().__init__(
            f"O modelo '{model}' exige aproximadamente {required_mb} MB de memoria "
            f"de GPU, mas ha {available_mb} MB disponiveis.{hint}"
        )


class ProviderFailureError(EngineError):
    """The engine itself failed while transcribing."""


class TranscriptionInterrupted(EngineError):
    """Transcription stopped before consuming all the audio.

    Raised rather than returning what was decoded so far: a partial result
    presented as complete is worse than no result, because nothing downstream
    can tell the difference.
    """


@runtime_checkable
class TranscriptionEngine(Protocol):
    """Takes an audio path, returns ordered segments and its own identity."""

    def info(self) -> EngineInfo:
        """Identify engine and effective settings.

        Recorded with every transcription, so a text produced on CPU is
        distinguishable from one produced on GPU when read back months later.
        """
        ...

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str = "pt",
        vocabulary: str = "",
        should_stop: StopCheck | None = None,
    ) -> TranscriptionResult:
        """Transcribe a file.

        ``should_stop`` is polled during decoding; when it returns True the
        engine raises :class:`TranscriptionInterrupted`. Offsets in the result
        are relative to the start of ``audio_path`` as given, regardless of any
        intermediate conversion. The input file is never modified.

        Audio with no speech is an empty sequence and a success, not an error.
        """
        ...


class StopCheck(Protocol):
    def __call__(self) -> bool: ...


def validate_segments(result: TranscriptionResult) -> TranscriptionResult:
    """Enforce the contract's ordering and integrity rules on a result.

    Implementations are trusted to be well-behaved, but a provider that drifts
    out of contract should fail here rather than corrupt a stored timeline.
    """
    previous = -1
    for segment in result.segments:
        if segment.start_ms < previous:
            raise ProviderFailureError(
                f"Segmentos fora de ordem: {segment.start_ms} apos {previous}"
            )
        previous = segment.start_ms
    return result
