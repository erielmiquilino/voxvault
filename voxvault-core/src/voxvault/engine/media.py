"""Media normalization: whatever the decoder can read, in the shape the engine needs.

The input file is never modified. Conversion output goes to a temporary file
that the caller disposes of.

There is a deliberate fast path: VoxVault writes its own recordings as 16 kHz
mono 16-bit WAV, which is already exactly what the engine wants. Sending those
through the external decoder would cost an extra full pass over the audio and
a temporary copy of every meeting, for nothing.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

from ..errors import MediaDecodeError
from .base import UnreadableInputError

#: What the Whisper family expects.
TARGET_RATE = 16_000
TARGET_CHANNELS = 1
TARGET_SUBTYPE = "PCM_16"


def _winget_package_dirs() -> Iterator[Path]:
    """Where winget puts ffmpeg, for the window before PATH takes effect.

    A freshly installed ffmpeg is on the user's PATH but invisible to every
    process started before the install. Telling someone their decoder is
    missing seconds after they installed it is the kind of papercut that makes
    a tool feel broken, so look in the known locations too.
    """
    local = os.environ.get("LOCALAPPDATA")
    roots = [
        Path(local) / "Microsoft" / "WinGet" / "Packages" if local else None,
        Path(r"C:\ProgramData\chocolatey\bin"),
        Path(r"C:\ffmpeg\bin"),
    ]
    for root in roots:
        if root and root.exists():
            yield root


def _search_known_locations(exe_name: str) -> str | None:
    for root in _winget_package_dirs():
        direct = root / exe_name
        if direct.is_file():
            return str(direct)
        # winget nests as Packages/<Publisher.Pkg_hash>/<build>/bin/<exe>
        try:
            for found in root.glob(f"*ffmpeg*/**/bin/{exe_name}"):
                return str(found)
        except OSError:
            continue
    return None


def find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg") or _search_known_locations("ffmpeg.exe")


def find_ffprobe() -> str | None:
    return shutil.which("ffprobe") or _search_known_locations("ffprobe.exe")


def require_ffmpeg() -> str:
    exe = find_ffmpeg()
    if not exe:
        raise MediaDecodeError(
            "O decodificador de midia (ffmpeg) nao foi encontrado no PATH nem "
            "nos locais de instalacao conhecidos. A gravacao continua "
            "disponivel; importacao e transcricao nao."
        )
    return exe


def already_normalized(path: Path) -> bool:
    """True when the file is already 16 kHz mono 16-bit PCM."""
    try:
        import soundfile as sf  # noqa: PLC0415  (lazy: keeps CLI startup cheap)

        info = sf.info(str(path))
    except Exception:
        return False
    return (
        info.samplerate == TARGET_RATE
        and info.channels == TARGET_CHANNELS
        and info.subtype == TARGET_SUBTYPE
    )


def probe_duration_ms(path: Path) -> int | None:
    """Duration in milliseconds, or None when it cannot be determined."""
    with contextlib.suppress(Exception):
        import soundfile as sf  # noqa: PLC0415

        info = sf.info(str(path))
        if info.samplerate:
            return int(round(info.frames / info.samplerate * 1000))

    exe = find_ffprobe()
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stdout.strip()
    if out.returncode != 0 or not text:
        return None
    try:
        return int(round(float(text) * 1000))
    except ValueError:
        return None


@contextlib.contextmanager
def normalized_audio(path: Path) -> Iterator[Path]:
    """Yield a path to the audio in the engine's required format.

    Yields the original path untouched when it is already in that format, so
    the common case costs nothing. Otherwise decodes to a temporary file and
    removes it on exit. The original is never written to.
    """
    source = Path(path)
    if not source.exists():
        raise UnreadableInputError(source, "arquivo nao encontrado")
    if not source.is_file():
        raise UnreadableInputError(source, "o caminho nao e um arquivo")

    if already_normalized(source):
        yield source
        return

    exe = require_ffmpeg()
    handle, temp_name = tempfile.mkstemp(prefix="voxvault-norm-", suffix=".wav")
    os.close(handle)
    target = Path(temp_name)
    try:
        result = subprocess.run(
            [
                exe, "-nostdin", "-hide_banner", "-loglevel", "error",
                "-y", "-i", str(source),
                "-vn",                       # drop video; a video file is fine input
                "-ac", str(TARGET_CHANNELS),
                "-ar", str(TARGET_RATE),
                "-acodec", "pcm_s16le",
                "-map_metadata", "-1",
                str(target),
            ],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
            detail = (result.stderr or "").strip().splitlines()
            raise UnreadableInputError(source, detail[-1] if detail else "falha na decodificacao")
        yield target
    finally:
        with contextlib.suppress(OSError):
            target.unlink()


def compress_lossless(source: Path, target: Path, *, level: int = 8) -> Path:
    """Compress a finished recording for long-term retention, without loss.

    Lossless is a requirement, not a preference. Audio is kept indefinitely so
    that a meeting can be re-transcribed by a better model later; a lossy codec
    would bake today's quality ceiling into every future transcription of it,
    and the saving is not worth buying that.

    FLAC on 16 kHz mono speech lands near half the size of the raw PCM, which
    is where the roughly 120 MB per recorded hour for the two tracks comes from.
    """
    exe = require_ffmpeg()
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            exe, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-y", "-i", str(source),
            "-c:a", "flac", "-compression_level", str(level),
            str(target),
        ],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        detail = (result.stderr or "").strip().splitlines()
        raise MediaDecodeError(
            f"Falha ao comprimir '{source}': "
            f"{detail[-1] if detail else 'erro desconhecido'}"
        )
    return target


def verify_lossless(original: Path, compressed: Path) -> bool:
    """Confirm the compressed file decodes back to the original samples.

    The original is deleted only after this passes: losing a meeting to an
    unverified conversion is not a recoverable mistake.
    """
    try:
        import numpy as np  # noqa: PLC0415
        import soundfile as sf  # noqa: PLC0415

        a, rate_a = sf.read(str(original), dtype="int16", always_2d=True)
        b, rate_b = sf.read(str(compressed), dtype="int16", always_2d=True)
    except Exception:
        return False
    return rate_a == rate_b and a.shape == b.shape and bool(np.array_equal(a, b))
