"""Writing one captured track to disk, aligned on the session timeline.

Runs on its own thread, downstream of the capture reader. Everything that costs
real time -- converting samples, resampling, touching the disk -- happens here
and never in the reader, because a reader that stalls loses audio the operating
system has already thrown away.

Durability is expressed as a cadence, not a wish: the file is forced to disk
every :data:`flush_interval_s` seconds, so what can be lost to a power cut is
exactly what has accumulated since the last completed flush.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..capture.anchor import TrackPlacer
from ..capture.format import StreamFormat
from ..types import CapturePacket
from .wavfile import WavFile

#: What the engine wants, and what we store: 16 kHz mono 16-bit PCM.
TARGET_RATE = 16_000
TARGET_CHANNELS = 1
TARGET_SAMPLE_WIDTH = 2


@dataclass(slots=True)
class TrackStats:
    frames_written: int = 0
    silence_frames: int = 0
    trimmed_frames: int = 0
    gaps: int = 0
    derived_packets: int = 0
    reanchors: int = 0
    discontinuities: int = 0
    flushes: int = 0
    last_flush_monotonic: float = 0.0
    write_error: str = ""

    @property
    def duration_ms(self) -> int:
        return int(self.frames_written * 1000 / TARGET_RATE)


@dataclass(slots=True)
class WriteBudget:
    """Thresholds that decide when a struggling disk stops being tolerable."""

    flush_interval_s: float = 2.0
    stall_abort_s: float = 10.0
    min_free_mb: int = 500
    warn_after_s: float = 2.0


class TrackWriter:
    """Consumes packets for one track and appends aligned audio to a WAV file.

    WAV while recording, on purpose: appending PCM is a memcpy and a write, so
    a crash costs only the unflushed tail. The lossless compression that makes
    the file small happens at finalization, when there is nothing to lose.
    """

    def __init__(
        self,
        path: Path,
        source_format: StreamFormat,
        *,
        session_qpc_ns: int,
        threshold_ms: int = 200,
        budget: WriteBudget | None = None,
    ) -> None:
        self.path = path
        self.source_format = source_format
        self.budget = budget or WriteBudget()
        self.stats = TrackStats()
        self._session_qpc_ns = session_qpc_ns
        self._threshold_ms = threshold_ms
        self.placer = TrackPlacer(
            session_qpc_ns=session_qpc_ns,
            sample_rate=source_format.sample_rate,
            threshold_ms=threshold_ms,
        )
        self._lock = threading.Lock()
        self._paused = False
        self._resampler = None
        self._peak = 0.0
        self._silent_since = time.monotonic()
        self._handle: WavFile | None = WavFile(
            path, sample_rate=TARGET_RATE, channels=TARGET_CHANNELS
        )
        self.stats.last_flush_monotonic = time.monotonic()

    def close(self) -> None:
        """Flush the resampler's tail, then close the file.

        The tail is not optional. ``soxr.ResampleStream`` emits in bursts and
        holds the filter's remaining output until it is told the stream ended;
        skipping this silently truncates every recording by the length of that
        delay -- measured here at about 28 ms.
        """
        tail = self._drain_resampler()
        if tail:
            self._append(tail)
        with self._lock:
            handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass

    def _drain_resampler(self) -> bytes:
        if self._resampler is None:
            return b""
        try:
            import numpy as np

            remaining = self._resampler.resample_chunk(
                np.zeros(0, dtype=np.float32), last=True
            )
        except Exception:
            return b""
        finally:
            self._resampler = None
        if remaining is None or len(remaining) == 0:
            return b""
        import numpy as np

        return (np.clip(remaining, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()

    def rebind(self, source_format: StreamFormat) -> int:
        """Continue this track on a different device, in the same file.

        The gap is not filled here. The placer is rebuilt against the *same*
        session reference and told how much of the timeline is already on
        disk, so when the first packet of the new device arrives it computes
        the silence itself -- by exactly the mechanism that already handles a
        loopback track which was idle at the start. One behaviour, not two.

        Returns the timeline position, in milliseconds, at which the new
        device takes over.
        """
        tail = self._drain_resampler()
        if tail:
            self._append(tail)

        already_ms = self.timeline_ms
        self.source_format = source_format
        self.placer = TrackPlacer(
            session_qpc_ns=self._session_qpc_ns,
            sample_rate=source_format.sample_rate,
            threshold_ms=self._threshold_ms,
        )
        self.placer.written_frames = int(
            already_ms * source_format.sample_rate / 1000
        )
        self._resampler = None
        return already_ms

    def take_level(self) -> float:
        """The loudest sample since the last read, from 0 to 1.

        Reading resets it. A meter shows what happened during the interval the
        person was looking at, and a level that only ever rose would stay
        pinned at the loudest moment of the whole meeting.
        """
        peak, self._peak = self._peak, 0.0
        if peak > 0.0:
            self._silent_since = time.monotonic()
        return peak

    @property
    def silent_for_s(self) -> float:
        """How long this track has been delivering nothing audible.

        A microphone muted at the operating system level still delivers
        packets, full of zeroes. Only this distinguishes "quiet room" from
        "recording silence for twenty minutes without knowing".
        """
        return time.monotonic() - self._silent_since

    @property
    def timeline_ms(self) -> int:
        """Where this track stands on the session timeline.

        Taken from the placer, not from bytes on disk: the resampler emits in
        bursts, so the file lags the timeline by up to one burst and would make
        a duration reported during recording jump around.
        """
        return self.placer.written_ms

    # -- conversion ----------------------------------------------------

    def _to_target(self, raw: bytes, frames: int):
        """Convert one packet to 16 kHz mono int16.

        Imported lazily and held per track: numpy and soxr are the heaviest
        things this process touches, and a surface that never records should
        not pay for them.
        """
        import numpy as np

        fmt = self.source_format
        if fmt.sample_format == "float32":
            data = np.frombuffer(raw, dtype=np.float32, count=frames * fmt.channels)
        elif fmt.sample_format == "int16":
            data = (
                np.frombuffer(raw, dtype=np.int16, count=frames * fmt.channels)
                .astype(np.float32) / 32768.0
            )
        elif fmt.sample_format == "int32":
            data = (
                np.frombuffer(raw, dtype=np.int32, count=frames * fmt.channels)
                .astype(np.float32) / 2147483648.0
            )
        else:
            raise ValueError(f"formato de amostra nao suportado: {fmt.sample_format}")

        if fmt.channels > 1:
            data = data.reshape(-1, fmt.channels).mean(axis=1)

        if fmt.sample_rate != TARGET_RATE:
            import soxr

            if self._resampler is None:
                # Stateful so that block boundaries do not click: a fresh
                # resampler per packet would lose the filter's history.
                self._resampler = soxr.ResampleStream(
                    fmt.sample_rate, TARGET_RATE, 1,
                    dtype="float32", quality="HQ",
                )
            data = self._resampler.resample_chunk(data)

        import numpy as np

        clipped = np.clip(data, -1.0, 1.0)
        if clipped.size:
            # One pass over samples already in cache, for the level meter that
            # tells a person their microphone is actually picking something up.
            # Kept as a running maximum and reset when read, so publishing it
            # costs the reader's rate and not the packet rate.
            self._peak = max(self._peak, float(np.abs(clipped).max()))
        return (clipped * 32767.0).astype(np.int16)

    # -- writing -------------------------------------------------------

    def write_packet(self, packet: CapturePacket) -> None:
        """Place a packet on the timeline and append what it contributes."""
        if self._paused:
            return
        placement = self.placer.place(packet)

        if placement.gap:
            self.stats.gaps += 1
        if placement.derived:
            self.stats.derived_packets += 1
        if placement.reanchored:
            self.stats.reanchors += 1
        if packet.discontinuity:
            self.stats.discontinuities += 1
        if placement.trim_frames:
            self.stats.trimmed_frames += placement.trim_frames

        payload = b""
        if placement.silence_frames > 0:
            filler = self._silence(placement.silence_frames)
            payload += filler
            # Counted in target frames like everything else in TrackStats;
            # the placer works in the device's rate and mixing the two units
            # in one place would make every number here suspect.
            self.stats.silence_frames += len(filler) // (
                TARGET_SAMPLE_WIDTH * TARGET_CHANNELS
            )

        if placement.write_frames > 0:
            offset = placement.trim_frames * self.source_format.block_align
            raw = packet.data[offset:]
            if packet.silent:
                # The OS says the buffer is silence and its contents are
                # undefined; synthesising the silence is cheaper and correct.
                payload += self._silence_at_target(placement.write_frames)
            else:
                payload += self._to_target(raw, placement.write_frames).tobytes()

        if payload:
            self._append(payload)

    def write_silence_ms(self, milliseconds: int) -> None:
        """Fill a stretch with silence -- used for a pause interval."""
        frames = int(milliseconds * TARGET_RATE / 1000)
        if frames > 0:
            self._append(self._silence_at_target(frames))
            self.stats.silence_frames += frames

    def _silence(self, source_frames: int) -> bytes:
        target = int(source_frames * TARGET_RATE / self.source_format.sample_rate)
        return self._silence_at_target(target)

    @staticmethod
    def _silence_at_target(frames: int) -> bytes:
        return b"\x00" * (frames * TARGET_SAMPLE_WIDTH * TARGET_CHANNELS)

    def _append(self, payload: bytes) -> None:
        with self._lock:
            handle = self._handle
            if handle is None:
                return
            try:
                handle.append(payload)
            except Exception as exc:
                self.stats.write_error = str(exc)
                raise
        self.stats.frames_written += len(payload) // (
            TARGET_SAMPLE_WIDTH * TARGET_CHANNELS
        )
        self.maybe_flush()

    def maybe_flush(self, *, force: bool = False) -> bool:
        """Force the file to disk on the durability cadence.

        The guarantee "at most N seconds lost" is this interval, nothing else:
        what a power cut takes is precisely what accumulated since the last
        completed flush.
        """
        now = time.monotonic()
        if not force and now - self.stats.last_flush_monotonic < self.budget.flush_interval_s:
            return False
        with self._lock:
            handle = self._handle
            if handle is None:
                return False
            try:
                handle.flush()
            except Exception as exc:
                self.stats.write_error = str(exc)
                return False
        self.stats.last_flush_monotonic = now
        self.stats.flushes += 1
        return True

    # -- pause ---------------------------------------------------------

    def pause(self) -> None:
        self._paused = True
        self.maybe_flush(force=True)

    def resume(self, silence_ms: int) -> None:
        """Resume, filling the paused interval so alignment survives it."""
        self._paused = False
        if silence_ms > 0:
            self.write_silence_ms(silence_ms)

    @property
    def paused(self) -> bool:
        return self._paused
