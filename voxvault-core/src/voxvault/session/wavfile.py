"""An appendable WAV file whose header is correct at every flush.

The standard library's writer only patches the RIFF and data sizes when the
file is closed. A recording killed by a power cut would then be left declaring
zero frames -- readable by forgiving tools, rejected by strict ones, and
exactly the file the "recover an interrupted session" requirement needs to be
able to open.

So the header is rewritten on every durability flush. The cost is two seeks
and eight bytes every couple of seconds, which buys a file that is valid at
all times rather than only after a clean shutdown.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

HEADER_SIZE = 44
_RIFF_SIZE_OFFSET = 4
_DATA_SIZE_OFFSET = 40


class WavFile:
    """16-bit PCM WAV, opened for append-as-you-go writing."""

    def __init__(self, path: Path, *, sample_rate: int, channels: int = 1) -> None:
        self.path = Path(path)
        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_width = 2
        self.data_bytes = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "wb")
        self._file.write(self._header(0))
        self._file.flush()

    def _header(self, data_bytes: int) -> bytes:
        block_align = self.channels * self.sample_width
        byte_rate = self.sample_rate * block_align
        return struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + data_bytes, b"WAVE",
            b"fmt ", 16, 1, self.channels,
            self.sample_rate, byte_rate, block_align,
            self.sample_width * 8,
            b"data", data_bytes,
        )

    @property
    def frames(self) -> int:
        return self.data_bytes // (self.channels * self.sample_width)

    @property
    def closed(self) -> bool:
        return self._file.closed

    def append(self, payload: bytes) -> None:
        self._file.write(payload)
        self.data_bytes += len(payload)

    def flush(self, *, fsync: bool = True) -> None:
        """Make what has been written durable, and leave a valid header."""
        position = self._file.tell()
        self._file.seek(_RIFF_SIZE_OFFSET)
        self._file.write(struct.pack("<I", 36 + self.data_bytes))
        self._file.seek(_DATA_SIZE_OFFSET)
        self._file.write(struct.pack("<I", self.data_bytes))
        self._file.seek(position)
        self._file.flush()
        if fsync:
            os.fsync(self._file.fileno())

    def close(self) -> None:
        if self._file.closed:
            return
        try:
            self.flush()
        finally:
            self._file.close()

    def __enter__(self) -> WavFile:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
