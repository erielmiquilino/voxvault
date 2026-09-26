"""A track that changes device mid-meeting keeps recording, and the recording
still ends cleanly afterwards.

Reproduces the evening of 25/09: a Bluetooth headset in a Teams call dropped,
Bluetooth was restarted and the headset came back. The microphone went from
the headset's hands-free endpoint (16 kHz mono) to another device, and from
that instant on nothing more reached its file; ending the recording then left
it open in the library.
"""

from __future__ import annotations

import struct
import threading
import time
from pathlib import Path

import pytest

from voxvault.capture.format import StreamFormat
from voxvault.config import Config
from voxvault.session import RecordingSession
from voxvault.session.finalize import Step, read_metadata
from voxvault.types import CapturePacket

HANDS_FREE = StreamFormat(
    sample_rate=16_000, channels=1, bits_per_sample=32, valid_bits=32,
    block_align=4, sample_format="float32", channel_mask=4, extensible=True,
)
STEREO_48K = StreamFormat(
    sample_rate=48_000, channels=2, bits_per_sample=32, valid_bits=32,
    block_align=8, sample_format="float32", channel_mask=3, extensible=True,
)


class FormatStream:
    """A fake capture stream in a given format, fed 10 ms packets on demand."""

    def __init__(self, fmt: StreamFormat, *, armed_qpc_ns: int = 0) -> None:
        self.format = fmt
        self.endpoint_id = f"endpoint-{fmt.sample_rate}-{fmt.channels}"
        self.armed_qpc_ns = armed_qpc_ns
        self.running = True
        self.error = None
        self.packets_captured = 0
        self._queue: list[CapturePacket] = []
        self._lock = threading.Lock()
        self._position = 0

    def start(self) -> int:
        return self.armed_qpc_ns

    def stop(self) -> None:
        self.running = False

    def read(self) -> list[CapturePacket]:
        with self._lock:
            out, self._queue = self._queue, []
        return out

    def _packet(self, frames: int, data: bytes) -> CapturePacket:
        ns_per_frame = 1_000_000_000 // self.format.sample_rate
        packet = CapturePacket(
            data=data, frames=frames, device_position=self._position,
            qpc_ns=self.armed_qpc_ns + self._position * ns_per_frame,
            discontinuity=False,
        )
        self._position += frames
        return packet

    def feed(self, packets: int = 1) -> None:
        frames = self.format.sample_rate // 100
        samples = frames * self.format.channels
        produced = [
            self._packet(frames, struct.pack(f"<{samples}f", *([0.25] * samples)))
            for _ in range(packets)
        ]
        with self._lock:
            self._queue.extend(produced)
            self.packets_captured += packets

    def feed_malformed(self) -> None:
        """A packet whose data is shorter than its frame count says."""
        frames = self.format.sample_rate // 100
        with self._lock:
            self._queue.append(self._packet(frames, b"\x00" * 7))
            self.packets_captured += 1


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path, flush_interval_s=0.01)


def test_a_device_swap_writes_the_old_device_in_its_format_then_the_new(config) -> None:
    """Scenario: Troca de dispositivo com formatos diferentes."""
    old_mic = FormatStream(HANDS_FREE)
    system = FormatStream(STEREO_48K)
    session = RecordingSession(config, uid="troca", mic_stream=old_mic,
                               system_stream=system)
    session.start()
    try:
        old_mic.feed(50)
        system.feed(50)
        assert _wait_for(lambda: session.writer_for("mic").timeline_ms >= 490)

        # The device drops with its last 200 ms still unread, and the
        # supervisor hands the track a new device in another format.
        old_mic.feed(20)
        new_mic = FormatStream(
            STEREO_48K,
            armed_qpc_ns=session._session_qpc_ns + 1_000 * 1_000_000,
        )
        session.replace_stream("mic", new_mic)
        new_mic.feed(100)
        system.feed(100)

        writer = session.writer_for("mic")
        # 700 ms of the old device, 300 ms without any, 1 s of the new one.
        assert _wait_for(lambda: writer.timeline_ms >= 1_990), (
            f"a trilha parou em {writer.timeline_ms} ms depois da troca: "
            f"{session._warnings}"
        )
        assert writer.stats.unwritten_blocks == 0, (
            "os pacotes do dispositivo anterior foram lidos no formato errado"
        )
        silence_ms = writer.stats.silence_frames * 1000 / 16_000
        assert silence_ms < 400, (
            f"{silence_ms:.0f} ms de silencio: os ultimos 200 ms do dispositivo "
            f"anterior nao foram escritos"
        )
        assert writer.source_format is STEREO_48K
        assert not old_mic.running, "o dispositivo anterior foi fechado"
    finally:
        report = session.stop(compress=False)

    metadata = read_metadata(report.directory)
    assert metadata.step != Step.NOT_STARTED.value


def test_a_packet_that_cannot_be_converted_is_counted_and_the_track_goes_on(config) -> None:
    """Scenario: Bloco que nao pode ser convertido."""
    mic = FormatStream(HANDS_FREE)
    system = FormatStream(STEREO_48K)
    session = RecordingSession(config, uid="malformado", mic_stream=mic,
                               system_stream=system)
    session.start()
    try:
        mic.feed(20)
        mic.feed_malformed()
        mic.feed(80)
        system.feed(101)
        writer = session.writer_for("mic")
        assert _wait_for(lambda: writer.timeline_ms >= 1_000), (
            f"a trilha parou em {writer.timeline_ms} ms: {session._warnings}"
        )
    finally:
        report = session.stop(compress=False)

    stats = read_metadata(report.directory).tracks["mic"]
    assert stats["blocos_nao_escritos"] == 1
    assert stats["nao_escrito_ms"] == pytest.approx(10.0)
    assert stats["blocos_nao_escritos_em"][0]["instante_ms"] >= 190


def test_a_failing_step_of_the_stop_still_finalizes(config, monkeypatch) -> None:
    mic = FormatStream(HANDS_FREE)
    session = RecordingSession(config, uid="falha", mic_stream=mic)
    session.start()
    mic.feed(50)
    assert _wait_for(lambda: session.writer_for("mic").timeline_ms >= 490)

    def breaks() -> None:
        raise RuntimeError("quebrou no meio")

    monkeypatch.setattr(session, "_pad_tracks_to_equal_length", breaks)
    report = session.stop(compress=False)

    metadata = read_metadata(report.directory)
    assert metadata.step != Step.NOT_STARTED.value, "a gravacao ficou sem finalizar"
    assert any("quebrou no meio" in w for w in metadata.warnings)
    assert (report.directory / "mic.wav").is_file()


def test_every_warning_reaches_the_service_as_it_happens(config) -> None:
    mic = FormatStream(HANDS_FREE)
    session = RecordingSession(config, uid="avisos", mic_stream=mic)
    heard: list[str] = []
    session.on_warning = heard.append
    session.start()
    try:
        mic.feed(10)
        mic.feed_malformed()
        assert _wait_for(lambda: any("bloco nao escrito" in w for w in heard))
    finally:
        session.stop(compress=False)
