"""Turning device position plus acquisition instant into a timeline position.

This is the arithmetic the validation gate measures and the alignment stage
will later build on. It is deliberately pure -- no Windows calls, no device --
so the property that actually matters can be asserted in a unit test:

    a packet's position comes from the **device position**, anchored on the
    **acquisition timestamp**, never from when the packet reached us.

The discriminating quantity is :func:`residual_ns`. If a backend reports real
acquisition instants, the residual between the QPC timestamp and the instant
implied by the device position stays near zero however late the packet was
delivered. If a backend synthesises the timestamp from the delivery clock --
which is what PortAudio's ``inputBufferAdcTime`` does -- the residual tracks
the scheduling delay, and grows exactly when the machine is busy.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..types import CapturePacket

NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True, slots=True)
class Anchor:
    """The reference pair for one track: first valid position and its instant.

    Both tracks are expressed against ``session_qpc_ns``, the instant at which
    both streams were open and armed. That is the session's zero -- not the
    command, and not the first sample, because a loopback endpoint can stay
    idle for minutes after being correctly armed.
    """

    device_position: int
    qpc_ns: int
    sample_rate: int

    def offset_ns(self, device_position: int) -> int:
        """Acquisition instant of ``device_position``, relative to the anchor."""
        return (
            (device_position - self.device_position) * NS_PER_SECOND
        ) // self.sample_rate

    def instant_ns(self, device_position: int) -> int:
        """Absolute acquisition instant, on the same clock as ``qpc_ns``."""
        return self.qpc_ns + self.offset_ns(device_position)

    def position_of(self, instant_ns: int) -> int:
        """Inverse: the device position an acquisition instant corresponds to."""
        return self.device_position + (
            (instant_ns - self.qpc_ns) * self.sample_rate
        ) // NS_PER_SECOND


def residual_ns(anchor: Anchor, packet: CapturePacket) -> int:
    """How far the reported instant departs from the one the frames imply.

    Near zero means the two reported quantities agree, i.e. the timestamp is a
    property of the audio and not of the delivery. This is the number the
    load test watches.
    """
    return packet.qpc_ns - anchor.instant_ns(packet.device_position)


def delivery_lag_ns(packet: CapturePacket, arrival_qpc_ns: int) -> int:
    """How long after acquisition the packet reached the application.

    Expected to be at least one device period, and to jitter badly under load.
    It must *not* leak into ``residual_ns``.
    """
    return arrival_qpc_ns - packet.qpc_ns


@dataclass(frozen=True, slots=True)
class GapDecision:
    """What to do with a packet whose position is not where writing stopped."""

    #: Frames of silence to insert before writing this packet.
    silence_frames: int
    #: Frames to drop from the head of this packet (overlap already written).
    trim_frames: int
    #: Frames of this packet that will actually be written.
    write_frames: int
    #: Set when a gap was real and must be recorded in the metadata.
    gap: bool
    reason: str = ""


def decide_gap(
    *,
    expected_position: int,
    packet_position: int,
    packet_frames: int,
    sample_rate: int,
    threshold_ms: int,
    discontinuity: bool = False,
) -> GapDecision:
    """Compare where writing stopped against where this packet claims to start.

    Three cases, and the middle one is the whole point:

    * the packet starts *after* the written position by more than the
      threshold, or the OS flagged a discontinuity -> a real gap; fill it;
    * it starts after by less than the threshold -> ordinary delivery jitter;
      insert nothing. Filling here would fabricate the very error alignment
      exists to remove;
    * it starts *before* -> overlap; drop the already-written head and write
      only the excess. The write position never moves backwards.
    """
    if packet_frames < 0:
        raise ValueError("packet_frames nao pode ser negativo")
    delta = packet_position - expected_position
    threshold_frames = threshold_ms * sample_rate // 1000

    if delta < 0:
        trim = min(-delta, packet_frames)
        return GapDecision(
            silence_frames=0,
            trim_frames=trim,
            write_frames=packet_frames - trim,
            gap=False,
            reason="sobreposicao" if trim else "",
        )

    if discontinuity:
        # The OS said frames were lost. Believe it even below the threshold:
        # the threshold is about jitter, and this is not jitter.
        return GapDecision(
            silence_frames=delta,
            trim_frames=0,
            write_frames=packet_frames,
            gap=True,
            reason="descontinuidade sinalizada pelo sistema operacional",
        )

    if delta > threshold_frames:
        return GapDecision(
            silence_frames=delta,
            trim_frames=0,
            write_frames=packet_frames,
            gap=True,
            reason="posicao de dispositivo avancou alem dos quadros entregues",
        )

    return GapDecision(
        silence_frames=0,
        trim_frames=0,
        write_frames=packet_frames,
        gap=False,
        reason="jitter abaixo do limiar" if delta else "",
    )


def frames_to_ms(frames: int, sample_rate: int) -> int:
    return frames * 1000 // sample_rate


@dataclass(frozen=True, slots=True)
class Placement:
    """Where one packet lands on its track."""

    silence_frames: int
    trim_frames: int
    write_frames: int
    gap: bool
    reason: str = ""
    #: Position came from the continuous frame count, not from the report.
    derived: bool = False
    #: The anchor was re-established on this packet.
    reanchored: bool = False

    @property
    def total_frames(self) -> int:
        return self.silence_frames + self.write_frames


class TrackPlacer:
    """Places a track's packets on the common session timeline.

    Position zero is the instant *both* streams were armed, which is what the
    caller passes as ``session_qpc_ns``. Everything else follows from the
    anchor, so the four behaviours the specification separates fall out of one
    comparison instead of four special cases:

    * the loopback track that stays idle for the first thirty seconds -- its
      anchor is set on the first packet it does deliver, and the interval back
      to the session start is silence;
    * a packet delivered late but complete -- the device position has not run
      ahead of the frames handed over, so nothing is inserted;
    * a real gap -- the device position has run ahead, or the OS said so;
    * overlapping packets -- only the excess is written, and the write
      position never moves backwards.

    The class holds no audio and calls nothing: it decides frame counts, which
    is why every one of those behaviours is a unit test with no device.
    """

    def __init__(
        self,
        *,
        session_qpc_ns: int,
        sample_rate: int,
        threshold_ms: int = 200,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate deve ser maior que zero")
        self.session_qpc_ns = session_qpc_ns
        self.sample_rate = sample_rate
        self.threshold_ms = threshold_ms
        self.anchor: Anchor | None = None
        #: Device position that corresponds to written position zero.
        self.base_position: int = 0
        self.written_frames: int = 0
        self.gaps: list[tuple[int, int, str]] = []  # (start_frame, frames, reason)
        self.derived_packets: int = 0
        self.reanchors: int = 0
        self.duplicated_frames_dropped: int = 0

    @property
    def written_ms(self) -> int:
        return frames_to_ms(self.written_frames, self.sample_rate)

    def _anchor_on(self, packet: CapturePacket, *, reanchor: bool) -> None:
        self.anchor = Anchor(packet.device_position, packet.qpc_ns, self.sample_rate)
        if reanchor:
            # Re-establishing after a stretch of derived positions: the packet
            # must land exactly where writing stopped, not jump.
            self.base_position = packet.device_position - self.written_frames
            self.reanchors += 1
        else:
            self.base_position = self.anchor.position_of(self.session_qpc_ns)

    def place(self, packet: CapturePacket) -> Placement:
        """Decide what this packet contributes, and advance the write position."""
        if not packet.timestamp_valid:
            # Degrade locally and record it -- never switch to counting frames
            # as the normal mode of operation.
            self.derived_packets += 1
            placement = Placement(
                silence_frames=0,
                trim_frames=0,
                write_frames=packet.frames,
                gap=False,
                reason="posicao derivada da contagem continua de quadros",
                derived=True,
            )
            self.written_frames += packet.frames
            return placement

        reanchor = self.anchor is not None and self.derived_packets > 0
        if self.anchor is None or reanchor:
            first = self.anchor is None
            self._anchor_on(packet, reanchor=not first)
            self.derived_packets = 0
            if not first:
                self.written_frames += packet.frames
                return Placement(
                    silence_frames=0,
                    trim_frames=0,
                    write_frames=packet.frames,
                    gap=False,
                    reason="ancora restabelecida",
                    reanchored=True,
                )

        decision = decide_gap(
            expected_position=self.base_position + self.written_frames,
            packet_position=packet.device_position,
            packet_frames=packet.frames,
            sample_rate=self.sample_rate,
            threshold_ms=self.threshold_ms,
            discontinuity=packet.discontinuity,
        )
        if decision.gap and decision.silence_frames:
            self.gaps.append(
                (self.written_frames, decision.silence_frames, decision.reason)
            )
        if decision.trim_frames:
            self.duplicated_frames_dropped += decision.trim_frames
        self.written_frames += decision.silence_frames + decision.write_frames
        return Placement(
            silence_frames=decision.silence_frames,
            trim_frames=decision.trim_frames,
            write_frames=decision.write_frames,
            gap=decision.gap,
            reason=decision.reason,
        )
