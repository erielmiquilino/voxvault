"""Media without ffmpeg: PyAV decodes and resamples, soundfile compresses.

Covers the media decisions of the public release: importing reads whatever
the decoder inside the environment reads, a finished recording is kept as FLAC
that decodes back to the very same samples, and none of it needs a program
installed on the machine. Every input here is synthesized with PyAV itself, so
the tests carry no binary fixture and no real recording.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

av = pytest.importorskip("av")
np = pytest.importorskip("numpy")
sf = pytest.importorskip("soundfile")

from voxvault.engine.base import UnreadableInputError
from voxvault.engine.media import (
    BLOCK_FRAMES,
    TARGET_RATE,
    compress_lossless,
    normalized_audio,
    probe_duration_ms,
    verify_lossless,
)

TONE_HZ = 440.0


def _synthesize(
    path: Path, codec: str, *, rate: int, layout: str, seconds: float,
    container: str | None = None, video: bool = False, audio: bool = True,
) -> Path:
    """Encode a tone -- and optionally a few video frames -- with PyAV."""
    channels = 2 if layout == "stereo" else 1
    with av.open(str(path), "w", format=container) as out:
        stream = out.add_stream(codec, rate=rate, layout=layout) if audio else None
        picture = None
        if video:
            picture = out.add_stream("mpeg4", rate=10)
            picture.width, picture.height, picture.pix_fmt = 64, 48, "yuv420p"

        if stream is not None:
            total = int(seconds * rate)
            step = 4096
            for start in range(0, total, step):
                n = min(step, total - start)
                t = np.arange(start, start + n) / rate
                tone = (0.3 * np.sin(2 * np.pi * TONE_HZ * t)).astype(np.float32)
                frame = av.AudioFrame.from_ndarray(
                    np.tile(tone, (channels, 1)), format="fltp", layout=layout
                )
                frame.sample_rate = rate
                frame.pts = start
                for packet in stream.encode(frame):
                    out.mux(packet)
            for packet in stream.encode(None):
                out.mux(packet)

        if picture is not None:
            for index in range(int(seconds * 10)):
                image = np.full((48, 64, 3), index % 255, dtype=np.uint8)
                frame = av.VideoFrame.from_ndarray(image, format="rgb24")
                frame.pts = index
                for packet in picture.encode(frame):
                    out.mux(packet)
            for packet in picture.encode(None):
                out.mux(packet)
    return path


def _dominant_hz(samples, rate: int) -> float:
    spectrum = np.abs(np.fft.rfft(samples.astype(np.float64)))
    return float(np.fft.rfftfreq(samples.size, 1 / rate)[int(np.argmax(spectrum))])


FORMATS = [
    pytest.param("tom.opus", "libopus", 48_000, "stereo", "ogg", False, id="opus"),
    pytest.param("tom.m4a", "aac", 44_100, "stereo", "mp4", False, id="m4a"),
    pytest.param("video.mp4", "aac", 48_000, "mono", "mp4", True, id="mp4-com-video"),
    pytest.param("tom.wav", "pcm_s16le", 44_100, "stereo", "wav", False, id="wav-44k"),
]


@pytest.mark.parametrize(("name", "codec", "rate", "layout", "container", "video"), FORMATS)
def test_import_formats_decode_to_the_engine_shape(
    tmp_path: Path, name, codec, rate, layout, container, video
) -> None:
    source = _synthesize(tmp_path / name, codec, rate=rate, layout=layout,
                         seconds=3.0, container=container, video=video)
    original = source.read_bytes()

    duration = probe_duration_ms(source)
    assert duration is not None and abs(duration - 3000) <= 60, duration

    with normalized_audio(source) as normalized:
        info = sf.info(str(normalized))
        assert (info.samplerate, info.channels, info.subtype) == (TARGET_RATE, 1, "PCM_16")
        assert abs(info.frames - 3 * TARGET_RATE) <= 0.01 * 3 * TARGET_RATE, info.frames
        samples, _ = sf.read(str(normalized), dtype="int16")
        # The resampling kept the pitch: a wrong rate would move the tone.
        assert abs(_dominant_hz(samples, TARGET_RATE) - TONE_HZ) < 3
        assert np.sqrt(np.mean(samples.astype(np.float64) ** 2)) > 1000
    assert not normalized.exists(), "o temporario tem de sumir"
    assert source.read_bytes() == original, "a entrada nunca e alterada"


def test_a_recording_already_in_shape_is_used_as_it_is(tmp_path: Path) -> None:
    wav = tmp_path / "gravacao.wav"
    sf.write(str(wav), np.zeros(TARGET_RATE, dtype="int16"), TARGET_RATE, subtype="PCM_16")

    with normalized_audio(wav) as normalized:
        assert normalized == wav


def test_a_video_without_sound_has_no_audio_to_import(tmp_path: Path) -> None:
    mudo = _synthesize(tmp_path / "mudo.mp4", "aac", rate=48_000, layout="mono",
                       seconds=2.0, container="mp4", video=True, audio=False)

    assert probe_duration_ms(mudo) == 0
    with pytest.raises(UnreadableInputError, match="nao tem trilha de audio"), \
            normalized_audio(mudo):
        pass


def test_something_that_is_not_media_is_unreadable(tmp_path: Path) -> None:
    lixo = tmp_path / "planilha.opus"
    lixo.write_bytes(b"isto nao e audio" * 100)

    assert probe_duration_ms(lixo) is None
    with pytest.raises(UnreadableInputError), normalized_audio(lixo):
        pass


# -- lossless retention ---------------------------------------------------


@pytest.fixture
def gravacao(tmp_path: Path) -> Path:
    """Twenty seconds, so the comparison crosses a block boundary."""
    rng = np.random.default_rng(7)
    frames = BLOCK_FRAMES + 3 * TARGET_RATE
    speechlike = (rng.standard_normal(frames) * 3000).clip(-32768, 32767).astype("int16")
    path = tmp_path / "microfone.wav"
    sf.write(str(path), speechlike, TARGET_RATE, subtype="PCM_16")
    return path


def test_flac_decodes_back_to_the_same_samples(tmp_path: Path, gravacao: Path) -> None:
    flac = compress_lossless(gravacao, tmp_path / "microfone.flac")

    info = sf.info(str(flac))
    assert (info.format, info.subtype) == ("FLAC", "PCM_16")
    assert flac.stat().st_size < gravacao.stat().st_size
    assert verify_lossless(gravacao, flac)


@pytest.mark.parametrize("where", [10, BLOCK_FRAMES + 5], ids=["primeiro-bloco", "segundo-bloco"])
def test_one_altered_sample_fails_the_verification(
    tmp_path: Path, gravacao: Path, where: int
) -> None:
    flac = compress_lossless(gravacao, tmp_path / "microfone.flac")
    samples, rate = sf.read(str(flac), dtype="int16")
    samples[where] = samples[where] + (1 if samples[where] < 32767 else -1)
    adulterado = tmp_path / "adulterado.flac"
    sf.write(str(adulterado), samples, rate, subtype="PCM_16", format="FLAC")

    assert not verify_lossless(gravacao, adulterado)


def test_a_shorter_flac_fails_the_verification(tmp_path: Path, gravacao: Path) -> None:
    samples, rate = sf.read(str(gravacao), dtype="int16")
    curto = tmp_path / "curto.flac"
    sf.write(str(curto), samples[:-1], rate, subtype="PCM_16", format="FLAC")

    assert not verify_lossless(gravacao, curto)


def test_a_failed_compression_leaves_nothing_behind(tmp_path: Path) -> None:
    from voxvault.errors import MediaDecodeError

    nada = tmp_path / "nao-e-wav.wav"
    nada.write_bytes(b"x" * 64)
    alvo = tmp_path / "saida.flac"

    with pytest.raises(MediaDecodeError):
        compress_lossless(nada, alvo)
    assert not alvo.exists()


# -- a long file, in its own process ----------------------------------------

_PEAK = textwrap.dedent("""
    import ctypes, sys, time
    from ctypes import wintypes
    from pathlib import Path

    import av, numpy, soundfile
    from voxvault.engine.media import normalized_audio

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    def peak():
        c = Counters(); c.cb = ctypes.sizeof(c)
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            raise ctypes.WinError()
        return c.PeakWorkingSetSize

    before = peak()
    frames = 0
    started = time.perf_counter()
    if sys.argv[1] != "-":
        with normalized_audio(Path(sys.argv[1])) as out:
            frames = soundfile.info(str(out)).frames
    print(before, peak(), frames, time.perf_counter() - started)
""")


@pytest.mark.slow
@pytest.mark.skipif(sys.platform != "win32", reason="mede o pico pelo psapi do Windows")
def test_thirty_minutes_decode_in_bounded_memory(tmp_path: Path) -> None:
    """Thirty minutes of 48 kHz stereo AAC, the shape of a downloaded video.

    Decoded whole, as float, that would be 690 MB before any resampling. In
    blocks, the decode adds a few tens of megabytes to the process -- measured
    against the same process importing the same libraries and decoding nothing.
    """
    longo = _synthesize(tmp_path / "longo.m4a", "aac", rate=48_000, layout="stereo",
                        seconds=30 * 60, container="mp4")
    script = tmp_path / "pico.py"
    script.write_text(_PEAK, encoding="utf-8")

    def measure(arg: str) -> tuple[int, int, int, float]:
        result = subprocess.run([sys.executable, str(script), arg],
                                capture_output=True, text=True, timeout=900)
        assert result.returncode == 0, result.stderr
        before, peak, frames, seconds = result.stdout.split()
        return int(before), int(peak), int(frames), float(seconds)

    _, idle_peak, _, _ = measure("-")
    _, peak, frames, seconds = measure(str(longo))

    added_mb = (peak - idle_peak) / 2**20
    print(f"\n30 min: pico {peak / 2**20:.0f} MB, {added_mb:.0f} MB pela decodificacao, "
          f"{seconds:.1f} s")
    assert idle_peak > 0 and peak > 0, "o pico de memoria nao foi lido"
    assert abs(frames - 30 * 60 * TARGET_RATE) <= TARGET_RATE
    assert added_mb < 150, f"a decodificacao acrescentou {added_mb:.0f} MB"
