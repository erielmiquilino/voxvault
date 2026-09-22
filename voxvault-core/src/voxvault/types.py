"""Core value types.

Deliberately free of third-party imports: the command-line surface, the MCP
server and the capture threads all touch these, and none of them should pay
for numpy or an inference runtime to describe a segment.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Track(enum.StrEnum):
    """Which physical source a piece of audio came from.

    Speaker attribution in VoxVault is physical, not inferred: MIC is the
    person using the machine, SYSTEM is the whole mix the endpoint played.
    """

    MIC = "mic"
    SYSTEM = "system"


class Speaker(enum.StrEnum):
    """Attribution presented to a reader.

    UNKNOWN exists for imported media: a file dropped in has neither a
    microphone nor a system track, and forcing it into one of the two would
    state an attribution the audio does not support.
    """

    ME = "eu"
    OTHERS = "outros"
    UNKNOWN = "desconhecido"


TRACK_TO_SPEAKER: dict[Track, Speaker] = {
    Track.MIC: Speaker.ME,
    Track.SYSTEM: Speaker.OTHERS,
}


class MeetingState(enum.StrEnum):
    """State of a meeting's own lifecycle -- not of its transcription."""

    RECORDING = "gravando"
    PAUSED = "pausada"
    RECORDED = "gravada"
    PARTIAL = "parcial"
    FAILED = "falha"


class AttemptState(enum.StrEnum):
    """State of the current transcription attempt.

    Kept separate from transcript availability on purpose: a meeting being
    reprocessed has a complete transcript available AND an attempt running.
    Collapsing the two forces every surface to lie about one of them.
    """

    NONE = "nenhuma"
    QUEUED = "na_fila"
    RUNNING = "em_execucao"
    FAILED = "falhou"


class RevisionState(enum.StrEnum):
    COMPLETE = "completa"
    PARTIAL = "parcial"


@dataclass(frozen=True, slots=True)
class Segment:
    """One stretch of transcribed speech.

    Offsets are integer milliseconds relative to the start of the audio the
    engine was given, never to any intermediate conversion.
    """

    start_ms: int
    end_ms: int
    text: str

    def __post_init__(self) -> None:
        if self.start_ms < 0:
            raise ValueError(f"start_ms deve ser >= 0, recebido {self.start_ms}")
        if self.end_ms <= self.start_ms:
            raise ValueError(
                f"end_ms ({self.end_ms}) deve ser maior que start_ms "
                f"({self.start_ms})"
            )
        if not self.text.strip():
            raise ValueError("texto do segmento nao pode ser vazio")


@dataclass(frozen=True, slots=True)
class EngineInfo:
    """Identity of whatever produced a transcription.

    Frozen at the start of an attempt and stored with the revision, so any
    text can be attributed to the engine and settings that made it.
    """

    name: str
    model: str
    compute_type: str
    device: str
    version: str = ""

    def identifier(self) -> str:
        parts = [self.name, self.model, self.device, self.compute_type]
        if self.version:
            parts.append(self.version)
        return "/".join(parts)


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    segments: tuple[Segment, ...]
    engine: EngineInfo
    language: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """A segment placed on the merged, two-track meeting timeline."""

    start_ms: int
    end_ms: int
    text: str
    track: Track
    speaker: Speaker
    segment_id: int


@dataclass(slots=True)
class DiagnosticItem:
    """One line of the environment report."""

    key: str
    label: str
    status: str  # "ok" | "aviso" | "falha"
    detail: str = ""
    remedy: str = ""


@dataclass(slots=True)
class CapturePacket:
    """One buffer as the OS handed it over.

    device_position and qpc_ns come from the capture interface itself. They are
    the whole reason a backend is adopted: a timestamp taken when the callback
    runs carries scheduling delay, which is exactly the error alignment exists
    to remove.
    """

    data: bytes
    frames: int
    device_position: int
    qpc_ns: int
    discontinuity: bool
    silent: bool = False
    timestamp_valid: bool = True
