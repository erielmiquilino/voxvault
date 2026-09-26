"""Keeping a track recording when its device changes underneath it.

Plugging in a headset mid-meeting is the most ordinary thing that can happen,
and it is the case where a recorder either survives or quietly stops capturing
half the conversation. Three separate things have to be caught:

* the device in use **disappears** -- the stream errors, which is easy;
* the default device for the configured role **changes** while the old one is
  still perfectly available and the stream reports nothing wrong at all;
* a **call starts or ends on a headset** whose microphone is being recorded:
  the call plays through the headset's hands-free output, which need not be
  the default of any role.

Polling catches all three. Detection is bounded at five seconds and recovery
at thirty; they are different budgets and the metadata records their sum as
one gap, because that is what the person listening actually lost.

A track that is still without a device after thirty seconds is marked
incomplete and the person is told -- but it is not given up on. Found on a
real meeting: a Bluetooth headset dropped, Bluetooth was restarted, and the
headset came back after the budget; both tracks had been ended and the rest
of the meeting was lost. Tries continue for as long as the recording lasts,
each in a thread of its own, so a device that hangs while opening -- a
headset halfway through reconnecting -- delays nobody else's detection.

Nothing here ever ends the recording: the session finishes when the person
says so.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum

from ..config import POLICY_PINNED

#: Poll period. Well inside the five-second detection budget, and one endpoint
#: enumeration every couple of seconds costs nothing measurable.
CHECK_INTERVAL_S = 2.0
#: Detection budget from the specification.
DETECTION_BUDGET_S = 5.0
#: Recovery budget, counted from the moment the change was detected. Past it
#: the track is marked incomplete, and still retried.
RECOVERY_BUDGET_S = 30.0
#: How often a track still without a device retries once the budget is spent.
RETRY_INTERVAL_S = 5.0
#: A microphone that has delivered no packet for this long is treated as lost.
#: Inside the detection budget, with room for one missed poll.
STALL_S = 4.0
#: How long a check waits for an attempt before moving on. A device that opens
#: at once recovers inside the same check; one that hangs keeps only its own
#: thread busy.
ATTEMPT_WAIT_S = 0.5


class TrackHealth(StrEnum):
    RECORDING = "gravando"
    RECOVERING = "recuperando"
    INCOMPLETE = "incompleta"


@dataclass(slots=True)
class DeviceGap:
    """One stretch lost to a device change, as the metadata records it."""

    track: str
    started_ms: int
    duration_ms: int
    reason: str
    from_device: str = ""
    to_device: str = ""

    def as_dict(self) -> dict:
        return {
            "trilha": self.track,
            "inicio_ms": self.started_ms,
            "duracao_ms": self.duration_ms,
            "motivo": self.reason,
            "de": self.from_device,
            "para": self.to_device,
        }


class _Attempt:
    """One try at reopening a track, in a thread of its own."""

    def __init__(self, open_track) -> None:
        self.done = threading.Event()
        self.endpoint = None
        self.stream = None
        self.error: BaseException | None = None
        self._thread = threading.Thread(
            target=self._run, args=(open_track,), name="voxvault-reabertura",
            daemon=True,
        )
        self._thread.start()

    def _run(self, open_track) -> None:
        try:
            self.endpoint, self.stream = open_track()
        except BaseException as exc:
            self.error = exc
        finally:
            self.done.set()

    def discard(self) -> None:
        """Close whatever this attempt opened, now or when it finishes."""
        def close() -> None:
            self.done.wait()
            if self.stream is not None:
                try:
                    self.stream.stop()
                except Exception:
                    pass

        threading.Thread(target=close, name="voxvault-descarte", daemon=True).start()


@dataclass(slots=True)
class TrackWatch:
    """Everything the supervisor needs to know about one track."""

    track: str
    flow: str
    policy: str
    pinned_id: str
    role: str
    endpoint_id: str
    endpoint_name: str = ""
    health: TrackHealth = TrackHealth.RECORDING
    recovering_since: float = 0.0
    detected_at_ms: int = 0
    reason: str = ""
    gaps: list = field(default_factory=list)
    #: Packet count at the last check, and when it last moved. Used only for
    #: the microphone, which delivers continuously while it is alive.
    last_packets: int = -1
    progressed_at: float = 0.0
    #: Set once the recovery budget runs out. The track keeps being retried
    #: and may come back, but its hole is longer than the budget allowed.
    incomplete: bool = False
    #: Whether the track records a headset's call output right now.
    on_call_output: bool = False
    to_device: str = ""
    last_error: str = ""
    attempt: _Attempt | None = None
    next_attempt_at: float = 0.0

    @property
    def follows_default(self) -> bool:
        return self.policy != POLICY_PINNED

    @property
    def is_capture(self) -> bool:
        from ..capture.devices import FLOW_CAPTURE

        return self.flow == FLOW_CAPTURE

    @property
    def is_system(self) -> bool:
        from ..capture.devices import FLOW_RENDER

        return self.flow == FLOW_RENDER


class DeviceSupervisor:
    """Watches both tracks and migrates or reopens them as needed."""

    def __init__(
        self,
        session,
        *,
        interval_s: float = CHECK_INTERVAL_S,
        recovery_budget_s: float = RECOVERY_BUDGET_S,
        retry_interval_s: float = RETRY_INTERVAL_S,
        attempt_wait_s: float = ATTEMPT_WAIT_S,
    ) -> None:
        self.session = session
        self.interval_s = interval_s
        self.recovery_budget_s = recovery_budget_s
        self.retry_interval_s = retry_interval_s
        self.attempt_wait_s = attempt_wait_s
        self.watches: dict[str, TrackWatch] = {}
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------

    def watch(self, watch: TrackWatch) -> None:
        self.watches[watch.track] = watch

    def start(self) -> None:
        if self._thread is not None or not self.watches:
            return
        self._halt.clear()
        self._thread = threading.Thread(
            target=self._run, name="voxvault-dispositivos", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._halt.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=3.0)
        for watch in self.watches.values():
            if watch.attempt is not None:
                # Whatever an attempt still in flight opens, nobody records.
                watch.attempt.discard()
                watch.attempt = None
            if watch.health is TrackHealth.RECOVERING:
                elapsed = time.monotonic() - watch.recovering_since
                watch.gaps.append(DeviceGap(
                    track=watch.track,
                    started_ms=watch.detected_at_ms,
                    duration_ms=int((elapsed + self.interval_s) * 1000),
                    reason=f"{watch.reason}; nao recuperada ate o fim da gravacao"
                           + (f" ({watch.last_error})" if watch.last_error else ""),
                    from_device=watch.endpoint_name,
                ))

    def _run(self) -> None:
        while not self._halt.wait(self.interval_s):
            try:
                self.check()
            except Exception as exc:  # a supervisor must not kill a recording
                self.session._warn(f"supervisao de dispositivos falhou: {exc}")

    # -- the check -----------------------------------------------------

    def check(self) -> None:
        for watch in list(self.watches.values()):
            if watch.health is TrackHealth.RECOVERING:
                self._continue_recovery(watch)
                continue
            self._check_healthy(watch)

    def _check_healthy(self, watch: TrackWatch) -> None:
        stream = self.session.stream_for(watch.track)
        if stream is None:
            return

        if getattr(stream, "error", None) is not None or not stream.running:
            self._begin_recovery(watch, "o dispositivo em uso foi perdido")
            return

        if watch.is_capture and self._stalled(watch, stream):
            # The case that lost a real meeting: a Bluetooth headset whose
            # battery died. Not every driver reports that as an error -- the
            # stream can stay "running" and simply stop delivering. A
            # microphone is never legitimately silent at the packet level: it
            # delivers room noise, or zeroes, continuously while it is alive.
            # The loopback track is exempt, because nothing playing is normal.
            self._begin_recovery(
                watch,
                f"o microfone '{watch.endpoint_name}' parou de entregar audio "
                f"ha {STALL_S:.0f}s",
            )
            return

        if not watch.follows_default:
            return  # a pinned track never migrates, by policy

        target = self._target(watch)
        if target is not None and target.id != watch.endpoint_id:
            # The old device may still be perfectly available and the stream
            # perfectly healthy. Following the role -- or the call -- means
            # following it anyway.
            self._begin_recovery(watch, self._why(watch, target), to_device=target.name)

    def _why(self, watch: TrackWatch, target) -> str:
        if watch.is_system and getattr(target, "is_call_endpoint", False):
            return f"a chamada passou a tocar em '{target.name}'"
        if watch.is_system and watch.on_call_output:
            return (
                f"a saida de chamada '{watch.endpoint_name}' deixou de estar "
                f"ativa; o padrao de {watch.role} e '{target.name}'"
            )
        return f"o padrao de {watch.role} passou a ser '{target.name}'"

    def _stalled(self, watch: TrackWatch, stream) -> bool:
        """True when a capture stream has delivered nothing for too long."""
        packets = getattr(stream, "packets_captured", None)
        if packets is None:
            return False
        now = time.monotonic()
        if packets != watch.last_packets:
            watch.last_packets = packets
            watch.progressed_at = now
            return False
        return now - watch.progressed_at >= STALL_S

    def _microphone_id(self) -> str:
        from ..capture.devices import FLOW_CAPTURE

        for other in self.watches.values():
            if other.flow == FLOW_CAPTURE:
                return other.endpoint_id
        return ""

    def _target(self, watch: TrackWatch):
        """Where a track that follows the default should be recording now."""
        try:
            return self._resolve(watch)
        except Exception:
            return None

    def _resolve(self, watch: TrackWatch):
        from ..capture import devices

        if not watch.follows_default:
            return devices.resolve_endpoint(
                flow=watch.flow, policy_pinned_id=watch.pinned_id, role=watch.role
            )
        if watch.is_system:
            return devices.system_track_endpoint(
                role=watch.role, microphone_id=self._microphone_id()
            )
        return devices.resolve_endpoint(flow=watch.flow, role=watch.role)

    # -- recovery ------------------------------------------------------

    def _begin_recovery(self, watch: TrackWatch, reason: str, to_device: str = "") -> None:
        watch.health = TrackHealth.RECOVERING
        watch.recovering_since = time.monotonic()
        watch.reason = reason
        watch.to_device = to_device
        watch.last_error = ""
        watch.next_attempt_at = 0.0
        writer = self.session.writer_for(watch.track)
        watch.detected_at_ms = writer.timeline_ms if writer is not None else 0
        self.session._warn(f"trilha '{watch.track}': {reason}; recuperando")
        # Try immediately: a headset that is already present recovers on the
        # first attempt, and waiting a whole interval would widen the gap for
        # no reason.
        self._continue_recovery(watch)

    def _continue_recovery(self, watch: TrackWatch) -> None:
        if watch.attempt is None:
            if time.monotonic() < watch.next_attempt_at:
                return
            watch.attempt = _Attempt(lambda: self._open(watch))
        attempt = watch.attempt
        if not attempt.done.wait(self.attempt_wait_s):
            self._mark_incomplete_when_due(watch)
            return  # still opening; its own thread, nobody else waits

        watch.attempt = None
        if attempt.error is not None or attempt.stream is None:
            watch.last_error = str(attempt.error)
            self._mark_incomplete_when_due(watch)
            elapsed = time.monotonic() - watch.recovering_since
            if elapsed >= self.recovery_budget_s:
                watch.next_attempt_at = time.monotonic() + self.retry_interval_s
            return
        self._hand_over(watch, attempt.endpoint, attempt.stream)

    def _open(self, watch: TrackWatch):
        """Resolve where the track records and open it. Runs in the attempt's thread."""
        from ..capture.devices import FLOW_RENDER
        from ..capture.stream import CaptureStream

        endpoint = self._resolve(watch)
        stream = CaptureStream(
            endpoint.id, loopback=(watch.flow == FLOW_RENDER), name=watch.track
        )
        stream.start()
        if self._halt.is_set():
            stream.stop()
            raise RuntimeError("a gravacao terminou durante a reabertura")
        return endpoint, stream

    def _mark_incomplete_when_due(self, watch: TrackWatch) -> None:
        if watch.incomplete:
            return
        if time.monotonic() - watch.recovering_since < self.recovery_budget_s:
            return
        watch.incomplete = True
        detail = f" ({watch.last_error})" if watch.last_error else ""
        self.session._warn(
            f"trilha '{watch.track}' marcada como incompleta: {watch.reason}; "
            f"sem dispositivo ha {self.recovery_budget_s:.0f}s{detail}. "
            f"As tentativas continuam enquanto a gravacao durar."
        )

    def _hand_over(self, watch: TrackWatch, endpoint, stream) -> None:
        elapsed = time.monotonic() - watch.recovering_since
        # The session moves the writer to the new device's format on the
        # writer's own thread, after what the old device still held.
        self.session.replace_stream(watch.track, stream)

        previous_name = watch.endpoint_name
        watch.endpoint_id = endpoint.id
        watch.endpoint_name = endpoint.name
        watch.on_call_output = watch.is_system and bool(
            getattr(endpoint, "is_call_endpoint", False)
        )
        watch.health = TrackHealth.RECORDING
        # A fresh stream gets a fair window before it can be judged stalled;
        # its packet count starts over and has nothing to do with the old one.
        watch.last_packets = -1
        watch.progressed_at = time.monotonic()
        watch.gaps.append(DeviceGap(
            track=watch.track,
            started_ms=watch.detected_at_ms,
            # Detection and recovery are different budgets; what the listener
            # lost is their sum, recorded as one gap.
            duration_ms=int((elapsed + self.interval_s) * 1000),
            reason=watch.reason,
            from_device=previous_name,
            to_device=watch.to_device or endpoint.name,
        ))
        self.session._warn(
            f"trilha '{watch.track}': gravando agora em '{endpoint.name}'"
        )

    # -- reporting -----------------------------------------------------

    def gaps(self) -> list[dict]:
        return [
            gap.as_dict()
            for watch in self.watches.values()
            for gap in watch.gaps
        ]

    def health(self) -> dict[str, str]:
        return {
            track: str(TrackHealth.INCOMPLETE if w.incomplete else w.health)
            for track, w in self.watches.items()
        }

    def device_names(self) -> dict[str, str]:
        """What each track records from right now, by name."""
        return {track: w.endpoint_name for track, w in self.watches.items()}
