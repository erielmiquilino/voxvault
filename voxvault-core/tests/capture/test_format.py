"""Mix-format parsing, with no device involved.

A wrong answer here is invisible until ``IAudioClient::Initialize`` rejects
the format with an unhelpful code, or -- worse -- succeeds and every buffer is
read with the wrong stride.
"""

from __future__ import annotations

import ctypes
import struct

import pytest

from voxvault.capture.format import (
    SAMPLE_FLOAT32,
    SAMPLE_INT16,
    SAMPLE_INT24,
    WAVEFORMATEX_SIZE,
    build_wave_format,
    parse_wave_format,
)
from voxvault.errors import CaptureError


def test_waveformatex_is_packed_to_eighteen_bytes():
    """``mmreg.h`` packs these structures; default alignment would pad them.

    18/40 are the sizes Windows uses. Padding to 20/44 shifts every field
    after the sample rate.
    """
    from voxvault.capture.wasapi import WAVEFORMATEX, WAVEFORMATEXTENSIBLE

    assert ctypes.sizeof(WAVEFORMATEX) == 18
    assert ctypes.sizeof(WAVEFORMATEXTENSIBLE) == 40


def test_parses_extensible_float32_the_usual_modern_mix_format():
    raw = build_wave_format(
        sample_rate=48_000, channels=2, sample_format=SAMPLE_FLOAT32, channel_mask=3
    )
    fmt = parse_wave_format(raw)
    assert fmt.sample_rate == 48_000
    assert fmt.channels == 2
    assert fmt.bits_per_sample == 32
    assert fmt.sample_format == SAMPLE_FLOAT32
    assert fmt.block_align == 8
    assert fmt.channel_mask == 3
    assert fmt.extensible is True


def test_parses_plain_ieee_float_without_the_extension():
    raw = build_wave_format(
        sample_rate=44_100, channels=1, sample_format=SAMPLE_FLOAT32, extensible=False
    )
    assert len(raw) == WAVEFORMATEX_SIZE
    fmt = parse_wave_format(raw)
    assert fmt.sample_format == SAMPLE_FLOAT32
    assert fmt.channels == 1
    assert fmt.block_align == 4
    assert fmt.extensible is False


@pytest.mark.parametrize(
    "sample_format,bits",
    [(SAMPLE_INT16, 16), (SAMPLE_INT24, 24)],
)
def test_parses_integer_pcm(sample_format, bits):
    raw = build_wave_format(
        sample_rate=16_000, channels=2, sample_format=sample_format
    )
    fmt = parse_wave_format(raw)
    assert fmt.sample_format == sample_format
    assert fmt.bits_per_sample == bits
    assert fmt.block_align == 2 * bits // 8


def test_valid_bits_of_zero_falls_back_to_the_container_size():
    raw = bytearray(
        build_wave_format(sample_rate=48_000, channels=2, sample_format=SAMPLE_INT16)
    )
    struct.pack_into("<H", raw, WAVEFORMATEX_SIZE, 0)
    assert parse_wave_format(bytes(raw)).valid_bits == 16


def test_rejects_a_truncated_header():
    with pytest.raises(CaptureError, match="truncado"):
        parse_wave_format(b"\x00" * 10)


def test_rejects_extensible_without_its_extension():
    head = struct.pack("<HHIIHHH", 0xFFFE, 2, 48_000, 384_000, 8, 32, 22)
    with pytest.raises(CaptureError, match="extensao"):
        parse_wave_format(head)


def test_rejects_an_inconsistent_block_align():
    """The copy length in the reader loop comes from this field."""
    raw = bytearray(
        build_wave_format(sample_rate=48_000, channels=2, sample_format=SAMPLE_FLOAT32)
    )
    struct.pack_into("<H", raw, 12, 6)  # nBlockAlign, should be 8
    with pytest.raises(CaptureError, match="nBlockAlign"):
        parse_wave_format(bytes(raw))


def test_rejects_an_unknown_subformat():
    raw = bytearray(
        build_wave_format(sample_rate=48_000, channels=2, sample_format=SAMPLE_FLOAT32)
    )
    raw[WAVEFORMATEX_SIZE + 6] = 0x7F
    with pytest.raises(CaptureError, match="subformato"):
        parse_wave_format(bytes(raw))


def test_frame_and_nanosecond_conversion_is_exact():
    fmt = parse_wave_format(
        build_wave_format(sample_rate=48_000, channels=2, sample_format=SAMPLE_FLOAT32)
    )
    assert fmt.frames_to_ns(48_000) == 1_000_000_000
    assert fmt.ns_to_frames(1_000_000_000) == 48_000
    assert fmt.bytes_per_second == 48_000 * 8


def test_conversion_does_not_drift_over_a_long_meeting():
    """Integer arithmetic, because float seconds accumulate error over an hour."""
    fmt = parse_wave_format(
        build_wave_format(sample_rate=44_100, channels=2, sample_format=SAMPLE_FLOAT32)
    )
    one_hour_frames = 44_100 * 3600
    assert fmt.frames_to_ns(one_hour_frames) == 3600 * 1_000_000_000
    assert fmt.ns_to_frames(fmt.frames_to_ns(one_hour_frames)) == one_hour_frames
