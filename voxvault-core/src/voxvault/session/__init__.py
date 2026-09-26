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
from datetime import UTC, datetime
from pathlib import Path

from ..config import Config
from ..errors import CaptureError
from ..layout import meeting_dir, track_path
from ..types import Track
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
        self.started_at = datetime.now(UTC)
        self._mic_stream = mic_stream
        self._system_stream = system_stream
        self._streams: dict[str, object] = {}
        #: A device the supervisor opened for a track, waiting for that track's
        #: writing thread to take it over. See :meth:`replace_stream`.
        self._pending: dict[str, object] = {}
        self._writers: dict[str, TrackWriter] = {}
        self.supervisor = None
        self._pumps: list[threading.Thread] = []
        self._pump_for: dict[str, threading.Thread] = {}
        #: Told of every warning as it happens -- the service's log. The list
        #: below is what the metadata keeps.
        self.on_warning = None
        self._last_read_error: dict[str, str] = {}
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
                self._warn(f"trilha '{track}' nao pode ser aberta: {exc}")

        if not opened:
            raise CaptureError(
                "Nenhuma trilha pode ser aberta; a gravacao nao foi iniciada."
            )

        self._session_qpc_ns = max(armed.values())
        self._streams = dict(opened)
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
            self._pump_for[track] = pump

        self._start_latency_ms = (time.perf_counter() - began) * 1000
        return self._start_latency_ms

    def _warn(self, message: str) -> None:
        """Keep a warning for the metadata and tell the service as it happens."""
        self._warnings.append(message)
        callback = self.on_warning
        if callback is not None:
            try:
                callback(message)
            except Exception:
                pass

    def _pump(self, track: str, stream) -> None:
        """Drain one stream into its writer until the session stops.

        The stream is looked up each turn rather than captured once: the
        device supervisor can swap it underneath, and a pump holding the old
        object would keep draining a device nobody is recording from.

        Nothing short of a disk that refuses the writes ends this loop. It
        used to end on the first packet that failed, and a track then stopped
        being written for the rest of the meeting while its device kept
        capturing -- with nothing on screen to say so.
        """
        writer = self._writers[track]
        while not self._halt.is_set():
            self._take_over_pending(track, writer)
            stream = self._streams.get(track)
            if stream is None:
                return  # the track was ended; the recording carries on
            try:
                packets = stream.read()
            except Exception as exc:
                # A device that is gone keeps raising until the supervisor
                # replaces it: said once per kind of failure, not 20 times a
                # second for as long as it lasts.
                text = str(exc)
                if self._last_read_error.get(track) != text:
                    self._last_read_error[track] = text
                    self._warn(f"trilha '{track}' parou de entregar: {exc}")
                self._halt.wait(PUMP_INTERVAL_S)
                continue
            self._last_read_error.pop(track, None)
            if packets:
                if not self._write(track, writer, packets):
                    return
            else:
                writer.maybe_flush()
            self._halt.wait(PUMP_INTERVAL_S)

    def _write(self, track: str, writer: TrackWriter, packets) -> bool:
        """Write packets; False only when the disk refuses them."""
        unwritten_before = writer.stats.unwritten_blocks
        for packet in packets:
            try:
                writer.write_packet(packet)
            except OSError as exc:
                self._warn(f"falha ao escrever '{track}': {exc}")
                return False
            except Exception as exc:
                # The packet could not even be placed on the timeline. The
                # next one fills the hole with silence, as for any gap.
                writer.count_unwritten(getattr(packet, "frames", 0), str(exc))
        if unwritten_before == 0 and writer.stats.unwritten_blocks > 0:
            self._warn(
                f"trilha '{track}': bloco nao escrito ({writer.stats.unwritten_reason}); "
                f"a trilha continua"
            )
        return True

    def _take_over_pending(self, track: str, writer: TrackWriter) -> None:
        """Move a track to the device the supervisor opened for it.

        Here, on the writing thread, and in order: what the old device still
        held is written in the old device's format, and only then is the
        writer moved to the new one. Moved from the supervisor's thread, the
        writer could change format while this thread was still writing the
        old device's packets, and read them in a format they were not in.
        """
        with self._lock:
            new = self._pending.pop(track, None)
        if new is None:
            return
        old = self._streams.get(track)
        if old is not None:
            try:
                leftovers = old.read()
            except Exception:
                leftovers = []
            if leftovers:
                self._write(track, writer, leftovers)
        try:
            writer.rebind(new.format)
        except Exception as exc:
            # The new device is not taken; the old one, gone, stays in place
            # and the supervisor finds it lost again on its next check.
            self._warn(f"trilha '{track}': o novo dispositivo nao pode ser assumido ({exc})")
            try:
                new.stop()
            except Exception:
                pass
            return
        with self._lock:
            self._streams[track] = new
        self._last_read_error.pop(track, None)
        if old is not None:
            try:
                old.stop()
            except Exception:
                pass

    # -- devices -------------------------------------------------------

    def stream_for(self, track: str):
        with self._lock:
            return self._pending.get(track) or self._streams.get(track)

    def writer_for(self, track: str) -> TrackWriter | None:
        return self._writers.get(track)

    def replace_stream(self, track: str, stream) -> None:
        """Hand a track a new device, without touching its file or timeline.

        The track's writing thread takes it over on its next turn -- see
        :meth:`_take_over_pending`. A track nobody writes any more cannot take
        a device: that one is closed at once instead of capturing into a
        buffer nobody drains.
        """
        pump = self._pump_for.get(track)
        if pump is None or not pump.is_alive():
            try:
                stream.stop()
            except Exception:
                pass
            self._warn(f"trilha '{track}' nao esta sendo escrita; o novo dispositivo foi fechado")
            return
        with self._lock:
            superseded = self._pending.get(track)
            self._pending[track] = stream
        if superseded is not None:
            try:
                superseded.stop()
            except Exception:
                pass

    def end_track(self, track: str) -> None:
        """Stop one track for good. The recording continues on the other."""
        with self._lock:
            stream = self._streams.pop(track, None)
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass

    def supervise_devices(self, watches) -> None:
        """Start watching the tracks' devices for loss and for role changes."""
        from .supervisor import DeviceSupervisor

        supervisor = DeviceSupervisor(self)
        for watch in watches:
            supervisor.watch(watch)
        supervisor.start()
        self.supervisor = supervisor

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
        """How misaligned the two tracks are, from their clock residuals.

        Measured as the spread between the tracks' placement errors, not as
        the difference in their lengths. The length difference was the first
        version of this, and it was wrong in the most ordinary situation there
        is: nobody talking. The loopback delivers nothing while nothing plays,
        so its written length stops growing -- and that read as ever-growing
        "divergence", firing the alarm every second of every quiet stretch,
        when the next packet would be placed at its own instant regardless.
        """
        residuals = [
            w.residual_ns for w in self._writers.values() if w.residual_ns is not None
        ]
        if len(residuals) < 2:
            return 0
        drift = round((max(residuals) - min(residuals)) / 1_000_000)
        self._max_drift_ms = max(getattr(self, "_max_drift_ms", 0), drift)
        return drift

    @property
    def warnings(self) -> list[str]:
        drift = self.drift_ms
        if drift > self.config.drift_warn_ms and not getattr(self, "_drift_warned", False):
            # Once, with the value at that moment. A warning whose text changes
            # every second is a new warning every second to anything that
            # de-duplicates by text -- and a person stops reading those.
            self._drift_warned = True
            self._warn(
                f"as trilhas passaram a divergir {drift} ms, acima do limite de "
                f"{self.config.drift_warn_ms} ms"
            )
        collected = list(self._warnings)
        for track, writer in self._writers.items():
            if writer.stats.write_error:
                collected.append(f"erro de escrita em '{track}': {writer.stats.write_error}")
        return collected

    def levels(self) -> dict[str, dict]:
        """Per-track level, for a meter. Reading resets each track's peak."""
        return {
            track: {
                "pico": round(writer.take_level(), 4),
                "silencio_ha_s": round(writer.silent_for_s, 1),
            }
            for track, writer in self._writers.items()
        }

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
                **(
                    {
                        "blocos_nao_escritos": writer.stats.unwritten_blocks,
                        "nao_escrito_ms": round(writer.stats.unwritten_ms, 1),
                        "blocos_nao_escritos_em": list(writer.stats.unwritten_at),
                    }
                    if writer.stats.unwritten_blocks
                    else {}
                ),
            }
            for track, writer in self._writers.items()
        }

    # -- the durable minimum -------------------------------------------

    def durable_minimum(self, reason: str = "suspensao do sistema") -> float:
        """Make the recording survivable, and nothing more. Returns elapsed ms.

        The operating system gives about two seconds' warning before it
        suspends. A full finalization compresses the whole recording losslessly
        and does not remotely fit in that, so attempting it would lose the
        meeting rather than save it.

        Four things, in this order: stop capturing, force the pending audio
        out, close the files, persist the metadata with the real duration and
        why it ended. Compression, exports and queueing are derived work, and
        the resume -- or the next start, if this process does not survive --
        picks them up from what is on disk.
        """
        began = time.perf_counter()
        if self._finished:
            return 0.0

        self._halt.set()
        if self.supervisor is not None:
            self.supervisor.stop()

        with self._lock:
            handed_over = list(self._pending.values())
            self._pending.clear()
        for stream in list(self._streams.values()) + handed_over:
            try:
                stream.stop()
            except Exception:
                pass

        for writer in self._writers.values():
            try:
                writer.maybe_flush(force=True)
            except Exception:
                pass

        duration = self.duration_ms
        stats = self.track_stats()
        for writer in self._writers.values():
            try:
                writer.close()
            except Exception:
                pass
        self._finished = True
        self._duration_at_stop = duration
        self._drift_at_stop = self.drift_ms

        metadata = read_metadata(self.directory) or Metadata(uid=self.uid)
        metadata.title = self.title
        metadata.started_at = self.started_at.isoformat()
        metadata.duration_ms = duration
        metadata.ended_at = now_iso()
        metadata.tracks = stats
        metadata.pauses = [p.as_dict() for p in self._pauses]
        metadata.warnings = [*list(self._warnings), f"a gravacao foi encerrada por {reason}"]
        # Stops at "files closed": everything after it is derived work that
        # the resume redoes, and claiming otherwise would make recovery skip
        # steps that never ran.
        metadata.step = Step.FILES_CLOSED.value
        write_metadata(self.directory, metadata)

        return (time.perf_counter() - began) * 1000

    # -- stopping ------------------------------------------------------

    def stop(self, *, submit=None, compress: bool = True) -> SessionReport:
        """Close the streams, finalize the directory, and report.

        Every step before the metadata stands on its own: one that fails is a
        warning, and the metadata and the finalization still happen. A stop
        that raised halfway once left a real meeting marked "recording" for
        good, its files never closed.
        """
        if self._finished:
            return self._report()

        if self._paused.is_set():
            self._step("retomar da pausa", self.resume)

        if self.supervisor is not None:
            self._step("parar a supervisao de dispositivos", self.supervisor.stop)

        self._halt.set()
        for pump in self._pumps:
            pump.join(timeout=3.0)

        # Drain whatever the streams still held -- the device in use and one
        # handed over that the writing thread never got to take -- including
        # packets a stream kept back while measuring its position scale.
        for track, writer in self._writers.items():
            with self._lock:
                current = self._streams.get(track)
                pending = self._pending.pop(track, None)
            for stream, handed_over in ((current, False), (pending, True)):
                if stream is None:
                    continue
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    if handed_over:
                        writer.rebind(stream.format)
                    self._write(track, writer, stream.read())
                except Exception as exc:
                    self._warn(f"trilha '{track}': o fim do audio nao foi escrito ({exc})")

        self._step("igualar a duracao das trilhas", self._pad_tracks_to_equal_length)

        duration = self._safely("medir a duracao", lambda: self.duration_ms, 0)
        drift = self._safely("medir a divergencia", lambda: self.drift_ms, 0)
        stats = self._safely("reunir as estatisticas", self.track_stats, {})
        warnings = self._safely("reunir os avisos", lambda: self.warnings, list(self._warnings))

        for track, writer in self._writers.items():
            self._step(f"fechar a trilha '{track}'", writer.close)
        self._finished = True

        metadata = read_metadata(self.directory) or Metadata(uid=self.uid)
        metadata.title = self.title
        metadata.started_at = self.started_at.isoformat()
        metadata.tracks = stats
        metadata.pauses = [p.as_dict() for p in self._pauses]
        metadata.warnings = warnings
        metadata.alignment = self._safely("reunir o alinhamento", lambda: {
            # The worst the tracks got, not where they happened to end: a
            # recording can drift and recover, and the peak is what says
            # whether its timeline can be trusted.
            "divergencia_ms": max(drift, getattr(self, "_max_drift_ms", 0)),
            "divergencia_final_ms": drift,
            "residuo_por_trilha_ms": {
                track: round(writer.residual_ns / 1_000_000, 1)
                for track, writer in self._writers.items()
                if writer.residual_ns is not None
            },
            "limite_aviso_ms": self.config.drift_warn_ms,
            "atraso_de_inicio_ms": round(self._start_latency_ms, 1),
        }, {})
        if self.supervisor is not None:
            # A device change is a real gap in what was heard, so it belongs in
            # the metadata with its instant and its length -- not only in a
            # warning somebody may never read.
            metadata.alignment["lacunas_por_dispositivo"] = self._safely(
                "reunir as lacunas", self.supervisor.gaps, []
            )
            metadata.alignment["saude_das_trilhas"] = self._safely(
                "reunir a saude das trilhas", self.supervisor.health, {}
            )
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

    def _step(self, what: str, action) -> None:
        """Run one step of the stop; a failure is a warning, not the end."""
        try:
            action()
        except Exception as exc:
            self._warn(f"encerramento: nao foi possivel {what} ({exc})")

    def _safely(self, what: str, compute, fallback):
        try:
            return compute()
        except Exception as exc:
            self._warn(f"encerramento: nao foi possivel {what} ({exc})")
            return fallback

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
                self._warn(
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
