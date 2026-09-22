"""Transcription engines.

Importing this package must stay cheap: the CLI and the MCP server touch these
names to build an engine only when they actually transcribe, and the inference
runtime costs seconds to load.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import (
    InsufficientResourceError,
    MissingPrerequisiteError,
    ProviderFailureError,
    TranscriptionEngine,
    TranscriptionInterrupted,
    UnreadableInputError,
    validate_segments,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..config import Config

__all__ = [
    "InsufficientResourceError",
    "MissingPrerequisiteError",
    "ProviderFailureError",
    "TranscriptionEngine",
    "TranscriptionInterrupted",
    "UnreadableInputError",
    "build_engine",
    "validate_segments",
]

#: Registered implementations. Adding an online provider means adding a key
#: here and a class that satisfies the contract -- nothing else changes.
_ENGINES = {
    "faster-whisper": "voxvault.engine.local_whisper:LocalWhisperEngine",
}


def build_engine(
    config: "Config", *, name: str = "faster-whisper", model: str | None = None
) -> TranscriptionEngine:
    """Construct the configured engine without importing the others."""
    try:
        target = _ENGINES[name]
    except KeyError:
        known = ", ".join(sorted(_ENGINES))
        raise ValueError(
            f"Motor de transcricao desconhecido: '{name}'. Conhecidos: {known}."
        ) from None

    module_name, _, class_name = target.partition(":")
    from importlib import import_module

    cls = getattr(import_module(module_name), class_name)
    return cls(config, model=model)
