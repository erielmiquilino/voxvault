"""Audio capture: direct WASAPI access over plain ctypes.

Nothing in this package imports numpy, soxr or soundfile. The capture threads
run for the whole length of a meeting and every import they pull in is startup
cost the product promised not to pay; resampling and file writing happen in the
worker that consumes the queue, not here.

The public surface is:

``devices``
    endpoint enumeration with persistent identifiers and per-role defaults.
``stream``
    :class:`~voxvault.capture.stream.CaptureStream`, one endpoint open in
    shared mode, yielding :class:`~voxvault.types.CapturePacket`.
``anchor``
    the pure arithmetic that turns device position plus acquisition timestamp
    into a timeline position. No Windows calls, so it is testable anywhere.
``gate``
    the runnable validation gate for tasks 2.0.1 - 2.0.3.
"""

from __future__ import annotations

__all__ = [
    "anchor",
    "devices",
    "stream",
    "wasapi",
]
