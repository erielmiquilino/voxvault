"""Session lifecycle, with streams faked and the disk real.

No audio hardware exists in this environment, and none is needed: what the
session has to get right is the lifecycle -- arming, pausing, finalizing,
surviving one track that will not open -- and a fake stream provokes each of
those on demand, which a real device would not.
"""

from __future__ import annotations

import struct
import threading
import time
import wave
from pathlib import Path

import pytest

from voxvault.config import Config
from voxvault.errors import CaptureError
from voxvault.session import RecordingSession
from voxvault.session.finalize import Step, read_metadata
from voxvault.types import CapturePacket, Track

SOURCE_RATE = 48_000
PACKET_FRAMES = 480  # 10 ms

from voxvault.capture.format import StreamFormat

FORMAT = StreamFormat(
    sample_rate=SOURCE_RATE, channels=2, bits_per_sample=32, valid_bits=32,
    block_align=8, sample_format="float32", channel_mask=3, extensible=True,
)
NS_PER_FRAME = 1_000_000_000 // SOURCE_RATE


class FakeStream:
    """A capture stream that produces packets on demand.

    ``armed_qpc_ns`` is settable so a test can make one track arm later than
    the other, which is the case the session's instant-zero rule exists for.
    """

    def __init__(
        self,
        *,
        armed_qpc_ns: int = 0,
        fail_to_start: str = "",
        endpoint_id: str = "endpoint",
    ) -> None:
        self.format = FORMAT
        self.endpoint_id = endpoint_id
        self.armed_qpc_ns = armed_qpc_ns
        self.fail_to_start = fail_to_start
        self.started = False
        self.stopped = False
        self._queue: list[CapturePacket] = []
        self._lock = threading.Lock()
        self._next_position = 0

    def start(self) -> int:
        if self.fail_to_start:
            raise CaptureError(self.fail_to_start)
        self.started = True
        return self.armed_qpc_ns

    def stop(self) -> None:
        self.stopped = True

    def read(self) -> list[CapturePacket]:
        with self._lock:
            out, self._queue = self._queue, []
        return out

    def feed(self, packets: int = 1) -> None:
        """Queue contiguous packets, as a healthy device would deliver them."""
        produced = []
        for _ in range(packets):
            position = self._next_position
            payload = struct.pack(
                f"<{PACKET_FRAMES * 2}f", *([0.25] * (PACKET_FRAMES * 2))
            )
            produced.append(CapturePacket(
                data=payload, frames=PACKET_FRAMES, device_position=position,
                qpc_ns=self.armed_qpc_ns + position * NS_PER_FRAME,
                discontinuity=False,
            ))
            self._next_position += PACKET_FRAMES
        with self._lock:
            self._queue.extend(produced)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path, flush_interval_s=0.01)


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_both_tracks_are_written_to_their_own_files(config) -> None:
    mic, system = FakeStream(), FakeStream()
    session = RecordingSession(config, uid="reuniao", mic_stream=mic,
                               system_stream=system)
    session.start()
    try:
        mic.feed(100)      # one second
        system.feed(100)
        assert _wait_for(lambda: session.duration_ms >= 900)
    finally:
        report = session.stop(compress=False)

    assert (report.directory / "mic.wav").is_file()
    assert (report.directory / "system.wav").is_file()
    assert report.duration_ms >= 900


def test_instant_zero_is_when_the_later_stream_armed(config) -> None:
    """Neither track may start before the other could deliver anything."""
    mic = FakeStream(armed_qpc_ns=0)
    system = FakeStream(armed_qpc_ns=500_000_000)  # armed 500 ms later
    session = RecordingSession(config, uid="r", mic_stream=mic, system_stream=system)
    session.start()
    try:
        assert session._session_qpc_ns == 500_000_000
        for writer in session._writers.values():
            assert writer.placer.session_qpc_ns == 500_000_000
    finally:
        session.stop(compress=False)


def test_one_track_failing_to_open_does_not_cancel_the_recording(config) -> None:
    """Half a meeting is far better than none."""
    mic = FakeStream()
    system = FakeStream(fail_to_start="dispositivo de saida indisponivel")
    session = RecordingSession(config, uid="r", mic_stream=mic, system_stream=system)
    session.start()
    try:
        mic.feed(50)
        assert _wait_for(lambda: session.duration_ms > 0)
        assert any("system" in w for w in session.warnings)
    finally:
        report = session.stop(compress=False)

    assert (report.directory / "mic.wav").is_file()
    assert not (report.directory / "system.wav").exists()


def test_no_track_opening_refuses_to_start(config) -> None:
    session = RecordingSession(
        config, uid="r",
        mic_stream=FakeStream(fail_to_start="sem microfone"),
        system_stream=FakeStream(fail_to_start="sem saida"),
    )
    with pytest.raises(CaptureError, match="Nenhuma trilha"):
        session.start()


def test_pause_stops_capture_on_both_tracks(config) -> None:
    mic, system = FakeStream(), FakeStream()
    session = RecordingSession(config, uid="r", mic_stream=mic, system_stream=system)
    session.start()
    try:
        mic.feed(50)
        system.feed(50)
        assert _wait_for(lambda: session.duration_ms >= 450)
        session.pause()
        assert session.paused is True

        before = session.duration_ms
        mic.feed(50)
        system.feed(50)
        time.sleep(0.3)
        assert session.duration_ms == before, "nada pode ser capturado em pausa"
    finally:
        session.stop(compress=False)


def test_resume_fills_the_pause_on_both_tracks_equally(config) -> None:
    """The filler is what keeps the tracks aligned across a pause."""
    mic, system = FakeStream(), FakeStream()
    session = RecordingSession(config, uid="r", mic_stream=mic, system_stream=system)
    session.start()
    try:
        mic.feed(50)
        system.feed(50)
        assert _wait_for(lambda: session.duration_ms >= 450)

        session.pause()
        time.sleep(0.5)
        session.resume()
        assert session.paused is False
    finally:
        report = session.stop(compress=False)

    metadata = read_metadata(report.directory)
    assert len(metadata.pauses) == 1
    assert metadata.pauses[0]["duracao_ms"] >= 400

    lengths = []
    for name in ("mic.wav", "system.wav"):
        with wave.open(str(report.directory / name)) as handle:
            lengths.append(handle.getnframes())
    assert abs(lengths[0] - lengths[1]) <= 32, (
        "as trilhas tem de continuar alinhadas depois da pausa"
    )


def test_stop_writes_metadata_and_reaches_the_queued_step(config) -> None:
    submitted: list[str] = []
    mic, system = FakeStream(), FakeStream()
    session = RecordingSession(config, uid="r", title="Alinhamento",
                               mic_stream=mic, system_stream=system)
    session.start()
    mic.feed(20)
    system.feed(20)
    _wait_for(lambda: session.duration_ms > 0)
    report = session.stop(submit=lambda: submitted.append("ok"), compress=False)

    metadata = read_metadata(report.directory)
    assert metadata.step == Step.QUEUED.value
    assert metadata.title == "Alinhamento"
    assert metadata.duration_ms > 0
    assert metadata.ended_at
    assert submitted == ["ok"], "a sessao tem de ser submetida para transcricao"


def test_stop_is_idempotent(config) -> None:
    submitted: list[str] = []
    mic = FakeStream()
    session = RecordingSession(config, uid="r", mic_stream=mic)
    session.start()
    mic.feed(10)
    _wait_for(lambda: session.duration_ms > 0)

    first = session.stop(submit=lambda: submitted.append("x"), compress=False)
    second = session.stop(submit=lambda: submitted.append("x"), compress=False)

    assert first.uid == second.uid
    assert submitted == ["x"], "parar duas vezes nao pode enfileirar duas vezes"


def test_streams_are_stopped_when_the_session_stops(config) -> None:
    mic, system = FakeStream(), FakeStream()
    session = RecordingSession(config, uid="r", mic_stream=mic, system_stream=system)
    session.start()
    session.stop(compress=False)
    assert mic.stopped and system.stopped


def test_drift_above_the_threshold_becomes_a_warning(config) -> None:
    config.drift_warn_ms = 50
    mic, system = FakeStream(), FakeStream()
    session = RecordingSession(config, uid="r", mic_stream=mic, system_stream=system)
    session.start()
    try:
        mic.feed(100)       # one track gets a second of audio
        system.feed(1)      # the other barely anything
        assert _wait_for(lambda: session.drift_ms > 50)
        assert any("divergiram" in w for w in session.warnings)
    finally:
        session.stop(compress=False)


def test_default_title_names_the_moment(config) -> None:
    session = RecordingSession(config, mic_stream=FakeStream())
    assert "Reuniao de" in session.title
