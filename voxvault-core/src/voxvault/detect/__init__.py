"""Noticing that a meeting started, without watching what is in it.

The beginning of a call is where the context gets set, and it is exactly what
gets lost when somebody remembers to press record five minutes in. So the
detector watches one local signal -- which process is holding the microphone --
and nothing else.

The correlation rule is what makes it usable rather than annoying: it is not
enough that a meeting application is *running* and that *something* is
capturing. The process holding the microphone has to be the recognized
application itself. Every machine has a meeting client sitting resident all day
while some other program uses the microphone, and a detector that fired on that
would cry wolf until it was turned off.

A browser holding the microphone is treated as a weak signal: no platform is
inferred from it, and it needs a longer sustained period before it counts. The
browser is genuinely ambiguous, and guessing which site is open would mean
looking at things this detector will not look at.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from .microphone import MicrophoneUser, active_users, store_is_readable

#: Executables that are meeting clients. Matched on file name, lowercased.
#: Deliberately a list and not a guess: a name that is not here produces no
#: detection, which is the safe direction.
MEETING_APPS: frozenset[str] = frozenset({
    "teams.exe", "ms-teams.exe", "msteams.exe",
    "zoom.exe", "zoommeetings.exe",
    "webex.exe", "webexmta.exe", "atmgr.exe",
    "slack.exe",
    "discord.exe", "discordptb.exe", "discordcanary.exe",
    "skype.exe", "lync.exe",
    "gotomeeting.exe", "g2mcomm.exe",
    "bluejeans.exe", "whereby.exe", "ringcentral.exe",
    "meet.exe", "chime.exe", "amazonchime.exe",
})

#: Packaged applications, matched on family name prefix.
MEETING_PACKAGES: tuple[str, ...] = (
    "MicrosoftTeams", "MSTeams", "5319275A.WhatsAppDesktop", "ZoomVideo",
)

#: Browsers. A meeting may well be running in one, but which one is unknowable
#: without looking at things this detector will not look at.
BROWSERS: frozenset[str] = frozenset({
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
    "opera.exe", "opera_gx.exe", "vivaldi.exe", "arc.exe", "thorium.exe",
})

#: Defaults from the specification.
SUSTAIN_APP_S = 20.0
SUSTAIN_BROWSER_S = 60.0
CESSATION_S = 60.0


class Signal(StrEnum):
    NONE = "nenhum"
    APP = "aplicativo_reconhecido"
    BROWSER = "navegador"


@dataclass(frozen=True, slots=True)
class Candidate:
    """A process holding the microphone that might mean a meeting."""

    user: MicrophoneUser
    signal: Signal

    @property
    def sustain_s(self) -> float:
        return SUSTAIN_BROWSER_S if self.signal is Signal.BROWSER else SUSTAIN_APP_S

    @property
    def name(self) -> str:
        return self.user.describe


def classify(user: MicrophoneUser, excluded: frozenset[str] = frozenset()) -> Signal:
    """What, if anything, this microphone holder means."""
    executable = user.executable.lower()
    if executable in excluded or user.identity in excluded:
        return Signal.NONE
    if executable in MEETING_APPS:
        return Signal.APP
    if user.packaged and any(
        user.identity.startswith(prefix) for prefix in MEETING_PACKAGES
    ):
        return Signal.APP
    if executable in BROWSERS:
        return Signal.BROWSER
    return Signal.NONE


def candidates(
    users: list[MicrophoneUser] | None = None,
    *,
    excluded: frozenset[str] = frozenset(),
) -> list[Candidate]:
    """The microphone holders that are recognized, correlated by construction.

    Correlation is not a separate check here: the only thing examined is the
    process that actually holds the microphone. A meeting client merely running
    never appears, which is exactly the false positive the requirement names.
    """
    found = active_users() if users is None else users
    out = []
    for user in found:
        signal = classify(user, excluded)
        if signal is not Signal.NONE:
            out.append(Candidate(user=user, signal=signal))
    return out


@dataclass(slots=True)
class Detection:
    """A meeting the detector believes has started."""

    name: str
    signal: Signal
    since: float
    weak: bool

    def as_dict(self) -> dict:
        return {
            "aplicativo": self.name,
            "sinal": str(self.signal),
            "fraco": self.weak,
            "sustentado_ha_s": round(time.monotonic() - self.since, 1),
        }


class MeetingDetector:
    """Turns a momentary signal into a sustained one.

    A microphone touched for three seconds is somebody testing their headset.
    The sustained period is what separates that from a meeting, and it is
    longer for a browser because the browser says less.
    """

    def __init__(
        self,
        *,
        sustain_app_s: float = SUSTAIN_APP_S,
        sustain_browser_s: float = SUSTAIN_BROWSER_S,
        cessation_s: float = CESSATION_S,
        excluded: frozenset[str] = frozenset(),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sustain_app_s = sustain_app_s
        self.sustain_browser_s = sustain_browser_s
        self.cessation_s = cessation_s
        self.excluded = excluded
        self._clock = clock
        #: identity -> instant the signal was first seen continuously
        self._seen: dict[str, float] = {}
        self._detected: Detection | None = None
        self._gone_since: float | None = None

    # -- observation ---------------------------------------------------

    def observe(self, users: list[MicrophoneUser] | None = None) -> Detection | None:
        """Feed one sample. Returns the current detection, if any."""
        now = self._clock()
        present = candidates(users, excluded=self.excluded)
        identities = {c.user.identity for c in present}

        # Anything that went away loses its accumulated time: the period has
        # to be continuous, or a microphone touched once a minute for an hour
        # would eventually look like a meeting.
        for identity in list(self._seen):
            if identity not in identities:
                del self._seen[identity]

        for candidate in present:
            self._seen.setdefault(candidate.user.identity, now)

        if self._detected is not None:
            return self._track_ongoing(present, now)

        for candidate in present:
            first_seen = self._seen[candidate.user.identity]
            needed = (
                self.sustain_browser_s if candidate.signal is Signal.BROWSER
                else self.sustain_app_s
            )
            if now - first_seen >= needed:
                self._detected = Detection(
                    name=candidate.name,
                    signal=candidate.signal,
                    since=first_seen,
                    weak=candidate.signal is Signal.BROWSER,
                )
                self._gone_since = None
                return self._detected
        return None

    def _track_ongoing(self, present: list[Candidate], now: float) -> Detection | None:
        still_here = any(c.name == self._detected.name for c in present)
        if still_here:
            self._gone_since = None
            return self._detected
        if self._gone_since is None:
            self._gone_since = now
            return self._detected
        if now - self._gone_since >= self.cessation_s:
            self._detected = None
            self._gone_since = None
            return None
        return self._detected

    # -- state ---------------------------------------------------------

    @property
    def detection(self) -> Detection | None:
        return self._detected

    @property
    def ended(self) -> bool:
        """True once a previously detected meeting has ceased for long enough."""
        return self._detected is None

    def reset(self) -> None:
        self._seen.clear()
        self._detected = None
        self._gone_since = None

    def available(self) -> tuple[bool, str]:
        """Whether this machine can answer the question at all."""
        if not store_is_readable():
            return False, (
                "o Windows nao expoe o registro de uso de microfone nesta "
                "maquina; a deteccao de reuniao fica indisponivel"
            )
        return True, ""


__all__ = [
    "BROWSERS",
    "CESSATION_S",
    "MEETING_APPS",
    "SUSTAIN_APP_S",
    "SUSTAIN_BROWSER_S",
    "Candidate",
    "Detection",
    "MeetingDetector",
    "MicrophoneUser",
    "Signal",
    "active_users",
    "candidates",
    "classify",
]
