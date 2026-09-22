"""Bringing an existing audio or video file in as a meeting.

The file the user points at is never touched. A copy goes into the meeting's
own directory, so the directory stays self-contained: move it to another
machine, or lose the database, and it still holds everything about that
meeting.

Imported audio has neither a microphone track nor a system track. Forcing it
into one would claim an attribution the file cannot support, so it gets a track
of its own and its speakers are reported as unknown.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from ..errors import MediaDecodeError, VoxVaultError
from ..layout import IMPORTED_BASENAME, meeting_dir
from ..types import MeetingState, Track
from .finalize import Metadata, Step, now_iso, write_metadata

#: Extensions accepted without probing. Anything else is still attempted --
#: the decoder is the real authority on what it can read.
KNOWN_SUFFIXES = frozenset({
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff",
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv",
})


class ImportError_(VoxVaultError):
    """The file could not be imported. Names the path and the reason."""


def import_media(
    config: Config,
    source: Path,
    *,
    title: str = "",
    store=None,
    submit=None,
) -> tuple[str, Path]:
    """Copy a media file in as a meeting and return (uid, directory).

    Registers the meeting when a store is given, and submits it for
    transcription through ``submit``. Both are optional so that importing can
    be exercised, and recovered, without either.
    """
    source = Path(source).expanduser()
    if not source.exists():
        raise ImportError_(f"Arquivo nao encontrado: {source}")
    if not source.is_file():
        raise ImportError_(f"O caminho nao e um arquivo: {source}")
    if source.stat().st_size == 0:
        raise ImportError_(f"O arquivo esta vazio: {source}")

    # Ask the decoder before copying: a file it cannot read would otherwise be
    # duplicated into the data directory only to fail later.
    from ..engine.media import probe_duration_ms

    duration_ms = probe_duration_ms(source)
    if duration_ms is None:
        raise MediaDecodeError(
            f"O decodificador de midia nao conseguiu ler '{source}'. "
            f"Verifique se o ffmpeg esta instalado e se o arquivo nao esta "
            f"corrompido."
        )
    if duration_ms <= 0:
        raise ImportError_(
            f"O arquivo '{source}' nao contem trilha de audio utilizavel."
        )

    uid = uuid.uuid4().hex
    directory = meeting_dir(config.data_dir, uid)
    directory.mkdir(parents=True, exist_ok=True)

    suffix = source.suffix.lower() or ".bin"
    target = directory / f"{IMPORTED_BASENAME}{suffix}"
    # copy2 rather than move: the file the user pointed at is theirs, and an
    # import that silently relocates it would be a surprise nobody asked for.
    shutil.copy2(source, target)

    started_at = _original_moment(source)
    resolved_title = title or source.stem

    write_metadata(directory, Metadata(
        uid=uid,
        title=resolved_title,
        started_at=started_at.isoformat(),
        ended_at=now_iso(),
        duration_ms=duration_ms,
        step=Step.METADATA_WRITTEN.value,
        tracks={Track.IMPORTED.value: {"duracao_ms": duration_ms}},
        devices={"origem": str(source)},
    ))

    if store is not None:
        from ..store import Origin

        store.create_meeting(
            uid=uid,
            title=resolved_title,
            started_at=started_at,
            directory=directory,
            origin=Origin.IMPORTED,
            state=MeetingState.RECORDED,
            source_path=str(source),
            duration_ms=duration_ms,
        )

    if submit is not None:
        submit(uid)

    write_metadata(directory, Metadata(
        uid=uid,
        title=resolved_title,
        started_at=started_at.isoformat(),
        ended_at=now_iso(),
        duration_ms=duration_ms,
        step=Step.QUEUED.value,
        tracks={Track.IMPORTED.value: {"duracao_ms": duration_ms}},
        devices={"origem": str(source)},
    ))

    return uid, directory


def _original_moment(source: Path) -> datetime:
    """When the recording happened, as far as the file can say.

    The file's modification time beats the import instant: a meeting recorded
    last week should not sort as if it happened today just because it was
    imported today.
    """
    try:
        return datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return datetime.now(timezone.utc)
