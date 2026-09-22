"""One endpoint open in shared mode, producing :class:`CapturePacket`.

The reader loop does exactly four things per packet: ``GetBuffer``, one
``ctypes.string_at`` copy, ``deque.append``, ``ReleaseBuffer``. No resampling,
no disk, no logging, no lock held across the copy. It runs for the whole
meeting, and anything expensive in it comes out as dropped audio rather than
as a slow function.

A *render* endpoint is opened with ``AUDCLNT_STREAMFLAGS_LOOPBACK``, which
captures the mix the system is playing with no virtual cable and no driver.
Such a stream **delivers nothing while the endpoint is idle**. That is correct
behaviour, not a fault to work around: the silence-fill logic downstream
exists precisely for it, and keeping a silent stream playing to force packets
would be a side effect on the user's audio to solve a problem the timestamp
already solves.
"""

from __future__ import annotations

import ctypes
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Final

from ..errors import CaptureError, DeviceLostError
from ..types import CapturePacket
from . import wasapi
from .format import StreamFormat, parse_wave_format

#: Requested engine buffer. Large enough that a scheduling hiccup under load
#: does not overrun the ring, small enough that stopping is prompt.
DEFAULT_BUFFER_MS: Final = 200

#: Poll period for loopback. ~5 ms keeps latency below one device period while
#: costing a sleep per iteration rather than a spin.
DEFAULT_POLL_MS: Final = 5

#: How long the reader waits on the event before re-checking the stop flag.
_EVENT_WAIT_MS: Final = 200


@dataclass(frozen=True, slots=True)
class StreamStats:
    packets: int
    frames: int
    discontinuities: int
    silent_packets: int
    invalid_timestamps: int
    overruns_detected: int


class CaptureStream:
    """A single WASAPI capture (or loopback) stream, read on its own thread.

    All COM work -- activation included -- happens on the reader thread, so
    the object never has to be marshalled between apartments. :meth:`start`
    blocks until the stream is *armed*, which is the instant the session clock
    is anchored on: not the command, and not the first sample.
    """

    def __init__(
        self,
        endpoint_id: str,
        *,
        loopback: bool,
        name: str = "",
        event_driven: bool | None = None,
        buffer_ms: int = DEFAULT_BUFFER_MS,
        poll_ms: int = DEFAULT_POLL_MS,
        record_arrival: bool = False,
        pro_audio_priority: bool = True,
    ) -> None:
        self.endpoint_id = endpoint_id
        self.loopback = loopback
        self.name = name or ("loopback" if loopback else "entrada")
        # Event-driven for the microphone, polling for loopback. The
        # combination of LOOPBACK and EVENTCALLBACK is unreliable on some
        # drivers -- the event simply never fires -- and a track that silently
        # delivers nothing is worse than one that costs a 5 ms sleep.
        self.event_driven = (not loopback) if event_driven is None else event_driven
        self.buffer_ms = buffer_ms
        self.poll_ms = poll_ms
        self.record_arrival = record_arrival
        self.pro_audio_priority = pro_audio_priority

        self._packets: deque = deque()
        self._stop = threading.Event()
        self._armed = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None

        self._format: StreamFormat | None = None
        self._armed_qpc_ns: int = 0
        self._buffer_frames: int = 0
        self._latency_ns: int = 0
        self._device_period_ns: int = 0
        self._priority_status: str = "nao solicitada"
        self._arming_ms: float = 0.0
        self._initialize_ms: float = 0.0

        self.frames_captured: int = 0
        self.packets_captured: int = 0
        self.discontinuities: int = 0
        self.silent_packets: int = 0
        self.invalid_timestamps: int = 0
        self._frames_read: int = 0

        #: Ratio between one unit of the reported device position and one
        #: frame of the format this stream hands over. See :meth:`_emit`.
        self.position_scale: float = 0.0
        self.position_scale_note: str = "ainda nao calibrada"
        self._calibrating: list = []

        #: Test-only hooks used by the gate to provoke a real capture overrun.
        self.debug_stall_after_s: float = 0.0
        self.debug_stall_s: float = 0.0

        self._event_handle: int = 0
        self._audio_client: wasapi.IAudioClient | None = None
        self._capture_client: wasapi.IAudioCaptureClient | None = None

    # -- properties -------------------------------------------------------

    @property
    def format(self) -> StreamFormat:
        if self._format is None:
            raise CaptureError(f"fluxo '{self.name}' ainda nao foi aberto")
        return self._format

    @property
    def armed_qpc_ns(self) -> int:
        """Instant the stream started, on the same clock packets are stamped with."""
        return self._armed_qpc_ns

    @property
    def latency_ns(self) -> int:
        return self._latency_ns

    @property
    def device_period_ns(self) -> int:
        return self._device_period_ns

    @property
    def buffer_frames(self) -> int:
        return self._buffer_frames

    @property
    def priority_status(self) -> str:
        return self._priority_status

    @property
    def arming_ms(self) -> float:
        """Wall-clock cost of opening and arming, for the metadata.

        The specification caps the delay between the command and the session
        instant, and requires the breach to be *recorded* rather than treated
        as a failure -- so this is measured and reported, never asserted here.
        """
        return self._arming_ms

    @property
    def initialize_ms(self) -> float:
        """Cost of ``IAudioClient::Initialize`` alone.

        Measured separately because it dominates: the first Initialize in a
        process can take seconds on some endpoints while later ones take
        milliseconds, which is the case for pre-warming the audio engine when
        the service starts instead of when the user presses record.
        """
        return self._initialize_ms

    @property
    def pending_frames(self) -> int:
        """Frames captured but not yet drained -- the durability exposure."""
        return self.frames_captured - self._frames_read

    @property
    def error(self) -> BaseException | None:
        return self._error

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stats(self) -> StreamStats:
        return StreamStats(
            packets=self.packets_captured,
            frames=self.frames_captured,
            discontinuities=self.discontinuities,
            silent_packets=self.silent_packets,
            invalid_timestamps=self.invalid_timestamps,
            overruns_detected=self.discontinuities,
        )

    # -- lifecycle --------------------------------------------------------

    # -- device position scale --------------------------------------------

    def _emit(self, packet: CapturePacket, arrival: int | None, append) -> None:
        """Hand a packet over, with its position in this stream's frame units.

        In shared mode the reported device position is counted in the
        **device's** own frames, while the buffer we receive is in the mix
        format the engine resampled it into. Those differ whenever the device
        runs at another rate -- a webcam microphone at 16 kHz behind a 48 kHz
        mix reports its position advancing 160 per packet while handing over
        480 frames.

        Measured here, not assumed: taking the two as equal makes the placer
        read every packet as overlapping its predecessor and discard two
        thirds of the audio, silently. Nothing about that looks wrong until
        the recording is played back.

        The first packets are held until the ratio is known. That costs
        nothing, because position and instant both travel with the packet: a
        packet delivered late still lands where it was acquired.
        """
        if self.position_scale:
            append(self._scaled(packet, arrival))
            return

        self._calibrating.append((packet, arrival))
        if len(self._calibrating) < CALIBRATION_PACKETS:
            return

        first = self._calibrating[0][0]
        advance = packet.device_position - first.device_position
        delivered = sum(p.frames for p, _ in self._calibrating[:-1])
        self._settle_scale(delivered, advance)
        self._release_calibrated(append)

    def _settle_scale(self, delivered: int, advance: int) -> None:
        if advance <= 0 or delivered <= 0:
            self.position_scale = 1.0
            self.position_scale_note = (
                "posicao de dispositivo nao avancou durante a calibracao; "
                "assumida a mesma unidade do formato entregue"
            )
            return

        measured = delivered / advance
        if abs(measured - 1.0) < 0.02:
            self.position_scale = 1.0
            self.position_scale_note = "posicao ja vem em quadros do formato entregue"
            return

        # Snap to the exact ratio between two real sample rates rather than
        # carrying the measurement's rounding error for the whole meeting.
        mix_rate = self.format.sample_rate
        implied = mix_rate / measured
        nearest = min(_STANDARD_RATES, key=lambda r: abs(r - implied))
        if abs(nearest - implied) / implied < 0.05:
            self.position_scale = mix_rate / nearest
            self.position_scale_note = (
                f"dispositivo em {nearest} Hz atras de mixagem em {mix_rate} Hz "
                f"(escala {self.position_scale:.4g})"
            )
        else:
            self.position_scale = measured
            self.position_scale_note = (
                f"escala medida {measured:.4g}, sem taxa padrao correspondente"
            )

    def _scaled(self, packet: CapturePacket, arrival: int | None):
        if self.position_scale != 1.0:
            packet = CapturePacket(
                data=packet.data,
                frames=packet.frames,
                device_position=round(packet.device_position * self.position_scale),
                qpc_ns=packet.qpc_ns,
                discontinuity=packet.discontinuity,
                silent=packet.silent,
                timestamp_valid=packet.timestamp_valid,
            )
        return (packet, arrival) if arrival is not None else packet

    def _release_calibrated(self, append) -> None:
        held, self._calibrating = self._calibrating, []
        for packet, arrival in held:
            append(self._scaled(packet, arrival))

    def _flush_calibration(self) -> None:
        """Release anything still held when the stream stops.

        A recording shorter than the calibration window would otherwise lose
        its first packets entirely.
        """
        if not self._calibrating:
            return
        if not self.position_scale:
            first = self._calibrating[0][0]
            last = self._calibrating[-1][0]
            delivered = sum(p.frames for p, _ in self._calibrating[:-1])
            self._settle_scale(delivered, last.device_position - first.device_position)
        self._release_calibrated(self._packets.append)

    def start(self, timeout_s: float = 120.0) -> int:
        """Open, initialise and start the stream. Returns the arming instant.

        Blocks until the stream is armed so the caller can treat the returned
        QPC instant as the track's reference. Raises whatever the reader
        thread raised while opening.

        ``timeout_s`` is a guard against a wedged driver, not the start-latency
        budget: an endpoint that is merely slow must still open, with the delay
        recorded in :attr:`arming_ms` for the caller to judge against the
        1500 ms ceiling. Failures inside opening are reported immediately and
        do not wait for the timeout. The default is deliberately generous --
        a cold Remote Desktop endpoint was measured taking over 30 s inside
        ``Initialize`` and then working perfectly.
        """
        started_at = time.perf_counter()
        if self._thread is not None:
            raise CaptureError(f"fluxo '{self.name}' ja foi iniciado")
        self._thread = threading.Thread(
            target=self._run, name=f"voxvault-capture-{self.name}", daemon=True
        )
        self._thread.start()
        if not self._armed.wait(timeout_s):
            self._stop.set()
            raise CaptureError(
                f"fluxo '{self.name}' nao ficou armado em {timeout_s:.1f} s"
            )
        self._arming_ms = (time.perf_counter() - started_at) * 1000.0
        if self._error is not None:
            raise self._error
        return self._armed_qpc_ns

    def stop(self, timeout_s: float = 3.0) -> None:
        self._stop.set()
        if self._event_handle:
            wasapi.set_event(self._event_handle)
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout_s)
        # A recording shorter than the calibration window still has its first
        # packets in hand; releasing them here is what keeps them.
        self._flush_calibration()

    def __enter__(self) -> CaptureStream:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- consumption ------------------------------------------------------

    def read(self) -> list[CapturePacket]:
        """Drain every queued packet. Cheap: one ``popleft`` per packet."""
        out: list[CapturePacket] = []
        packets = self._packets
        popleft = packets.popleft
        if self.record_arrival:
            for _ in range(len(packets)):
                out.append(popleft()[0])
        else:
            for _ in range(len(packets)):
                out.append(popleft())
        self._frames_read += sum(p.frames for p in out)
        return out

    def read_with_arrival(self) -> list[tuple[CapturePacket, int]]:
        """Drain packets together with the instant each one reached us.

        Only available when the stream was built with ``record_arrival``; the
        production path pays nothing for it.
        """
        if not self.record_arrival:
            raise CaptureError(
                f"fluxo '{self.name}' nao foi aberto com record_arrival"
            )
        packets = self._packets
        popleft = packets.popleft
        out = [popleft() for _ in range(len(packets))]
        self._frames_read += sum(p.frames for p, _ in out)
        return out

    def materialize(self, packet: CapturePacket) -> bytes:
        """Bytes for a packet, expanding a silent one.

        ``AUDCLNT_BUFFERFLAGS_SILENT`` means the buffer contents are not
        meaningful, so the reader loop stores no data for it. Expanding zeros
        is the consumer's job, off the capture thread.
        """
        if packet.silent:
            return b"\x00" * (packet.frames * self.format.block_align)
        return packet.data

    # -- reader thread ----------------------------------------------------

    def _run(self) -> None:
        priority_handle = 0
        try:
            wasapi.co_initialize()
            if self.pro_audio_priority:
                priority_handle, self._priority_status = wasapi.set_pro_audio_priority()
            self._open()
        except BaseException as exc:  # reported through start()
            self._error = exc
            self._armed.set()
            self._teardown()
            wasapi.revert_pro_audio_priority(priority_handle)
            wasapi.co_uninitialize()
            return

        self._armed.set()
        try:
            self._loop()
        except BaseException as exc:
            self._error = exc
        finally:
            self._teardown()
            wasapi.revert_pro_audio_priority(priority_handle)
            wasapi.co_uninitialize()

    def _open(self) -> None:
        enumerator = wasapi.create_enumerator()
        try:
            device = enumerator.device(self.endpoint_id)
            if device is None:
                raise CaptureError(
                    f"dispositivo nao encontrado: {self.endpoint_id}"
                )
            try:
                client = device.activate_audio_client()
            finally:
                device.release()
        finally:
            enumerator.release()

        self._audio_client = client
        raw_format, format_ptr = client.mix_format()
        try:
            self._format = parse_wave_format(raw_format)

            flags = 0
            if self.loopback:
                flags |= wasapi.AUDCLNT_STREAMFLAGS_LOOPBACK
            if self.event_driven:
                flags |= wasapi.AUDCLNT_STREAMFLAGS_EVENTCALLBACK

            initialize_at = time.perf_counter()
            client.initialize(
                share_mode=wasapi.AUDCLNT_SHAREMODE_SHARED,
                stream_flags=flags,
                buffer_duration_hns=self.buffer_ms * 10_000,
                periodicity_hns=0,
                format_ptr=format_ptr,
            )
            self._initialize_ms = (time.perf_counter() - initialize_at) * 1000.0
        finally:
            wasapi._ole32.CoTaskMemFree(format_ptr)

        if self.event_driven:
            self._event_handle = wasapi.create_event()
            client.set_event_handle(self._event_handle)

        self._capture_client = client.capture_client()
        self._buffer_frames = client.buffer_size()
        latency_hns = client.stream_latency_hns()
        self._latency_ns = latency_hns * 100 if latency_hns >= 0 else -1
        default_period, _minimum = client.device_period_hns()
        self._device_period_ns = default_period * 100

        client.start()
        # Armed. Everything from here on is positioned against this instant.
        self._armed_qpc_ns = wasapi.qpc_now_ns()

    def _teardown(self) -> None:
        if self._audio_client is not None:
            try:
                self._audio_client.stop()
            except Exception:
                pass
        if self._capture_client is not None:
            self._capture_client.release()
            self._capture_client = None
        if self._audio_client is not None:
            self._audio_client.release()
            self._audio_client = None
        if self._event_handle:
            wasapi.close_handle(self._event_handle)
            self._event_handle = 0

    def _loop(self) -> None:
        capture = self._capture_client
        assert capture is not None
        # Hoisted into locals: the loop runs for the whole meeting, and an
        # attribute lookup per packet is cost for nothing.
        this = capture.this
        get_buffer = capture._GetBuffer
        release_buffer = capture._ReleaseBuffer
        next_packet_size = capture._GetNextPacketSize
        append = self._packets.append
        string_at = ctypes.string_at
        byref = ctypes.byref
        qpc_now = wasapi.qpc_now_ns
        record_arrival = self.record_arrival
        stop = self._stop
        event_driven = self.event_driven
        event_handle = self._event_handle
        poll_s = self.poll_ms / 1000.0
        block_align = self.format.block_align
        wait = wasapi.wait_for_event

        SILENT = wasapi.AUDCLNT_BUFFERFLAGS_SILENT
        DISCONTINUITY = wasapi.AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY
        TS_ERROR = wasapi.AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR
        BUFFER_EMPTY = wasapi.AUDCLNT_S_BUFFER_EMPTY

        p_data = ctypes.c_void_p()
        n_frames = ctypes.c_uint32()
        flags = wasapi.DWORD()
        device_position = ctypes.c_uint64()
        qpc_position = ctypes.c_uint64()
        next_size = ctypes.c_uint32()

        stall_at = 0.0
        if self.debug_stall_after_s > 0.0:
            stall_at = time.monotonic() + self.debug_stall_after_s
        monotonic = time.monotonic

        while not stop.is_set():
            if event_driven:
                wait(event_handle, _EVENT_WAIT_MS)
            elif stop.wait(poll_s):
                break

            if stall_at and stall_at <= monotonic():
                # Deliberately stop draining for longer than the engine
                # buffer, so the driver overwrites unread frames and the next
                # GetBuffer carries DATA_DISCONTINUITY. Test-only.
                stall_at = 0.0
                stop.wait(self.debug_stall_s)

            while True:
                hr = next_packet_size(this, byref(next_size))
                if hr < 0:
                    wasapi.check(hr, "IAudioCaptureClient::GetNextPacketSize")
                if next_size.value == 0:
                    break

                hr = get_buffer(
                    this,
                    byref(p_data),
                    byref(n_frames),
                    byref(flags),
                    byref(device_position),  # never NULL -- this is the point
                    byref(qpc_position),  # never NULL -- this is the point
                )
                if (hr & 0xFFFFFFFF) == BUFFER_EMPTY:
                    break
                if hr < 0:
                    wasapi.check(hr, "IAudioCaptureClient::GetBuffer")

                frames = n_frames.value
                raw_flags = flags.value
                if frames:
                    silent = bool(raw_flags & SILENT)
                    data = b"" if silent else string_at(p_data, frames * block_align)
                    qpc = qpc_position.value
                    packet = CapturePacket(
                        data=data,
                        frames=frames,
                        device_position=device_position.value,
                        qpc_ns=qpc * 100,  # WASAPI reports 100-ns units
                        discontinuity=bool(raw_flags & DISCONTINUITY),
                        silent=silent,
                        timestamp_valid=not (raw_flags & TS_ERROR) and qpc != 0,
                    )
                    self._emit(packet, qpc_now() if record_arrival else None, append)
                    self.frames_captured += frames
                    self.packets_captured += 1
                    if packet.discontinuity:
                        self.discontinuities += 1
                    if silent:
                        self.silent_packets += 1
                    if not packet.timestamp_valid:
                        self.invalid_timestamps += 1

                release_buffer(this, frames)
                if not frames:
                    break


#: Packets held back while the position scale is measured. Roughly 120 ms of
#: audio, which costs nothing: the placer anchors on a packet's own timestamp,
#: not on when it was handed over, so delivering the first ones late shifts
#: nothing on the timeline.
CALIBRATION_PACKETS: Final = 12

#: Rates a capture device plausibly runs at natively. Used to snap a measured
#: ratio to an exact one instead of carrying its rounding error forever.
_STANDARD_RATES: Final = (
    8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000, 88200, 96000, 192000,
)


def prewarm(endpoint_id: str, *, loopback: bool = True) -> float:
    """Open and immediately close a stream, returning the cost in milliseconds.

    The first ``IAudioClient::Initialize`` in a process can be orders of
    magnitude slower than every later one -- seconds rather than milliseconds
    on endpoints whose audio path has to be negotiated, such as a Remote
    Desktop endpoint. Paying that once when the resident service starts keeps
    it out of the start-recording budget, which the specification caps at
    1500 ms.

    Failures are swallowed: pre-warming is an optimisation, and the real
    attempt must be the one that reports a problem.
    """
    started = time.perf_counter()
    stream = CaptureStream(
        endpoint_id, loopback=loopback, name="prewarm", pro_audio_priority=False
    )
    try:
        stream.start()
    except Exception:
        return (time.perf_counter() - started) * 1000.0
    finally:
        stream.stop()
    return (time.perf_counter() - started) * 1000.0


def open_pair(
    *,
    mic_endpoint_id: str,
    render_endpoint_id: str,
    record_arrival: bool = False,
    mic_event_driven: bool | None = None,
    loopback_event_driven: bool | None = None,
) -> tuple[CaptureStream, CaptureStream, int]:
    """Arm both tracks and return them with the common session instant.

    The session's zero is when *both* streams are armed, expressed on the QPC
    clock. Defining it by the first sample would be wrong: a loopback stream
    can stay silent for minutes after being correctly opened.
    """
    mic = CaptureStream(
        mic_endpoint_id,
        loopback=False,
        name="mic",
        event_driven=mic_event_driven,
        record_arrival=record_arrival,
    )
    system = CaptureStream(
        render_endpoint_id,
        loopback=True,
        name="loopback",
        event_driven=loopback_event_driven,
        record_arrival=record_arrival,
    )
    mic.start()
    try:
        system.start()
    except BaseException:
        mic.stop()
        raise
    session_qpc_ns = max(mic.armed_qpc_ns, system.armed_qpc_ns)
    return mic, system, session_qpc_ns


__all__ = [
    "CaptureStream",
    "DeviceLostError",
    "StreamStats",
    "open_pair",
]
