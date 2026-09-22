"""Parsing of the endpoint mix format.

Split out of :mod:`voxvault.capture.wasapi` for one reason: parsing a
``WAVEFORMATEX`` blob is a pure function over bytes, so it can be tested
without an audio device, and a wrong answer here is otherwise only visible as
a cryptic ``AUDCLNT_E_UNSUPPORTED_FORMAT`` from ``Initialize``.

On modern Windows the shared-mode mix format is almost always 32-bit float,
delivered as ``WAVE_FORMAT_EXTENSIBLE`` with the IEEE-float subformat. Some
drivers still report plain ``WAVE_FORMAT_IEEE_FLOAT``, and a few report
integer PCM; all three are handled, because falling over on one of them means
a device that simply cannot be recorded.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final

from ..errors import CaptureError

WAVE_FORMAT_PCM: Final = 0x0001
WAVE_FORMAT_IEEE_FLOAT: Final = 0x0003
WAVE_FORMAT_EXTENSIBLE: Final = 0xFFFE

WAVEFORMATEX_SIZE: Final = 18

#: The two KSDATAFORMAT subformats that matter, as raw little-endian GUIDs.
_SUBTYPE_PCM: Final = bytes.fromhex("0100000000001000800000aa00389b71")
_SUBTYPE_IEEE_FLOAT: Final = bytes.fromhex("0300000000001000800000aa00389b71")

SAMPLE_FLOAT32: Final = "float32"
SAMPLE_INT16: Final = "int16"
SAMPLE_INT24: Final = "int24"
SAMPLE_INT32: Final = "int32"


@dataclass(frozen=True, slots=True)
class StreamFormat:
    """What the endpoint actually hands over, per frame."""

    sample_rate: int
    channels: int
    bits_per_sample: int
    valid_bits: int
    block_align: int
    sample_format: str
    channel_mask: int
    extensible: bool

    @property
    def bytes_per_second(self) -> int:
        return self.sample_rate * self.block_align

    def frames_to_ns(self, frames: int) -> int:
        """Exact, in integers: float seconds would drift over a 60-minute meeting."""
        return frames * 1_000_000_000 // self.sample_rate

    def ns_to_frames(self, ns: int) -> int:
        return ns * self.sample_rate // 1_000_000_000

    def describe(self) -> str:
        return (
            f"{self.sample_rate} Hz, {self.channels} canal(is), "
            f"{self.sample_format} ({self.bits_per_sample} bits), "
            f"bloco {self.block_align} B"
        )


def _sample_format(tag: int, subformat: bytes | None, bits: int) -> str:
    if tag == WAVE_FORMAT_IEEE_FLOAT or subformat == _SUBTYPE_IEEE_FLOAT:
        if bits == 32:
            return SAMPLE_FLOAT32
        raise CaptureError(
            f"formato de ponto flutuante com {bits} bits nao e suportado"
        )
    if tag == WAVE_FORMAT_PCM or subformat == _SUBTYPE_PCM:
        if bits == 16:
            return SAMPLE_INT16
        if bits == 24:
            return SAMPLE_INT24
        if bits == 32:
            return SAMPLE_INT32
        raise CaptureError(f"PCM inteiro com {bits} bits nao e suportado")
    if subformat is not None:
        raise CaptureError(
            "subformato de audio desconhecido: " + subformat.hex()
        )
    raise CaptureError(f"tag de formato de audio desconhecida: 0x{tag:04X}")


def parse_wave_format(raw: bytes) -> StreamFormat:
    """Turn a ``WAVEFORMATEX`` / ``WAVEFORMATEXTENSIBLE`` blob into a format.

    ``raw`` must contain at least the 18 bytes of ``WAVEFORMATEX``; when
    ``wFormatTag`` is ``WAVE_FORMAT_EXTENSIBLE`` it must also contain the 22
    extension bytes the header promises via ``cbSize``.
    """
    if len(raw) < WAVEFORMATEX_SIZE:
        raise CaptureError(
            f"formato de audio truncado: {len(raw)} bytes, "
            f"esperados ao menos {WAVEFORMATEX_SIZE}"
        )
    (
        tag,
        channels,
        sample_rate,
        _avg_bytes,
        block_align,
        bits,
        cb_size,
    ) = struct.unpack_from("<HHIIHHH", raw, 0)

    if channels < 1:
        raise CaptureError("formato de audio sem canais")
    if sample_rate < 1:
        raise CaptureError("formato de audio sem taxa de amostragem")

    subformat: bytes | None = None
    valid_bits = bits
    channel_mask = 0
    extensible = tag == WAVE_FORMAT_EXTENSIBLE

    if extensible:
        if cb_size < 22 or len(raw) < WAVEFORMATEX_SIZE + 22:
            raise CaptureError(
                "WAVE_FORMAT_EXTENSIBLE sem os 22 bytes de extensao "
                f"(cbSize={cb_size}, recebidos {len(raw)} bytes)"
            )
        valid_bits, channel_mask = struct.unpack_from("<HI", raw, WAVEFORMATEX_SIZE)
        subformat = raw[WAVEFORMATEX_SIZE + 6 : WAVEFORMATEX_SIZE + 22]
        if valid_bits == 0:
            valid_bits = bits

    sample_format = _sample_format(tag, subformat, bits)

    expected_align = channels * bits // 8
    if block_align != expected_align:
        # Trusting the driver's own nBlockAlign here would misread every
        # buffer; the copy length in the reader loop is derived from it.
        raise CaptureError(
            f"nBlockAlign incoerente: {block_align} B, "
            f"esperado {expected_align} B para {channels}x{bits} bits"
        )

    return StreamFormat(
        sample_rate=sample_rate,
        channels=channels,
        bits_per_sample=bits,
        valid_bits=valid_bits,
        block_align=block_align,
        sample_format=sample_format,
        channel_mask=channel_mask,
        extensible=extensible,
    )


def build_wave_format(
    *,
    sample_rate: int,
    channels: int,
    sample_format: str = SAMPLE_FLOAT32,
    extensible: bool = True,
    channel_mask: int = 0,
) -> bytes:
    """Build a blob in the same layout. Used by tests and by the gate's tone."""
    bits = {
        SAMPLE_FLOAT32: 32,
        SAMPLE_INT16: 16,
        SAMPLE_INT24: 24,
        SAMPLE_INT32: 32,
    }[sample_format]
    block_align = channels * bits // 8
    avg = block_align * sample_rate
    if extensible:
        head = struct.pack(
            "<HHIIHHH",
            WAVE_FORMAT_EXTENSIBLE,
            channels,
            sample_rate,
            avg,
            block_align,
            bits,
            22,
        )
        sub = _SUBTYPE_IEEE_FLOAT if sample_format == SAMPLE_FLOAT32 else _SUBTYPE_PCM
        return head + struct.pack("<HI", bits, channel_mask) + sub
    tag = WAVE_FORMAT_IEEE_FLOAT if sample_format == SAMPLE_FLOAT32 else WAVE_FORMAT_PCM
    return struct.pack(
        "<HHIIHHH", tag, channels, sample_rate, avg, block_align, bits, 0
    )
