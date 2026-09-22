"""Finishing a recording, in steps that can each be interrupted and resumed.

Four steps: close the captured files, compress them losslessly, write the
metadata and duration, and submit the session for transcription. Each is
idempotent and each records its progress on disk, so a process killed anywhere
in the sequence is recoverable at the next start rather than leaving a meeting
in a state nobody can name.

The durability point is deliberate and earlier than the end: once the audio
files are closed and the metadata is written, the session is safe. Everything
after that is derived work that the next start can redo.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC
from enum import StrEnum
from pathlib import Path

from ..layout import FINAL_SUFFIX, METADATA_NAME, RECORDING_SUFFIX, TRACK_BASENAME


class Step(StrEnum):
    """Where a finalization got to. Ordered: each implies the ones before."""

    NOT_STARTED = "nao_iniciada"
    FILES_CLOSED = "arquivos_fechados"
    COMPRESSED = "comprimida"
    METADATA_WRITTEN = "metadados_gravados"
    QUEUED = "enfileirada"


_ORDER = [
    Step.NOT_STARTED,
    Step.FILES_CLOSED,
    Step.COMPRESSED,
    Step.METADATA_WRITTEN,
    Step.QUEUED,
]


def reached(current: str, target: Step) -> bool:
    try:
        return _ORDER.index(Step(current)) >= _ORDER.index(target)
    except ValueError:
        return False


@dataclass(slots=True)
class Metadata:
    """What a meeting directory says about itself.

    Self-contained on purpose: a directory copied to another machine, or
    recovered after the database was lost, still describes its own recording.
    """

    uid: str
    title: str = ""
    started_at: str = ""
    ended_at: str = ""
    duration_ms: int = 0
    step: str = Step.NOT_STARTED.value
    tracks: dict[str, dict] = field(default_factory=dict)
    pauses: list[dict] = field(default_factory=list)
    devices: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    uncompressed: bool = False
    alignment: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_dict(self) -> dict:
        """Keys in pt-BR: this file is meant to be opened and read by a person."""
        return {
            "uid": self.uid,
            "titulo": self.title,
            "inicio": self.started_at,
            "fim": self.ended_at,
            "duracao_ms": self.duration_ms,
            "passo_finalizacao": self.step,
            "trilhas": self.tracks,
            "pausas": self.pauses,
            "dispositivos": self.devices,
            "avisos": self.warnings,
            "audio_nao_comprimido": self.uncompressed,
            "alinhamento": self.alignment,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Metadata:
        return cls(
            uid=raw.get("uid", ""),
            title=raw.get("titulo", ""),
            started_at=raw.get("inicio", ""),
            ended_at=raw.get("fim", ""),
            duration_ms=int(raw.get("duracao_ms", 0)),
            step=raw.get("passo_finalizacao", Step.NOT_STARTED.value),
            tracks=raw.get("trilhas", {}),
            pauses=raw.get("pausas", []),
            devices=raw.get("dispositivos", {}),
            warnings=raw.get("avisos", []),
            uncompressed=bool(raw.get("audio_nao_comprimido", False)),
            alignment=raw.get("alinhamento", {}),
        )


def metadata_path(directory: Path) -> Path:
    return directory / METADATA_NAME


def read_metadata(directory: Path) -> Metadata | None:
    path = metadata_path(directory)
    if not path.is_file():
        return None
    try:
        return Metadata.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def write_metadata(directory: Path, metadata: Metadata) -> Path:
    """Write metadata atomically.

    Atomic because a half-written metadata file is worse than none: recovery
    reads this to decide what still has to be done, and a truncated file would
    make it guess.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = metadata_path(directory)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(metadata.to_json(), encoding="utf-8")
    temporary.replace(path)
    return path


def record_step(directory: Path, step: Step, **updates) -> Metadata:
    metadata = read_metadata(directory) or Metadata(uid=directory.name)
    metadata.step = step.value
    for key, value in updates.items():
        setattr(metadata, key, value)
    write_metadata(directory, metadata)
    return metadata


def compress_tracks(directory: Path, tracks: list[str]) -> tuple[list[str], list[str]]:
    """Compress each track losslessly, keeping the original until it verifies.

    Returns (compressed, failed). A track whose compression fails keeps its
    uncompressed audio and stays perfectly usable -- the recording is the thing
    being protected, and saving disk space is not worth risking it.
    """
    from ..engine.media import compress_lossless, verify_lossless

    compressed: list[str] = []
    failed: list[str] = []

    for track in tracks:
        base = TRACK_BASENAME.get(track, track)
        source = directory / f"{base}{RECORDING_SUFFIX}"
        target = directory / f"{base}{FINAL_SUFFIX}"

        if target.is_file() and target.stat().st_size > 0 and not source.exists():
            compressed.append(track)  # already done on an earlier attempt
            continue
        if not source.is_file() or source.stat().st_size == 0:
            continue

        try:
            compress_lossless(source, target)
        except Exception as exc:
            failed.append(f"{track}: {exc}")
            target.unlink(missing_ok=True)
            continue

        # The original is deleted only after the compressed file is shown to
        # decode back to the same samples. Losing a meeting to an unverified
        # conversion is not a recoverable mistake.
        if not verify_lossless(source, target):
            failed.append(f"{track}: o arquivo comprimido nao confere com o original")
            target.unlink(missing_ok=True)
            continue

        source.unlink(missing_ok=True)
        compressed.append(track)

    return compressed, failed


def finalize_session(
    directory: Path,
    *,
    tracks: list[str],
    duration_ms: int,
    ended_at: str,
    submit: Callable[[], None] | None = None,
    compress: bool = True,
) -> Metadata:
    """Run the finalization from wherever it last stopped.

    Safe to call again after any interruption: every step checks whether it has
    already happened before doing anything.
    """
    metadata = read_metadata(directory) or Metadata(uid=directory.name)

    # Step 1 -- the files are closed by the caller before this runs; recording
    # the fact is what makes the rest resumable.
    if not reached(metadata.step, Step.FILES_CLOSED):
        metadata.step = Step.FILES_CLOSED.value
        write_metadata(directory, metadata)

    # Step 2 -- compress, preserving the original on any failure.
    if compress and not reached(metadata.step, Step.COMPRESSED):
        compressed, failed = compress_tracks(directory, tracks)
        if failed:
            metadata.uncompressed = True
            metadata.warnings.extend(failed)
            metadata.warnings.append(
                "o audio permaneceu no formato nao comprimido; a transcricao "
                "prossegue normalmente"
            )
        for track in compressed:
            entry = metadata.tracks.setdefault(track, {})
            entry["comprimida"] = True
        metadata.step = Step.COMPRESSED.value
        write_metadata(directory, metadata)

    # Step 3 -- duration and end instant. This is the durability point.
    if not reached(metadata.step, Step.METADATA_WRITTEN):
        metadata.duration_ms = duration_ms
        metadata.ended_at = ended_at
        metadata.step = Step.METADATA_WRITTEN.value
        write_metadata(directory, metadata)

    # Step 4 -- submit for transcription. Recoverable from the persisted state
    # rather than depending on this process staying alive.
    if not reached(metadata.step, Step.QUEUED):
        if submit is not None:
            submit()
        metadata.step = Step.QUEUED.value
        write_metadata(directory, metadata)

    return metadata


def pending_finalizations(data_dir: Path) -> list[Path]:
    """Meeting directories whose finalization did not reach the end."""
    root = data_dir / "recordings"
    if not root.is_dir():
        return []
    pending: list[Path] = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        metadata = read_metadata(directory)
        if metadata is None:
            continue
        if not reached(metadata.step, Step.QUEUED):
            pending.append(directory)
    return pending


def now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


__all__ = [
    "Metadata",
    "Step",
    "compress_tracks",
    "finalize_session",
    "now_iso",
    "pending_finalizations",
    "reached",
    "read_metadata",
    "record_step",
    "write_metadata",
]
