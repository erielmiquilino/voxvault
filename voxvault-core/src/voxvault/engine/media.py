"""Media normalization: whatever the decoder can read, in the shape the engine needs.

The decoder is PyAV, which carries its own build of the libav* libraries inside
the wheel, and the lossless codec is libsndfile, through soundfile. Neither is a
program that has to be installed on the machine: whatever the preparation of
the environment put there is all that importing, transcribing and compressing
need.

The input file is never modified. Conversion output goes to a temporary file
that the caller disposes of.

There is a deliberate fast path: VoxVault writes its own recordings as 16 kHz
mono 16-bit WAV, which is already exactly what the engine wants. Decoding those
again would cost an extra full pass over the audio and a temporary copy of
every meeting, for nothing.

Everything here works in blocks. A two-hour video is decoded, resampled and
written a frame at a time, compared a block at a time, and never held whole in
memory -- this runs inside the resident service.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

from ..errors import MediaDecodeError
from .base import UnreadableInputError

#: What the Whisper family expects.
TARGET_RATE = 16_000
TARGET_CHANNELS = 1
TARGET_SUBTYPE = "PCM_16"

#: Frames read at a time when compressing and verifying: a few seconds of a
#: recording, small next to a long meeting and large enough to be cheap.
BLOCK_FRAMES = 1 << 18


def already_normalized(path: Path) -> bool:
    """True when the file is already 16 kHz mono 16-bit PCM."""
    try:
        import soundfile as sf

        info = sf.info(str(path))
    except Exception:
        return False
    return (
        info.samplerate == TARGET_RATE
        and info.channels == TARGET_CHANNELS
        and info.subtype == TARGET_SUBTYPE
    )


def _first_audio_stream(container):
    return next((s for s in container.streams if s.type == "audio"), None)


def probe_duration_ms(path: Path) -> int | None:
    """Duration in milliseconds, or None when it cannot be determined.

    Zero means the file opened but carries no audio: a video without sound,
    for instance. The caller decides what that means for it.
    """
    with contextlib.suppress(Exception):
        import soundfile as sf

        info = sf.info(str(path))
        if info.samplerate:
            return round(info.frames / info.samplerate * 1000)

    try:
        import av

        with av.open(str(path)) as container:
            audio = _first_audio_stream(container)
            if audio is None:
                return 0
            if audio.duration is not None and audio.time_base is not None:
                return round(float(audio.duration * audio.time_base) * 1000)
            if container.duration:
                # Container duration is in AV_TIME_BASE units: microseconds.
                return round(container.duration / 1000)
            # Neither the stream nor the container says: count what decodes.
            samples = sum(frame.samples for frame in container.decode(audio))
            return round(samples / audio.rate * 1000) if audio.rate else None
    except Exception:
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

    handle, temp_name = tempfile.mkstemp(prefix="voxvault-norm-", suffix=".wav")
    os.close(handle)
    target = Path(temp_name)
    try:
        written = _decode_to_wav(source, target)
        if written == 0:
            raise UnreadableInputError(source, "nenhuma amostra de audio foi decodificada")
        yield target
    finally:
        with contextlib.suppress(OSError):
            target.unlink()


def _decode_to_wav(source: Path, target: Path) -> int:
    """Decode the first audio stream to 16 kHz mono 16-bit WAV, frame by frame.

    Returns how many samples were written.
    """
    import av
    import soundfile as sf

    written = 0
    try:
        with av.open(str(source)) as container:
            audio = _first_audio_stream(container)
            if audio is None:
                raise UnreadableInputError(source, "o arquivo nao tem trilha de audio")
            audio.thread_type = "AUTO"
            resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_RATE)
            with sf.SoundFile(
                str(target), "w", samplerate=TARGET_RATE, channels=TARGET_CHANNELS,
                subtype=TARGET_SUBTYPE, format="WAV",
            ) as out:
                for frame in container.decode(audio):
                    for chunk in resampler.resample(frame):
                        samples = chunk.to_ndarray().reshape(-1)
                        out.write(samples)
                        written += samples.size
                # The resampler holds the tail of its filter until told the
                # stream ended; without this every file loses a few ms.
                for chunk in resampler.resample(None):
                    samples = chunk.to_ndarray().reshape(-1)
                    out.write(samples)
                    written += samples.size
    except UnreadableInputError:
        raise
    except Exception as exc:
        raise UnreadableInputError(source, f"falha na decodificacao: {exc}") from None
    return written


def compress_lossless(source: Path, target: Path, *, level: int = 8) -> Path:
    """Compress a finished recording for long-term retention, without loss.

    Lossless is a requirement, not a preference. Audio is kept indefinitely so
    that a meeting can be re-transcribed by a better model later; a lossy codec
    would bake today's quality ceiling into every future transcription of it,
    and the saving is not worth buying that.

    FLAC on 16 kHz mono speech lands near half the size of the raw PCM, which
    is where the roughly 120 MB per recorded hour for the two tracks comes from.
    ``level`` is FLAC's own 0-8 scale; libsndfile takes it as a fraction.
    """
    import soundfile as sf

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sf.SoundFile(str(source)) as origem, sf.SoundFile(
            str(target), "w", samplerate=origem.samplerate,
            channels=origem.channels, subtype="PCM_16", format="FLAC",
            compression_level=max(0, min(level, 8)) / 8,
        ) as destino:
            for block in origem.blocks(blocksize=BLOCK_FRAMES, dtype="int16"):
                destino.write(block)
    except Exception as exc:
        with contextlib.suppress(OSError):
            target.unlink()
        raise MediaDecodeError(f"Falha ao comprimir '{source}': {exc}") from None
    if not target.exists() or target.stat().st_size == 0:
        raise MediaDecodeError(f"Falha ao comprimir '{source}': o arquivo final ficou vazio")
    return target


def verify_lossless(original: Path, compressed: Path) -> bool:
    """Confirm the compressed file decodes back to the original samples.

    The original is deleted only after this passes: losing a meeting to an
    unverified conversion is not a recoverable mistake. Compared a block at a
    time, sample for sample.
    """
    try:
        import numpy as np
        import soundfile as sf

        with sf.SoundFile(str(original)) as a, sf.SoundFile(str(compressed)) as b:
            if (a.samplerate, a.channels, a.frames) != (b.samplerate, b.channels, b.frames):
                return False
            while True:
                block_a = a.read(BLOCK_FRAMES, dtype="int16", always_2d=True)
                block_b = b.read(BLOCK_FRAMES, dtype="int16", always_2d=True)
                if block_a.shape != block_b.shape or not np.array_equal(block_a, block_b):
                    return False
                if block_a.shape[0] == 0:
                    return True
    except Exception:
        return False


def decoder_version() -> str:
    """The media decoder in use, for the diagnostic. Raises when it is missing."""
    import av

    versions = getattr(av, "library_versions", {}) or {}
    avcodec = versions.get("libavcodec")
    suffix = f", libavcodec {'.'.join(map(str, avcodec))}" if avcodec else ""
    return f"PyAV {av.__version__}{suffix}"
