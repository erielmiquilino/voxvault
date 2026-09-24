"""The domain vocabulary reaches every window of a meeting, not only the first.

Passed as an initial prompt, it counted for the first 30 s of speech: with the
previous text not carried over between windows, the prompt is reset after
the first one. Measured on a real meeting, as hotwords it turned a misheard
product name into the right one twice and fixed two other terms, with the
word count unchanged.
"""

from __future__ import annotations

import wave
from pathlib import Path

from voxvault.config import Config
from voxvault.engine.local_whisper import LocalWhisperEngine


def _speech_like(path: Path) -> Path:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16_000)
        out.writeframes(b"\x10\x00" * 16_000)
    return path


def _engine_recording_calls(tmp_path: Path) -> tuple[LocalWhisperEngine, list[dict]]:
    calls: list[dict] = []

    class Model:
        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            return iter(()), None

    engine = LocalWhisperEngine(Config(data_dir=tmp_path))
    engine._model = Model()
    # Placement decided up front: this test is about the call, not the GPU.
    engine._device, engine._compute_type, engine._version = "cpu", "int8", "teste"
    return engine, calls


def test_the_vocabulary_goes_to_every_window_as_hotwords(tmp_path: Path) -> None:
    engine, calls = _engine_recording_calls(tmp_path)

    engine.transcribe(_speech_like(tmp_path / "fala.wav"), vocabulary="  Code Review, MDF-e ")

    assert calls[0]["hotwords"] == "Code Review, MDF-e"
    assert calls[0].get("initial_prompt") is None, "so valeria na primeira janela"


def test_without_a_vocabulary_nothing_is_hinted(tmp_path: Path) -> None:
    engine, calls = _engine_recording_calls(tmp_path)

    engine.transcribe(_speech_like(tmp_path / "fala.wav"), vocabulary="   ")

    assert calls[0]["hotwords"] is None
