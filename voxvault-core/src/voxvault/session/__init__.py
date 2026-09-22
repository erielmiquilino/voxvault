"""A recording session: two tracks, one timeline, one lifecycle.

The microphone and the mix the system is playing are captured as separate
streams and written as separate files. That physical separation is the whole
speaker-attribution mechanism -- no diarization model, and no cooperation from
Teams, Meet or Zoom, which is the point of the product.

Position zero of the timeline is the instant both streams are armed, not the
instant the command was given and not the first sample. Both tracks are
expressed against that one reference, so they line up even though the loopback
track typically delivers nothing at all for the first stretch of a meeting.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from ..errors import CaptureError
from ..layout import TRACK_BASENAME, meeting_dir, track_path
from ..types import MeetingState, Track
from .finalize import (
    Metadata,
    Step,
    finalize_session,
    now_iso,
    read_metadata,
    write_metadata,
)
from .track import TrackWriter, WriteBudget

#: How often each pump drains its stream. Well under the stream's own buffer,
#: so a late turn costs latency and never audio.
PUMP_INTERVAL_S = 0.05

#: Budget from the start command to the first captured sample, when nothing
#: has to be interrupted first.
START_BUDGET_S = 1.5
#: The same budget when a transcription has to be stopped and GPU memory
#: released before capture can begin.
START_BUDGET_INTERRUPTING_S = 2.0


@dataclass(slots=True)
class PauseInterval:
    started_ms: int
    duration_ms: int = 0

    def as_dict(self) -> dict:
        return {"inicio_ms": self.started_ms, "duracao_ms": self.duration_ms}


@dataclass(slots=True)
class SessionReport:
    """What a finished session looked like, for the caller to show or store."""

    uid: str
    directory: Path
    duration_ms: int
    start_latency_ms: float
    tracks: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    drift_ms: int = 0


class RecordingSession:
    """One recording, from armed streams to a finalized directory on disk."""

    def __init__(
        self,
        config: Config,
        *,
        uid: str | None = None,
        title: str = "",
        mic_stream=None,
        system_stream=None,
    ) -> None:
        self.config = config
        self.uid = uid or uuid.uuid4().hex
        self.title = title or _default_title()
        self.directory = meeting_dir(config.data_dir, self.uid)
        self.started_at = datetime.now(timezone.utc)
        self._mic_stream = mic_stream
        self._system_stream = system_stream
        self._writers: dict[str, TrackWriter] = {}
        self._pumps: list[threading.Thread] = []
        self._halt = threading.Event()
        self._paused = threading.Event()
        self._pauses: list[PauseInterval] = []
        self._pause_started_monotonic = 0.0
        self._start_latency_ms = 0.0
        self._session_qpc_ns = 0
        self._lock = threading.Lock()
        self._warnings: list[str] = []
        self._finished = False

    # -- lifecycle -----------------------------------------------------

    def start(self, streams: dict[str, object] | None = None) -> float:
        """Arm both streams and begin writing. Returns the start latency in ms.

        The session's instant zero is when the *later* of the two streams was
        armed, so neither track starts before the other has any chance of
        delivering.
        """
        began = time.perf_counter()
        if streams is not None:
            self._mic_stream = streams.get(Track.MIC.value, self._mic_stream)
            self._system_stream = streams.get(Track.SYSTEM.value, self._system_stream)

        opened: dict[str, object] = {}
        armed: dict[str, int] = {}
        for track, stream in (
            (Track.MIC.value, self._mic_stream),
            (Track.SYSTEM.value, self._system_stream),
        ):
            if stream is None:
                continue
            try:
                armed[track] = stream.start()
                opened[track] = stream
            except Exception as exc:
                # One track failing must not cancel the recording: half a
                # meeting is far better than none.
                self._warnings.append(f"trilha '{track}' nao pode ser aberta: {exc}")

        if not opened:
            raise CaptureError(
                "Nenhuma trilha pode ser aberta; a gravacao nao foi iniciada."
            )

        self._session_qpc_ns = max(armed.values())
        self.directory.mkdir(parents=True, exist_ok=True)

        budget = WriteBudget(
            flush_interval_s=self.config.flush_interval_s,
            stall_abort_s=self.config.write_stall_abort_s,
            min_free_mb=self.config.min_free_mb,
        )
        for track, stream in opened.items():
            self._writers[track] = TrackWriter(
                track_path(self.directory, track),
                stream.format,
                session_qpc_ns=self._session_qpc_ns,
                threshold_ms=self.config.silence_fill_threshold_ms,
                budget=budget,
            )

        write_metadata(self.directory, Metadata(
            uid=self.uid, title=self.title,
            started_at=self.started_at.isoformat(),
            step=Step.NOT_STARTED.value,
            devices={
                track: getattr(stream, "endpoint_id", "")
                for track, stream in opened.items()
            },
        ))

        for track, stream in opened.items():
            pump = threading.Thread(
                target=self._pump, args=(track, stream),
                name=f"voxvault-escrita-{track}", daemon=True,
            )
            pump.start()
            self._pumps.append(pump)

        self._start_latency_ms = (time.perf_counter() - began) * 1000
        return self._start_latency_ms

    def _pump(self, track: str, stream) -> None:
        """Drain one stream into its writer until the session stops."""
        writer = self._writers[track]
        while not self._halt.is_set():
            try:
                packets = stream.read()
            except Exception as exc:
                self._warnings.append(f"trilha '{track}' parou de entregar: {exc}")
                return
            if packets:
                for packet in packets:
                    try:
                        writer.write_packet(packet)
                    except Exception as exc:
                        self._warnings.append(f"falha ao escrever '{track}': {exc}")
                        return
            else:
                writer.maybe_flush()
            self._halt.wait(PUMP_INTERVAL_S)

    # -- pause ---------------------------------------------------------

    def pause(self) -> None:
        """Stop capturing on both tracks, keeping the session open."""
        with self._lock:
            if self._paused.is_set() or self._finished:
                return
            self._paused.set()
            self._pause_started_monotonic = time.monotonic()
            self._pauses.append(PauseInterval(started_ms=self.duration_ms))
            for writer in self._writers.values():
                writer.pause()

    def resume(self) -> None:
        """Resume, filling the paused stretch with silence on both tracks.

        Both tracks get the same filler, which is what keeps them aligned
        across a pause instead of drifting apart by its length.
        """
        with self._lock:
            if not self._paused.is_set() or self._finished:
                return
            elapsed_ms = int((time.monotonic() - self._pause_started_monotonic) * 1000)
            if self._pauses:
                self._pauses[-1].duration_ms = elapsed_ms
            for writer in self._writers.values():
                writer.resume(silence_ms=elapsed_ms)
            self._paused.clear()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    # -- observation ---------------------------------------------------

    @property
    def duration_ms(self) -> int:
        """Longest track's timeline position -- the meeting's length so far."""
        if not self._writers:
            return 0
        return max(w.timeline_ms for w in self._writers.values())

    @property
    def drift_ms(self) -> int:
        """How far apart the two tracks currently sit on the timeline."""
        if len(self._writers) < 2:
            return 0
        positions = [w.timeline_ms for w in self._writers.values()]
        return max(positions) - min(positions)

    @property
    def warnings(self) -> list[str]:
        collected = list(self._warnings)
        if self.drift_ms > self.config.drift_warn_ms:
            collected.append(
                f"as trilhas divergiram {self.drift_ms} ms, acima do limite de "
                f"{self.config.drift_warn_ms} ms"
            )
        for track, writer in self._writers.items():
            if writer.stats.write_error:
                collected.append(f"erro de escrita em '{track}': {writer.stats.write_error}")
        return collected

    def track_stats(self) -> dict[str, dict]:
        return {
            track: {
                "duracao_ms": writer.timeline_ms,
                "lacunas": writer.stats.gaps,
                "silencio_inserido_ms": int(
                    writer.stats.silence_frames * 1000 / 16000
                ),
                "descontinuidades": writer.stats.discontinuities,
                "pacotes_com_posicao_derivada": writer.stats.derived_packets,
                "reancoragens": writer.stats.reanchors,
                "flushes": writer.stats.flushes,
            }
            for track, writer in self._writers.items()
        }

    # -- stopping ------------------------------------------------------

    def stop(self, *, submit=None, compress: bool = True) -> SessionReport:
        """Close the streams, finalize the directory, and report."""
        if self._finished:
            return self._report()

        if self._paused.is_set():
            self.resume()

        self._halt.set()
        for pump in self._pumps:
            pump.join(timeout=3.0)

        for stream in (self._mic_stream, self._system_stream):
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass

        # Drain whatever the streams still held, including packets a stream
        # kept back while measuring its position scale.
        for track, stream in (
            (Track.MIC.value, self._mic_stream),
            (Track.SYSTEM.value, self._system_stream),
        ):
            writer = self._writers.get(track)
            if stream is None or writer is None:
                continue
            try:
                for packet in stream.read():
                    writer.write_packet(packet)
            except Exception:
                pass

        self._pad_tracks_to_equal_length()

        duration = self.duration_ms
        drift = self.drift_ms
        stats = self.track_stats()
        warnings = self.warnings

        for writer in self._writers.values():
            writer.close()
        self._finished = True

        metadata = read_metadata(self.directory) or Metadata(uid=self.uid)
        metadata.title = self.title
        metadata.started_at = self.started_at.isoformat()
        metadata.tracks = stats
        metadata.pauses = [p.as_dict() for p in self._pauses]
        metadata.warnings = warnings
        metadata.alignment = {
            "divergencia_ms": drift,
            "limite_aviso_ms": self.config.drift_warn_ms,
            "atraso_de_inicio_ms": round(self._start_latency_ms, 1),
        }
        write_metadata(self.directory, metadata)

        finalize_session(
            self.directory,
            tracks=list(self._writers),
            duration_ms=duration,
            ended_at=now_iso(),
            submit=submit,
            compress=compress,
        )
        self._duration_at_stop = duration
        self._drift_at_stop = drift
        return self._report()

    def _pad_tracks_to_equal_length(self) -> None:
        """Bring every track up to the longest one, with silence.

        The case this exists for is the ordinary one: the loopback endpoint
        delivers nothing at all while nothing is playing, so a meeting where
        the other participants never made a sound -- or where the output went
        somewhere else -- ends with a system track of zero length. Two files of
        different lengths do not describe one timeline, and anything reading
        them afterwards would place the second track's speech at the wrong
        instant.

        Silence here is the honest content: those seconds really were silent
        on that track.
        """
        if len(self._writers) < 2:
            return
        longest = max(w.timeline_ms for w in self._writers.values())
        for track, writer in self._writers.items():
            missing = longest - writer.timeline_ms
            if missing <= 0:
                continue
            had_audio = writer.stats.frames_written > 0
            writer.write_silence_ms(missing)
            writer.placer.written_frames += int(
                missing * writer.source_format.sample_rate / 1000
            )
            if not had_audio:
                self._warnings.append(
                    f"a trilha '{track}' nao recebeu audio algum e foi "
                    f"preenchida com {missing / 1000:.1f}s de silencio para "
                    f"manter o alinhamento com a outra trilha"
                )

    def _report(self) -> SessionReport:
        return SessionReport(
            uid=self.uid,
            directory=self.directory,
            duration_ms=getattr(self, "_duration_at_stop", self.duration_ms),
            start_latency_ms=self._start_latency_ms,
            tracks={t: s["duracao_ms"] for t, s in self.track_stats().items()},
            warnings=self._warnings,
            drift_ms=getattr(self, "_drift_at_stop", self.drift_ms),
        )


def _default_title() -> str:
    """A title nobody has to type, that still says when the meeting was."""
    now = datetime.now()
    return f"Reuniao de {now:%d/%m/%Y as %H:%M}"


__all__ = [
    "START_BUDGET_INTERRUPTING_S",
    "START_BUDGET_S",
    "Metadata",
    "PauseInterval",
    "RecordingSession",
    "SessionReport",
    "Step",
    "TrackWriter",
    "finalize_session",
]
