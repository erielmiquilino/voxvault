"""The device position is not always counted in the frames we receive.

This guards a bug that was found on real hardware and would be invisible in
review: a webcam microphone running natively at 16 kHz behind a 48 kHz mix
reports its position advancing 160 per packet while handing over 480 frames.
Taking the two as equal makes the placer read every packet as overlapping its
predecessor and silently discard two thirds of the recording -- measured at
158 364 frames trimmed out of 238 560 before the fix.

Nothing about that looks wrong until someone plays the audio back.
"""

from __future__ import annotations

import itertools

import pytest

from voxvault.capture.format import StreamFormat
from voxvault.capture.stream import CALIBRATION_PACKETS, CaptureStream
from voxvault.types import CapturePacket


def _stream(sample_rate: int = 48_000) -> CaptureStream:
    """A stream object without any device behind it.

    Only the calibration arithmetic is under test, and it neither touches COM
    nor needs a device -- which is what makes this reproducible on a machine
    with no audio hardware at all.
    """
    stream = CaptureStream.__new__(CaptureStream)
    stream.position_scale = 0.0
    stream.position_scale_note = ""
    stream._calibrating = []
    stream._format = StreamFormat(
        sample_rate=sample_rate, channels=2, bits_per_sample=32, valid_bits=32,
        block_align=8, sample_format="float32", channel_mask=3, extensible=True,
    )
    return stream


def _packet(position: int, frames: int = 480) -> CapturePacket:
    return CapturePacket(
        data=b"", frames=frames, device_position=position,
        qpc_ns=position * 20_833, discontinuity=False,
    )


def _feed(stream: CaptureStream, *, step: int, packets: int, frames: int = 480):
    """Deliver packets whose position advances by ``step`` each time."""
    emitted: list[CapturePacket] = []
    for index in range(packets):
        stream._emit(_packet(index * step, frames), None, emitted.append)
    return emitted


# -- the measurement ---------------------------------------------------

def test_a_device_behind_a_faster_mix_is_detected() -> None:
    """The real case: 16 kHz device, 48 kHz mix, position advancing by 160."""
    stream = _stream()
    _feed(stream, step=160, packets=CALIBRATION_PACKETS)

    assert stream.position_scale == 3.0
    assert "16000" in stream.position_scale_note
    assert "48000" in stream.position_scale_note


def test_a_matching_device_is_left_alone() -> None:
    stream = _stream()
    _feed(stream, step=480, packets=CALIBRATION_PACKETS)

    assert stream.position_scale == 1.0
    assert "ja vem em quadros" in stream.position_scale_note


def test_positions_are_rescaled_into_the_delivered_frame_units() -> None:
    """After calibration, position advance must equal frames delivered.

    That equality is the whole contract the placer relies on: it is how a real
    gap is told apart from a packet that merely arrived late.
    """
    stream = _stream()
    emitted = _feed(stream, step=160, packets=CALIBRATION_PACKETS + 8)

    assert len(emitted) == CALIBRATION_PACKETS + 8
    for earlier, later in itertools.pairwise(emitted):
        advance = later.device_position - earlier.device_position
        assert advance == earlier.frames, (
            "a posicao tem de avancar exatamente os quadros entregues"
        )


def test_held_packets_are_released_once_the_scale_is_known() -> None:
    """Nothing may be dropped while the ratio is being measured."""
    stream = _stream()
    emitted: list[CapturePacket] = []

    for index in range(CALIBRATION_PACKETS - 1):
        stream._emit(_packet(index * 160), None, emitted.append)
    assert emitted == [], "os primeiros pacotes ficam retidos ate calibrar"

    stream._emit(_packet((CALIBRATION_PACKETS - 1) * 160), None, emitted.append)
    assert len(emitted) == CALIBRATION_PACKETS, "e entao todos sao liberados"


def test_a_short_recording_still_keeps_its_packets() -> None:
    """A recording shorter than the calibration window must not lose its start."""
    stream = _stream()
    stream._packets = []
    for index in range(4):
        stream._emit(_packet(index * 160), None, lambda p: None)

    stream._flush_calibration()

    assert len(stream._packets) == 4
    assert stream.position_scale, "a escala tem de ser resolvida no encerramento"


def test_a_position_that_never_advances_falls_back_to_one_to_one() -> None:
    stream = _stream()
    emitted: list[CapturePacket] = []
    for _ in range(CALIBRATION_PACKETS):
        stream._emit(_packet(0), None, emitted.append)

    assert stream.position_scale == 1.0
    assert "nao avancou" in stream.position_scale_note
    assert len(emitted) == CALIBRATION_PACKETS


@pytest.mark.parametrize(
    ("mix_rate", "step", "expected"),
    [
        (48_000, 160, 3.0),      # 16 kHz device
        (48_000, 240, 2.0),      # 24 kHz device
        (48_000, 480, 1.0),      # matching
        (44_100, 441, 1.0),      # matching at another rate
        (48_000, 80, 6.0),       # 8 kHz device
    ],
)
def test_common_device_rates_resolve_exactly(mix_rate, step, expected) -> None:
    stream = _stream(mix_rate)
    frames = int(step * expected)
    _feed(stream, step=step, packets=CALIBRATION_PACKETS, frames=frames)
    assert stream.position_scale == pytest.approx(expected)
