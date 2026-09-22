"""Surviving a device change in the middle of a recording.

Plugging in a headset is the most ordinary thing that can happen during a
meeting, and the hard half of it is that the old device stays perfectly
available and the stream reports nothing wrong: only the *default for the role*
moved. A recorder that watches for errors alone keeps capturing the device
nobody is listening through.

Everything here runs without audio hardware. The endpoints and the streams are
fakes precisely so the awkward moments -- a default that changes, a device that
comes back on the fourth try, one that never comes back -- happen on demand
instead of by luck.
"""

from __future__ import annotations

import time

import pytest

from voxvault.capture.format import StreamFormat
from voxvault.config import POLICY_FOLLOW_DEFAULT, POLICY_PINNED
from voxvault.session.supervisor import (
    DeviceSupervisor,
    TrackHealth,
    TrackWatch,
)

FORMAT = StreamFormat(
    sample_rate=48_000, channels=2, bits_per_sample=32, valid_bits=32,
    block_align=8, sample_format="float32", channel_mask=3, extensible=True,
)


class FakeEndpoint:
    def __init__(self, identifier: str, name: str) -> None:
        self.id = identifier
        self.name = name


class FakeStream:
    def __init__(self, endpoint_id: str = "dev-1", *, broken: bool = False) -> None:
        self.endpoint_id = endpoint_id
        self.format = FORMAT
        self.error = RuntimeError("dispositivo sumiu") if broken else None
        self.running = not broken
        self.stopped = False

    def start(self) -> int:
        return 0

    def stop(self) -> None:
        self.stopped = True

    def read(self):
        return []


class FakeWriter:
    def __init__(self) -> None:
        self.timeline_ms = 4000
        self.rebound_to = None

    def rebind(self, source_format):
        self.rebound_to = source_format
        return self.timeline_ms


class FakeSession:
    """Stands in for the recording, recording what was asked of it."""

    def __init__(self, streams: dict[str, FakeStream]) -> None:
        self._streams = streams
        self._writers = {track: FakeWriter() for track in streams}
        self._warnings: list[str] = []
        self.ended: list[str] = []

    def stream_for(self, track):
        return self._streams.get(track)

    def writer_for(self, track):
        return self._writers.get(track)

    def replace_stream(self, track, stream):
        self._streams[track] = stream

    def end_track(self, track):
        self.ended.append(track)
        self._streams.pop(track, None)


def _watch(policy: str = POLICY_FOLLOW_DEFAULT, **kwargs) -> TrackWatch:
    defaults = dict(
        track="system", flow="saida", policy=policy, pinned_id="dev-1",
        role="comunicacoes", endpoint_id="dev-1", endpoint_name="Alto-falantes",
    )
    defaults.update(kwargs)
    return TrackWatch(**defaults)


@pytest.fixture
def setup(monkeypatch):
    """Wires the supervisor against fake devices and returns the knobs."""
    # Defaults are per flow, as Windows reports them. A fake that ignored the
    # flow would make every input track believe the output default was its own.
    state = {
        "default": FakeEndpoint("dev-1", "Alto-falantes"),
        "default_entrada": FakeEndpoint("mic-1", "Microfone"),
        "open_fails": 0,
        "opened": [],
    }

    def _default_for(flow):
        return state["default_entrada"] if flow == "entrada" else state["default"]

    def fake_default(flow, role):
        return _default_for(flow)

    def fake_resolve(*, flow, policy_pinned_id="", role=""):
        if state["open_fails"] > 0:
            state["open_fails"] -= 1
            raise RuntimeError("dispositivo ainda indisponivel")
        if policy_pinned_id:
            return FakeEndpoint(policy_pinned_id, "Fixado")
        return _default_for(flow)

    def fake_stream(endpoint_id, *, loopback=False, name=""):
        stream = FakeStream(endpoint_id)
        state["opened"].append(endpoint_id)
        return stream

    import voxvault.capture.devices as devices_module
    import voxvault.capture.stream as stream_module

    monkeypatch.setattr(devices_module, "default_endpoint", fake_default)
    monkeypatch.setattr(devices_module, "resolve_endpoint", fake_resolve)
    monkeypatch.setattr(stream_module, "CaptureStream", fake_stream)
    return state


# -- detection ---------------------------------------------------------

def test_a_healthy_track_on_the_current_default_is_left_alone(setup) -> None:
    session = FakeSession({"system": FakeStream("dev-1")})
    supervisor = DeviceSupervisor(session)
    supervisor.watch(_watch())

    supervisor.check()

    assert supervisor.watches["system"].health is TrackHealth.RECORDING
    assert setup["opened"] == [], "nada tinha de ser reaberto"


def test_a_changed_default_migrates_even_though_nothing_errored(setup) -> None:
    """Scenario: Fone conectado muda o padrao sem desconectar o anterior.

    The stream is fine and the old device is still there. Following the role
    means following it anyway -- that is what the policy says.
    """
    session = FakeSession({"system": FakeStream("dev-1")})
    supervisor = DeviceSupervisor(session)
    supervisor.watch(_watch())

    setup["default"] = FakeEndpoint("dev-2", "Fone")
    supervisor.check()

    watch = supervisor.watches["system"]
    assert watch.health is TrackHealth.RECORDING
    assert watch.endpoint_id == "dev-2"
    assert session._streams["system"].endpoint_id == "dev-2"
    assert session._writers["system"].rebound_to is FORMAT


def test_a_lost_device_starts_recovery(setup) -> None:
    session = FakeSession({"system": FakeStream("dev-1", broken=True)})
    supervisor = DeviceSupervisor(session)
    supervisor.watch(_watch())

    supervisor.check()

    assert setup["opened"] == ["dev-1"], "a trilha tem de ser reaberta"
    assert supervisor.watches["system"].health is TrackHealth.RECORDING


def test_a_pinned_track_never_migrates(setup) -> None:
    """A pinned device is a policy, not a preference."""
    session = FakeSession({"system": FakeStream("dev-1")})
    supervisor = DeviceSupervisor(session)
    supervisor.watch(_watch(policy=POLICY_PINNED))

    setup["default"] = FakeEndpoint("dev-2", "Fone")
    supervisor.check()

    assert supervisor.watches["system"].health is TrackHealth.RECORDING
    assert setup["opened"] == []
    assert session._streams["system"].endpoint_id == "dev-1"


def test_a_pinned_track_is_reopened_when_its_device_is_lost(setup) -> None:
    session = FakeSession({"system": FakeStream("dev-1", broken=True)})
    supervisor = DeviceSupervisor(session)
    supervisor.watch(_watch(policy=POLICY_PINNED))

    supervisor.check()

    assert setup["opened"] == ["dev-1"], "reabre o fixado, nao migra para outro"


# -- recovery ----------------------------------------------------------

def test_recovery_keeps_trying_inside_the_budget(setup) -> None:
    session = FakeSession({"system": FakeStream("dev-1", broken=True)})
    supervisor = DeviceSupervisor(session, recovery_budget_s=30.0)
    supervisor.watch(_watch())

    setup["open_fails"] = 2
    supervisor.check()
    assert supervisor.watches["system"].health is TrackHealth.RECOVERING

    supervisor.check()
    assert supervisor.watches["system"].health is TrackHealth.RECOVERING

    supervisor.check()
    assert supervisor.watches["system"].health is TrackHealth.RECORDING
    assert session.ended == [], "a trilha voltou, entao nada foi encerrado"


def test_a_track_that_never_comes_back_ends_as_incomplete(setup) -> None:
    session = FakeSession(
        {"system": FakeStream("dev-1", broken=True), "mic": FakeStream("mic-1")}
    )
    supervisor = DeviceSupervisor(session, recovery_budget_s=0.0)
    supervisor.watch(_watch())
    supervisor.watch(_watch(track="mic", flow="entrada", endpoint_id="mic-1"))

    setup["open_fails"] = 99
    supervisor.check()

    assert supervisor.watches["system"].health is TrackHealth.INCOMPLETE
    assert session.ended == ["system"]
    # The whole point: the other track carries on and the recording lives.
    assert "mic" in session._streams
    assert supervisor.watches["mic"].health is TrackHealth.RECORDING


def test_an_incomplete_track_is_not_checked_again(setup) -> None:
    session = FakeSession({"system": FakeStream("dev-1", broken=True)})
    supervisor = DeviceSupervisor(session, recovery_budget_s=0.0)
    supervisor.watch(_watch())
    setup["open_fails"] = 99

    supervisor.check()
    setup["opened"].clear()
    supervisor.check()

    assert setup["opened"] == [], "uma trilha encerrada nao volta a ser tentada"


# -- what the metadata records ----------------------------------------

def test_a_migration_is_recorded_as_one_gap(setup) -> None:
    """Detection and recovery are separate budgets; the listener lost their sum."""
    session = FakeSession({"system": FakeStream("dev-1")})
    supervisor = DeviceSupervisor(session, interval_s=2.0)
    supervisor.watch(_watch())

    setup["default"] = FakeEndpoint("dev-2", "Fone")
    supervisor.check()

    gaps = supervisor.gaps()
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["trilha"] == "system"
    assert gap["inicio_ms"] == 4000, "o instante vem da posicao na linha de tempo"
    assert gap["duracao_ms"] >= 2000, "a deteccao entra na conta da lacuna"
    assert gap["de"] == "Alto-falantes"
    assert gap["para"] == "Fone"
    assert "padrao" in gap["motivo"]


def test_giving_up_is_recorded_with_its_reason(setup) -> None:
    session = FakeSession({"system": FakeStream("dev-1", broken=True)})
    supervisor = DeviceSupervisor(session, recovery_budget_s=0.0)
    supervisor.watch(_watch())
    setup["open_fails"] = 99

    supervisor.check()

    gap = supervisor.gaps()[0]
    assert "nao recuperada" in gap["motivo"]
    assert supervisor.health()["system"] == "incompleta"


def test_the_user_is_told_in_plain_words(setup) -> None:
    session = FakeSession({"system": FakeStream("dev-1")})
    supervisor = DeviceSupervisor(session)
    supervisor.watch(_watch())

    setup["default"] = FakeEndpoint("dev-2", "Fone")
    supervisor.check()

    assert any("Fone" in w for w in session._warnings)


def test_a_failing_check_never_kills_the_recording(setup, monkeypatch) -> None:
    session = FakeSession({"system": FakeStream("dev-1")})
    supervisor = DeviceSupervisor(session, interval_s=0.05)
    supervisor.watch(_watch())

    def explode() -> None:
        raise RuntimeError("enumeracao falhou")

    monkeypatch.setattr(supervisor, "check", explode)
    supervisor.start()
    try:
        time.sleep(0.3)
        assert any("supervisao" in w for w in session._warnings)
    finally:
        supervisor.stop()
