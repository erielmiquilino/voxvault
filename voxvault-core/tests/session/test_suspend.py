"""Surviving the machine going to sleep mid-recording.

Windows gives about two seconds' warning. A full finalization compresses the
whole recording losslessly and does not fit in that, so the only correct
reaction is the *durable minimum*: stop, flush, close, write the metadata.
Attempting more would lose the meeting rather than save it.

What makes the budget reachable at all is the flush cadence -- at most a couple
of seconds of audio is ever pending -- so these measure the real thing rather
than asserting the constant.
"""

from __future__ import annotations

import json
import struct
import time
import wave
from pathlib import Path

import pytest

from voxvault.capture.format import StreamFormat
from voxvault.config import Config
from voxvault.session import RecordingSession
from voxvault.session.finalize import Step, finalize_session, pending_finalizations, read_metadata
from voxvault.service.power import DURABLE_MINIMUM_MS, PowerWatcher
from voxvault.types import CapturePacket

# tests/session is not a package, so this resolves through the directory
# pytest puts on sys.path for this file.
from test_session import FakeStream, _wait_for


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path, flush_interval_s=0.05)


@pytest.fixture
def recording(config: Config):
    mic, system = FakeStream(), FakeStream()
    session = RecordingSession(config, uid="r1", title="Reuniao longa",
                               mic_stream=mic, system_stream=system)
    session.start()
    mic.feed(200)
    system.feed(200)
    assert _wait_for(lambda: session.duration_ms >= 1500)
    return session, mic, system


def test_the_durable_minimum_fits_the_budget(recording) -> None:
    session, _mic, _system = recording

    elapsed_ms = session.durable_minimum()

    assert elapsed_ms <= DURABLE_MINIMUM_MS, (
        f"o minimo duravel levou {elapsed_ms:.0f} ms, acima do teto de "
        f"{DURABLE_MINIMUM_MS} ms"
    )


def test_the_audio_is_readable_afterwards(recording) -> None:
    """What was captured has to survive; that is the whole point."""
    session, _mic, _system = recording
    session.durable_minimum()

    for name in ("mic.wav", "system.wav"):
        path = session.directory / name
        with wave.open(str(path)) as handle:
            assert handle.getnframes() > 0


def test_no_compression_happens(recording) -> None:
    """Compression is what does not fit in the budget."""
    session, _mic, _system = recording
    session.durable_minimum()

    assert not list(session.directory.glob("*.flac"))
    assert list(session.directory.glob("*.wav"))


def test_nothing_is_queued_or_exported(recording) -> None:
    session, _mic, _system = recording
    session.durable_minimum()

    assert not (session.directory / "transcricao.md").exists()
    metadata = read_metadata(session.directory)
    assert metadata.step == Step.FILES_CLOSED.value, (
        "declarar um passo alem do que rodou faria a recuperacao pular etapas"
    )


def test_the_metadata_records_the_real_duration_and_the_reason(recording) -> None:
    session, _mic, _system = recording
    duration_before = session.duration_ms
    session.durable_minimum()

    metadata = read_metadata(session.directory)
    assert metadata.duration_ms >= duration_before - 100
    assert metadata.ended_at
    assert any("suspensao" in w for w in metadata.warnings)


def test_the_session_is_left_as_pending_finalization(recording, config) -> None:
    """Scenario: Suspensao com processo encerrado."""
    session, _mic, _system = recording
    session.durable_minimum()

    pending = pending_finalizations(config.data_dir)
    assert [p.name for p in pending] == ["r1"]


def test_the_remaining_steps_complete_on_resume(recording, config) -> None:
    """Scenario: Suspensao com processo sobrevivente."""
    session, _mic, _system = recording
    session.durable_minimum()

    submitted: list[str] = []
    metadata = read_metadata(session.directory)
    finalize_session(
        session.directory,
        tracks=list(metadata.tracks),
        duration_ms=metadata.duration_ms,
        ended_at=metadata.ended_at,
        compress=False,
        submit=lambda: submitted.append("r1"),
    )

    assert submitted == ["r1"]
    assert read_metadata(session.directory).step == Step.QUEUED.value


def test_calling_it_twice_is_harmless(recording) -> None:
    session, _mic, _system = recording
    first = session.durable_minimum()
    second = session.durable_minimum()

    assert first > 0
    assert second == 0.0, "a segunda chamada nao refaz nada"


def test_stopping_after_a_suspend_does_not_reopen_anything(recording) -> None:
    """Scenario: Retomada apos suspensao -- nada e reaberto."""
    session, _mic, _system = recording
    session.durable_minimum()

    report = session.stop(compress=False)
    assert report.duration_ms > 0
    assert session._finished is True


# -- the notification itself -------------------------------------------

def test_the_watcher_reports_failure_instead_of_raising() -> None:
    """A machine that cannot deliver power notices is still worth recording on."""
    watcher = PowerWatcher(on_suspend=lambda: None)
    started = watcher.start()
    try:
        assert isinstance(started, bool)
        if not started:
            assert watcher.failure, "uma falha tem de ser explicada"
    finally:
        watcher.stop()


def test_a_suspend_notice_is_handled_once() -> None:
    """The operating system may repeat the notice; the work must not repeat."""
    from voxvault.service.power import PBT_APMRESUMESUSPEND, PBT_APMSUSPEND

    calls: list[str] = []
    watcher = PowerWatcher(
        on_suspend=lambda: calls.append("suspend"),
        on_resume=lambda: calls.append("resume"),
    )

    watcher._dispatch(None, PBT_APMSUSPEND, None)
    watcher._dispatch(None, PBT_APMSUSPEND, None)
    watcher._dispatch(None, PBT_APMRESUMESUSPEND, None)
    watcher._dispatch(None, PBT_APMRESUMESUSPEND, None)

    assert calls == ["suspend", "resume"]


def test_a_failing_callback_never_escapes_into_the_operating_system() -> None:
    """Raising back into the OS mid-suspend would take the process down with a
    recording open."""
    from voxvault.service.power import PBT_APMSUSPEND

    def explode() -> None:
        raise RuntimeError("falhou")

    watcher = PowerWatcher(on_suspend=explode)
    assert watcher._dispatch(None, PBT_APMSUSPEND, None) == 0
