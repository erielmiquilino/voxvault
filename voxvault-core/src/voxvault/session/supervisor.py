"""Keeping a track recording when its device changes underneath it.

Plugging in a headset mid-meeting is the most ordinary thing that can happen,
and it is the case where a recorder either survives or quietly stops capturing
half the conversation. Two separate failures have to be caught:

* the device in use **disappears** -- the stream errors, which is easy;
* the default device for the configured role **changes** while the old one is
  still perfectly available and the stream reports nothing wrong at all.

The second is the one that needs watching, and polling catches both. Detection
is bounded at five seconds and recovery at thirty; they are different budgets
and the metadata records their sum as one gap, because that is what the person
listening actually lost.

A track that cannot be recovered ends as incomplete. It never ends the
recording: the other track keeps going, and the session finishes when the
person says so.
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
#: Recovery budget, counted from the moment the change was detected.
RECOVERY_BUDGET_S = 30.0


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

    @property
    def follows_default(self) -> bool:
        return self.policy != POLICY_PINNED


class DeviceSupervisor:
    """Watches both tracks and migrates or reopens them as needed."""

    def __init__(
        self,
        session,
        *,
        interval_s: float = CHECK_INTERVAL_S,
        recovery_budget_s: float = RECOVERY_BUDGET_S,
    ) -> None:
        self.session = session
        self.interval_s = interval_s
        self.recovery_budget_s = recovery_budget_s
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

    def _run(self) -> None:
        while not self._halt.wait(self.interval_s):
            try:
                self.check()
            except Exception as exc:  # a supervisor must not kill a recording
                self.session._warnings.append(
                    f"supervisao de dispositivos falhou: {exc}"
                )

    # -- the check -----------------------------------------------------

    def check(self) -> None:
        for watch in list(self.watches.values()):
            if watch.health is TrackHealth.INCOMPLETE:
                continue
            if watch.health is TrackHealth.RECOVERING:
                self._try_recover(watch)
                continue
            self._check_healthy(watch)

    def _check_healthy(self, watch: TrackWatch) -> None:
        stream = self.session.stream_for(watch.track)
        if stream is None:
            return

        if getattr(stream, "error", None) is not None or not stream.running:
            self._begin_recovery(watch, "o dispositivo em uso foi perdido")
            return

        if not watch.follows_default:
            return  # a pinned track never migrates, by policy

        target = self._current_default(watch)
        if target is not None and target.id != watch.endpoint_id:
            # The old device may still be perfectly available and the stream
            # perfectly healthy. Following the role means following it anyway.
            self._begin_recovery(
                watch,
                f"o padrao de {watch.role} passou a ser '{target.name}'",
                to_device=target.name,
            )

    def _current_default(self, watch: TrackWatch):
        from ..capture.devices import default_endpoint

        try:
            return default_endpoint(watch.flow, watch.role)
        except Exception:
            return None

    def _begin_recovery(self, watch: TrackWatch, reason: str, to_device: str = "") -> None:
        watch.health = TrackHealth.RECOVERING
        watch.recovering_since = time.monotonic()
        watch.reason = reason
        writer = self.session.writer_for(watch.track)
        watch.detected_at_ms = writer.timeline_ms if writer is not None else 0
        self.session._warnings.append(
            f"trilha '{watch.track}': {reason}; recuperando por ate "
            f"{self.recovery_budget_s:.0f}s"
        )
        # Try immediately: a headset that is already present recovers on the
        # first attempt, and waiting a whole interval would widen the gap for
        # no reason.
        self._try_recover(watch, to_device=to_device)

    def _try_recover(self, watch: TrackWatch, to_device: str = "") -> None:
        from ..capture.devices import FLOW_RENDER, resolve_endpoint
        from ..capture.stream import CaptureStream

        elapsed = time.monotonic() - watch.recovering_since
        try:
            endpoint = resolve_endpoint(
                flow=watch.flow,
                policy_pinned_id=watch.pinned_id if not watch.follows_default else "",
                role=watch.role,
            )
            stream = CaptureStream(
                endpoint.id, loopback=(watch.flow == FLOW_RENDER), name=watch.track
            )
            stream.start()
        except Exception as exc:
            if elapsed >= self.recovery_budget_s:
                self._give_up(watch, str(exc))
            return

        writer = self.session.writer_for(watch.track)
        started_ms = watch.detected_at_ms
        if writer is not None:
            writer.rebind(stream.format)
        self.session.replace_stream(watch.track, stream)

        previous_name = watch.endpoint_name
        watch.endpoint_id = endpoint.id
        watch.endpoint_name = endpoint.name
        watch.health = TrackHealth.RECORDING
        watch.gaps.append(DeviceGap(
            track=watch.track,
            started_ms=started_ms,
            # Detection and recovery are different budgets; what the listener
            # lost is their sum, recorded as one gap.
            duration_ms=int((elapsed + self.interval_s) * 1000),
            reason=watch.reason,
            from_device=previous_name,
            to_device=to_device or endpoint.name,
        ))
        self.session._warnings.append(
            f"trilha '{watch.track}': gravando agora em '{endpoint.name}'"
        )

    def _give_up(self, watch: TrackWatch, detail: str) -> None:
        """End one track. Never the recording."""
        watch.health = TrackHealth.INCOMPLETE
        watch.gaps.append(DeviceGap(
            track=watch.track,
            started_ms=watch.detected_at_ms,
            duration_ms=0,
            reason=f"{watch.reason}; nao recuperada em "
                   f"{self.recovery_budget_s:.0f}s ({detail})",
            from_device=watch.endpoint_name,
        ))
        self.session._warnings.append(
            f"trilha '{watch.track}' encerrada como incompleta: {watch.reason}. "
            f"A gravacao continua na outra trilha."
        )
        self.session.end_track(watch.track)

    # -- reporting -----------------------------------------------------

    def gaps(self) -> list[dict]:
        return [
            gap.as_dict()
            for watch in self.watches.values()
            for gap in watch.gaps
        ]

    def health(self) -> dict[str, str]:
        return {track: str(w.health) for track, w in self.watches.items()}
