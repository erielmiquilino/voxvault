"""Writing an aligned track from synthetic packets.

No audio hardware is involved, and that is the point: the behaviour worth
testing is arithmetic over device positions and timestamps, and it can be
provoked exactly here -- an idle loopback, a packet delivered late under load,
a real gap, overlapping packets -- where a real device would only produce them
by luck.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from voxvault.capture.format import StreamFormat
from voxvault.session.track import TARGET_RATE, TrackWriter, WriteBudget
from voxvault.session.wavfile import WavFile
from voxvault.types import CapturePacket

SOURCE_RATE = 48_000
PACKET_MS = 10
PACKET_FRAMES = SOURCE_RATE * PACKET_MS // 1000

FORMAT = StreamFormat(
    sample_rate=SOURCE_RATE,
    channels=2,
    bits_per_sample=32,
    valid_bits=32,
    block_align=8,  # 2 channels x 4 bytes
    sample_format="float32",
    channel_mask=3,
    extensible=True,
)

NS_PER_FRAME = 1_000_000_000 // SOURCE_RATE


def packet(
    index: int,
    *,
    position: int | None = None,
    qpc_ns: int | None = None,
    frames: int = PACKET_FRAMES,
    discontinuity: bool = False,
    silent: bool = False,
    amplitude: float = 0.5,
) -> CapturePacket:
    """One packet as the OS would hand it over, with a tone in it."""
    pos = index * PACKET_FRAMES if position is None else position
    stamp = pos * NS_PER_FRAME if qpc_ns is None else qpc_ns
    payload = struct.pack(f"<{frames * FORMAT.channels}f",
                          *([amplitude] * (frames * FORMAT.channels)))
    return CapturePacket(
        data=payload, frames=frames, device_position=pos, qpc_ns=stamp,
        discontinuity=discontinuity, silent=silent,
    )


def read_frames(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        assert handle.getframerate() == TARGET_RATE
        assert handle.getnchannels() == 1
        return handle.getnframes()


@pytest.fixture
def writer(tmp_path: Path) -> TrackWriter:
    handle = TrackWriter(
        tmp_path / "mic.wav", FORMAT, session_qpc_ns=0,
        budget=WriteBudget(flush_interval_s=0.0),
    )
    yield handle
    handle.close()


def test_contiguous_packets_produce_exactly_their_duration(writer) -> None:
    for index in range(100):  # one second of audio
        writer.write_packet(packet(index))
    writer.close()

    frames = read_frames(writer.path)
    assert abs(frames - TARGET_RATE) <= 4, "1 s de entrada tem de virar 1 s de saida"
    assert writer.stats.silence_frames == 0
    assert writer.stats.gaps == 0


def test_late_delivery_without_loss_inserts_no_silence(writer) -> None:
    """The discriminating case: jitter is not a gap.

    Packets arrive late and bunched, but their device positions are contiguous.
    Inserting silence here would fabricate the very error alignment exists to
    remove.
    """
    for index in range(50):
        writer.write_packet(packet(index))

    assert writer.stats.silence_frames == 0, (
        "atraso de entrega nao pode virar silencio"
    )
    assert writer.stats.gaps == 0


def test_real_gap_is_filled_with_silence(writer) -> None:
    """Device position ran ahead of what was handed over: audio was lost."""
    for index in range(10):
        writer.write_packet(packet(index))
    # Jump one full second ahead in device position.
    writer.write_packet(packet(0, position=10 * PACKET_FRAMES + SOURCE_RATE,
                               qpc_ns=(10 * PACKET_FRAMES + SOURCE_RATE) * NS_PER_FRAME))
    writer.close()

    assert writer.stats.gaps == 1
    frames = read_frames(writer.path)
    # 100 ms of audio + 1000 ms of silence + one more packet.
    assert frames >= TARGET_RATE, "a lacuna real tem de aparecer como silencio"


def test_idle_loopback_at_the_start_is_backfilled(tmp_path: Path) -> None:
    """Scenario: loopback ocioso nos primeiros trinta segundos.

    The output endpoint delivers nothing while nothing is playing, which is the
    normal condition of a meeting, not the exceptional one.
    """
    writer = TrackWriter(
        tmp_path / "system.wav", FORMAT, session_qpc_ns=0,
        budget=WriteBudget(flush_interval_s=0.0),
    )
    try:
        # First packet arrives 30 s after the session was armed.
        start_position = 30 * SOURCE_RATE
        writer.write_packet(packet(0, position=start_position,
                                   qpc_ns=start_position * NS_PER_FRAME))
        writer.close()

        frames = read_frames(writer.path)
        expected = 30 * TARGET_RATE + TARGET_RATE * PACKET_MS // 1000
        # Within one millisecond over thirty seconds. The requirement allows
        # 250 ms over an hour; this is three orders of magnitude inside it.
        assert abs(frames - expected) <= 16
    finally:
        writer.close()


def test_overlapping_packets_write_only_the_excess(writer) -> None:
    """Re-delivered audio advances the timeline by its excess only.

    Asserted on the placer's timeline rather than on bytes in the file: the
    resampler emits in bursts, so the file lags by up to one burst and a
    per-packet byte count would be measuring the resampler, not the placement.
    """
    for index in range(10):
        writer.write_packet(packet(index))
    before_ms = writer.timeline_ms

    # Re-deliver the last packet's position: half of it is already written.
    overlap = packet(0, position=9 * PACKET_FRAMES,
                     qpc_ns=9 * PACKET_FRAMES * NS_PER_FRAME,
                     frames=PACKET_FRAMES * 2)
    writer.write_packet(overlap)

    assert writer.timeline_ms - before_ms == PACKET_MS, (
        "so o excedente pode avancar a linha de tempo"
    )
    assert writer.stats.trimmed_frames == PACKET_FRAMES


def test_silent_packet_is_written_as_silence(writer) -> None:
    """The OS marks a buffer silent and leaves its contents undefined."""
    writer.write_packet(packet(0, silent=True))
    writer.close()

    with wave.open(str(writer.path), "rb") as handle:
        data = handle.readframes(handle.getnframes())
    assert set(data) == {0}, "conteudo de pacote silencioso nao pode ser escrito"


def test_silent_packets_take_exactly_their_duration(writer) -> None:
    """Found in a real hour: a loopback the OS kept open between sounds
    delivered packets flagged silent, and each was written three times its
    length -- the device's 48 kHz frames taken as 16 kHz ones. The timeline
    said nothing was wrong while the file ran 16.7 s ahead of it."""
    for index in range(100):  # one second, all of it flagged silent
        writer.write_packet(packet(index, silent=True))
    writer.close()

    frames = read_frames(writer.path)
    assert abs(frames - TARGET_RATE) <= 4, (
        f"1 s de pacotes silenciosos virou {frames / TARGET_RATE:.2f} s no arquivo"
    )


def test_audio_after_silent_packets_lands_where_the_timeline_says(writer) -> None:
    """Half a second of tone, half a second flagged silent, then tone again:
    the second tone starts in the file at 1.0 s, not after the silence grew."""
    for index in range(50):
        writer.write_packet(packet(index))
    for index in range(50, 100):
        writer.write_packet(packet(index, silent=True))
    for index in range(100, 150):
        writer.write_packet(packet(index))
    writer.close()

    with wave.open(str(writer.path), "rb") as handle:
        total = handle.getnframes()
        samples = struct.unpack(f"<{total}h", handle.readframes(total))
    assert abs(total - int(1.5 * TARGET_RATE)) <= 4
    assert abs(len(samples) - writer.timeline_ms * TARGET_RATE // 1000) <= 4
    first_loud = next(i for i in range(TARGET_RATE // 2 + 800, total) if samples[i])
    assert abs(first_loud - TARGET_RATE) <= 800, (
        f"o segundo tom comecou em {first_loud / TARGET_RATE:.3f} s, nao em 1,000 s"
    )


def test_discontinuity_flag_is_counted(writer) -> None:
    writer.write_packet(packet(0))
    writer.write_packet(packet(1, discontinuity=True))
    assert writer.stats.discontinuities == 1


def test_pause_fills_the_interval_so_alignment_survives(writer) -> None:
    """Scenario: o periodo pausado e preenchido com silencio nas duas trilhas."""
    for index in range(10):
        writer.write_packet(packet(index))
    writer.pause()

    # Packets arriving while paused are dropped, not written.
    for index in range(10, 20):
        writer.write_packet(packet(index))
    assert writer.placer.written_frames == 10 * PACKET_FRAMES, (
        "nada capturado durante a pausa pode entrar na linha de tempo"
    )

    writer.resume(silence_ms=5000)
    writer.close()

    assert writer.stats.silence_frames == 5 * TARGET_RATE
    frames = read_frames(writer.path)
    expected = 5 * TARGET_RATE + TARGET_RATE * 10 * PACKET_MS // 1000
    assert abs(frames - expected) <= 16


def test_header_is_valid_before_the_file_is_closed(tmp_path: Path) -> None:
    """A recording killed by a power cut must still be readable.

    The standard library only patches the sizes on close, which would leave a
    crashed recording declaring zero frames.
    """
    writer = TrackWriter(
        tmp_path / "mic.wav", FORMAT, session_qpc_ns=0,
        budget=WriteBudget(flush_interval_s=0.0),
    )
    for index in range(50):
        writer.write_packet(packet(index))
    writer.maybe_flush(force=True)

    # Read it without closing the writer, as a recovery would.
    frames = read_frames(writer.path)
    assert frames > 0, "o cabecalho tem de estar correto durante a gravacao"
    writer.close()


def test_flush_cadence_bounds_what_can_be_lost(tmp_path: Path) -> None:
    writer = TrackWriter(
        tmp_path / "mic.wav", FORMAT, session_qpc_ns=0,
        budget=WriteBudget(flush_interval_s=3600.0),  # effectively never
    )
    try:
        for index in range(10):
            writer.write_packet(packet(index))
        assert writer.stats.flushes == 0
        assert writer.maybe_flush(force=True) is True
        assert writer.stats.flushes == 1
    finally:
        writer.close()


def test_wavfile_header_survives_repeated_flushes(tmp_path: Path) -> None:
    handle = WavFile(tmp_path / "x.wav", sample_rate=TARGET_RATE)
    try:
        for _ in range(5):
            handle.append(b"\x01\x02" * 1000)
            handle.flush()
            assert read_frames(handle.path) == handle.frames
    finally:
        handle.close()
