"""Noticing a meeting without crying wolf.

The rule that decides whether this feature is usable or gets turned off within
a day: it is not enough that a meeting client is running and that something is
capturing. The process holding the microphone has to be the meeting client
itself. Every machine has Teams sitting resident all day while some other
program touches the microphone.

The clock is injected, so a twenty-second sustained period is tested in
microseconds and the assertions are about the rule rather than about waiting.
"""

from __future__ import annotations

import pytest

from voxvault.detect import (
    CESSATION_S,
    SUSTAIN_APP_S,
    SUSTAIN_BROWSER_S,
    MeetingDetector,
    Signal,
    candidates,
    classify,
)
from voxvault.detect.microphone import MicrophoneUser


def user(executable: str, *, packaged: bool = False, identity: str = "") -> MicrophoneUser:
    return MicrophoneUser(
        identity=identity or f"C:\\Apps\\{executable}",
        executable=executable.lower(),
        packaged=packaged,
    )


class Clock:
    """A hand-wound clock, so a minute of sustained signal costs nothing."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def detector(clock: Clock) -> MeetingDetector:
    return MeetingDetector(clock=clock)


# -- classification ----------------------------------------------------

def test_a_meeting_client_is_recognised() -> None:
    assert classify(user("Teams.exe")) is Signal.APP
    assert classify(user("zoom.exe")) is Signal.APP


def test_a_browser_is_a_weak_signal() -> None:
    assert classify(user("chrome.exe")) is Signal.BROWSER
    assert classify(user("msedge.exe")) is Signal.BROWSER


def test_anything_else_is_not_a_signal() -> None:
    assert classify(user("audacity.exe")) is Signal.NONE
    assert classify(user("obs64.exe")) is Signal.NONE


def test_an_excluded_application_never_counts() -> None:
    excluded = frozenset({"discord.exe"})
    assert classify(user("discord.exe"), excluded) is Signal.NONE
    assert classify(user("teams.exe"), excluded) is Signal.APP


def test_only_the_process_holding_the_microphone_is_examined() -> None:
    """Scenario: Aplicativo de reuniao ocioso com outro programa usando o microfone.

    Correlation is not a separate check: the only input is who actually holds
    the microphone. A meeting client merely running cannot appear here at all.
    """
    holders = [user("audacity.exe")]  # Teams is running, but is not in this list
    assert candidates(holders) == []


# -- the sustained period ----------------------------------------------

def test_a_recognised_app_detects_after_its_period(detector, clock) -> None:
    """Scenario: Reuniao iniciada numa plataforma conhecida."""
    holders = [user("teams.exe")]

    assert detector.observe(holders) is None
    clock.advance(SUSTAIN_APP_S - 1)
    assert detector.observe(holders) is None, "ainda dentro do periodo"

    clock.advance(2)
    detection = detector.observe(holders)
    assert detection is not None
    assert detection.signal is Signal.APP
    assert detection.weak is False


def test_a_momentary_touch_detects_nothing(detector, clock) -> None:
    """Scenario: Uso momentaneo do microfone."""
    holders = [user("teams.exe")]
    detector.observe(holders)
    clock.advance(5)
    detector.observe(holders)

    clock.advance(1)
    assert detector.observe([]) is None
    clock.advance(SUSTAIN_APP_S * 2)
    assert detector.observe([]) is None


def test_the_period_has_to_be_continuous(detector, clock) -> None:
    """A microphone touched once a minute must never accumulate into a meeting."""
    holders = [user("teams.exe")]
    for _ in range(5):
        detector.observe(holders)
        clock.advance(SUSTAIN_APP_S / 2)
        detector.observe([])          # gone: the accumulated time is lost
        clock.advance(1)

    assert detector.detection is None


def test_a_browser_needs_the_longer_period(detector, clock) -> None:
    """Scenario: Reuniao num navegador."""
    holders = [user("chrome.exe")]

    detector.observe(holders)
    clock.advance(SUSTAIN_APP_S + 1)
    assert detector.observe(holders) is None, (
        "o periodo do aplicativo nao pode valer para o navegador"
    )

    clock.advance(SUSTAIN_BROWSER_S)
    detection = detector.observe(holders)
    assert detection is not None
    assert detection.signal is Signal.BROWSER
    assert detection.weak is True


def test_no_platform_is_inferred_from_a_browser(detector, clock) -> None:
    holders = [user("chrome.exe")]
    detector.observe(holders)
    clock.advance(SUSTAIN_BROWSER_S + 1)
    detection = detector.observe(holders)

    # The name is the browser, never a guessed platform.
    assert "chrome" in detection.name
    assert detection.weak is True


# -- the end -----------------------------------------------------------

def test_a_meeting_stays_detected_while_the_signal_holds(detector, clock) -> None:
    holders = [user("teams.exe")]
    detector.observe(holders)
    clock.advance(SUSTAIN_APP_S + 1)
    detector.observe(holders)

    for _ in range(10):
        clock.advance(30)
        assert detector.observe(holders) is not None


def test_the_end_needs_the_cessation_period(detector, clock) -> None:
    holders = [user("teams.exe")]
    detector.observe(holders)
    clock.advance(SUSTAIN_APP_S + 1)
    assert detector.observe(holders) is not None

    detector.observe([])
    clock.advance(CESSATION_S - 5)
    assert detector.observe([]) is not None, "uma pausa curta nao encerra"

    clock.advance(10)
    assert detector.observe([]) is None
    assert detector.ended is True


def test_a_brief_dropout_does_not_end_the_meeting(detector, clock) -> None:
    """A microphone released for a second while switching devices is not the end."""
    holders = [user("teams.exe")]
    detector.observe(holders)
    clock.advance(SUSTAIN_APP_S + 1)
    detector.observe(holders)

    detector.observe([])
    clock.advance(2)
    assert detector.observe(holders) is not None
    clock.advance(CESSATION_S + 10)
    assert detector.observe(holders) is not None, "voltou antes de cessar"


# -- privacy -----------------------------------------------------------

def test_the_detector_reads_nothing_but_process_identity() -> None:
    """The licence for this feature to exist at all.

    Asserted by inspection of the source, because the guarantee is about what
    the code does *not* do and no runtime assertion can show an absence.
    """
    import inspect

    from voxvault import detect
    from voxvault.detect import microphone

    source = inspect.getsource(detect) + inspect.getsource(microphone)
    forbidden = [
        "GetWindowText", "window_title", "titulo_da_janela",
        "GetForegroundWindow", "url", "history", "historico",
        "screenshot", "BitBlt", "read_audio", "GetBuffer",
    ]
    for term in forbidden:
        assert term not in source, f"o detector nao pode tocar em {term}"


def test_availability_is_reported_rather_than_assumed(detector) -> None:
    available, reason = detector.available()
    assert isinstance(available, bool)
    if not available:
        assert reason, "a indisponibilidade tem de ser explicada"
