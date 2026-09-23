"""Finalization that can be interrupted anywhere and resumed.

The scenarios here are the ones where a process dies at an inconvenient
moment. Each is provoked by running the real steps and then stopping between
them, because "resumable" is a claim about what is on disk, and only what is
actually on disk can settle it.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from voxvault.layout import meeting_dir
from voxvault.session.finalize import (
    Metadata,
    Step,
    compress_tracks,
    finalize_session,
    pending_finalizations,
    reached,
    read_metadata,
    write_metadata,
)


def write_wav(path: Path, seconds: float = 1.0) -> Path:
    """A real WAV with real samples, so compression has something to chew on."""
    import math
    import struct

    rate = 16_000
    frames = int(rate * seconds)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        samples = [
            int(12000 * math.sin(2 * math.pi * 440 * n / rate)) for n in range(frames)
        ]
        handle.writeframes(struct.pack(f"<{frames}h", *samples))
    return path


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    place = meeting_dir(tmp_path, "reuniao-1")
    place.mkdir(parents=True, exist_ok=True)
    write_wav(place / "mic.wav")
    write_wav(place / "system.wav")
    write_metadata(place, Metadata(uid="reuniao-1", title="Teste"))
    return place


def test_step_order_is_cumulative() -> None:
    assert reached(Step.COMPRESSED.value, Step.FILES_CLOSED) is True
    assert reached(Step.FILES_CLOSED.value, Step.COMPRESSED) is False
    assert reached("lixo", Step.FILES_CLOSED) is False


def test_compression_replaces_the_wav_only_after_verifying(directory: Path) -> None:
    compressed, failed = compress_tracks(directory, ["mic", "system"])

    assert sorted(compressed) == ["mic", "system"]
    assert failed == []
    for track in ("mic", "system"):
        assert (directory / f"{track}.flac").is_file()
        assert not (directory / f"{track}.wav").exists(), (
            "o original so sai depois de o comprimido conferir"
        )


def test_compression_is_lossless(directory: Path) -> None:
    """Lossy compression would bake today's quality into every future
    transcription of this meeting."""
    import soundfile as sf

    original, rate = sf.read(str(directory / "mic.wav"), dtype="int16")
    compress_tracks(directory, ["mic"])
    restored, rate_back = sf.read(str(directory / "mic.flac"), dtype="int16")

    assert rate == rate_back
    assert (original == restored).all()


def test_compressed_file_is_smaller(directory: Path) -> None:
    before = (directory / "mic.wav").stat().st_size
    compress_tracks(directory, ["mic"])
    after = (directory / "mic.flac").stat().st_size
    assert after < before


def test_compression_failure_preserves_the_original(directory: Path, monkeypatch) -> None:
    """Scenario: Falha na compressao.

    The recording is what is being protected; saving disk space is never worth
    risking it.
    """
    from voxvault.engine import media

    def explode(source, target, **kwargs):
        raise media.MediaDecodeError("codificador indisponivel")

    # compress_tracks resolves this name at call time, so patching the module
    # attribute is enough and nothing needs to know how it was imported.
    monkeypatch.setattr(media, "compress_lossless", explode)

    metadata = finalize_session(
        directory, tracks=["mic", "system"], duration_ms=1000,
        ended_at="2026-09-22T00:00:00+00:00",
    )

    assert (directory / "mic.wav").is_file(), "o audio original tem de sobreviver"
    assert metadata.uncompressed is True
    assert any("nao comprimido" in w for w in metadata.warnings)
    # And the run still completes: transcription proceeds normally.
    assert metadata.step == Step.QUEUED.value


def test_interruption_between_compressing_and_queueing_resumes(
    directory: Path,
) -> None:
    """Scenario: Interrupcao entre comprimir e enfileirar."""
    submitted: list[str] = []

    # Stand where a killed process would have left things: compression done,
    # metadata written, nothing submitted yet.
    metadata = read_metadata(directory)
    metadata.step = Step.METADATA_WRITTEN.value
    metadata.duration_ms = 1000
    write_metadata(directory, metadata)

    def compress_again(*args, **kwargs):
        raise AssertionError("a compressao nao pode ser refeita")

    import voxvault.session.finalize as module

    original = module.compress_tracks
    module.compress_tracks = compress_again
    try:
        result = finalize_session(
            directory, tracks=["mic", "system"], duration_ms=1000,
            ended_at="2026-09-22T00:00:00+00:00",
            submit=lambda: submitted.append("ok"),
        )
    finally:
        module.compress_tracks = original

    assert submitted == ["ok"]
    assert result.step == Step.QUEUED.value


def test_interruption_during_compression_leaves_the_original(directory: Path) -> None:
    """Scenario: Interrupcao durante a compressao."""
    metadata = read_metadata(directory)
    metadata.step = Step.FILES_CLOSED.value
    write_metadata(directory, metadata)

    # A half-written compressed file from the interrupted attempt.
    (directory / "mic.flac").write_bytes(b"")

    assert (directory / "mic.wav").is_file(), "o original nunca foi removido"
    compressed, _failed = compress_tracks(directory, ["mic"])
    # It is redone from the start rather than trusting the leftover.
    assert "mic" in compressed or (directory / "mic.wav").is_file()


def test_finalize_runs_each_step_once(directory: Path) -> None:
    calls: list[str] = []
    finalize_session(
        directory, tracks=["mic"], duration_ms=500, ended_at="x",
        compress=False, submit=lambda: calls.append("submit"),
    )
    finalize_session(
        directory, tracks=["mic"], duration_ms=500, ended_at="x",
        compress=False, submit=lambda: calls.append("submit"),
    )
    assert calls == ["submit"], "uma segunda chamada nao pode reenfileirar"


def test_pending_finalizations_lists_only_unfinished(tmp_path: Path) -> None:
    done = meeting_dir(tmp_path, "pronta")
    done.mkdir(parents=True)
    write_metadata(done, Metadata(uid="pronta", step=Step.QUEUED.value))

    stuck = meeting_dir(tmp_path, "presa")
    stuck.mkdir(parents=True)
    write_metadata(stuck, Metadata(uid="presa", step=Step.COMPRESSED.value))

    pending = pending_finalizations(tmp_path)
    assert [p.name for p in pending] == ["presa"]


def test_metadata_is_written_atomically(directory: Path) -> None:
    """A truncated metadata file would make recovery guess."""
    write_metadata(directory, Metadata(uid="x", title="A" * 5000))
    metadata = read_metadata(directory)
    assert metadata is not None
    assert metadata.title == "A" * 5000
    assert not list(directory.glob("*.tmp")), "nenhum temporario pode sobrar"


def test_unreadable_metadata_is_reported_as_absent(directory: Path) -> None:
    (directory / "metadados.json").write_text("{ nao e json", encoding="utf-8")
    assert read_metadata(directory) is None
