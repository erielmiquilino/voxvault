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

import threading
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
    def __init__(self, identifier: str, name: str, *, call: bool = False) -> None:
        self.id = identifier
        self.name = name
        self.is_call_endpoint = call


class FakeStream:
    def __init__(self, endpoint_id: str = "dev-1", *, broken: bool = False,
                 hang: threading.Event | None = None) -> None:
        self.endpoint_id = endpoint_id
        self.format = FORMAT
        self.error = RuntimeError("dispositivo sumiu") if broken else None
        self.running = not broken
        self.stopped = False
        self._hang = hang

    def start(self) -> int:
        if self._hang is not None:
            self._hang.wait(10)  # a device halfway through reconnecting
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

    def _warn(self, message):
        self._warnings.append(message)


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
        # The call output of the headset whose microphone is recorded, while
        # a call is on; and the endpoints whose opening hangs.
        "call_output": None,
        "hang": {},
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

    def fake_system_target(*, role, microphone_id=""):
        if state["call_output"] is not None and microphone_id:
            return state["call_output"]
        return fake_resolve(flow="saida", role=role)

    def fake_stream(endpoint_id, *, loopback=False, name=""):
        stream = FakeStream(endpoint_id, hang=state["hang"].get(endpoint_id))
        state["opened"].append(endpoint_id)
        return stream

    import voxvault.capture.devices as devices_module
    import voxvault.capture.stream as stream_module

    monkeypatch.setattr(devices_module, "default_endpoint", fake_default)
    monkeypatch.setattr(devices_module, "resolve_endpoint", fake_resolve)
    monkeypatch.setattr(devices_module, "system_track_endpoint", fake_system_target)
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
    assert session._writers["system"].rebound_to is None, (
        "quem troca o formato do escritor e a sessao, na thread de escrita"
    )


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


def test_a_track_without_a_device_past_the_budget_is_marked_and_kept(setup) -> None:
    session = FakeSession(
        {"system": FakeStream("dev-1", broken=True), "mic": FakeStream("mic-1")}
    )
    supervisor = DeviceSupervisor(session, recovery_budget_s=0.0)
    supervisor.watch(_watch())
    supervisor.watch(_watch(track="mic", flow="entrada", endpoint_id="mic-1"))

    setup["open_fails"] = 99
    supervisor.check()

    assert supervisor.health()["system"] == "incompleta"
    assert supervisor.watches["system"].health is TrackHealth.RECOVERING
    assert session.ended == [], "a trilha nao e encerrada: continua sendo tentada"
    assert any("incompleta" in w and "continuam" in w for w in session._warnings)
    # The whole point: the other track carries on and the recording lives.
    assert "mic" in session._streams
    assert supervisor.watches["mic"].health is TrackHealth.RECORDING


def test_a_headset_that_comes_back_after_the_budget_is_recorded_again(setup) -> None:
    """Scenario: Headset que volta depois do prazo -- the evening of 25/09."""
    session = FakeSession({"mic": FakeStream("mic-1", broken=True)})
    supervisor = DeviceSupervisor(session, recovery_budget_s=0.0, retry_interval_s=0.0)
    supervisor.watch(_watch(track="mic", flow="entrada", endpoint_id="mic-1",
                            endpoint_name="JBL Hands-Free"))

    setup["open_fails"] = 3
    for _ in range(3):
        supervisor.check()
        assert supervisor.watches["mic"].health is TrackHealth.RECOVERING

    supervisor.check()  # Bluetooth restarted, the headset is back

    watch = supervisor.watches["mic"]
    assert watch.health is TrackHealth.RECORDING
    assert session._streams["mic"].endpoint_id == "mic-1"
    assert supervisor.health()["mic"] == "incompleta", "o buraco passou do prazo"
    assert len(supervisor.gaps()) == 1, "da perda a volta, uma unica lacuna"


def test_a_hanging_open_does_not_hold_up_the_other_track(setup) -> None:
    """Scenario: Abertura que demora numa trilha."""
    hang = threading.Event()
    setup["hang"]["dev-1"] = hang
    session = FakeSession({
        "system": FakeStream("dev-1", broken=True),
        "mic": FakeStream("mic-1", broken=True),
    })
    supervisor = DeviceSupervisor(session, attempt_wait_s=0.1)
    supervisor.watch(_watch())
    supervisor.watch(_watch(track="mic", flow="entrada", endpoint_id="mic-1"))

    began = time.monotonic()
    try:
        supervisor.check()
        took = time.monotonic() - began

        assert took < 1.0, f"a verificacao esperou a abertura travada ({took:.1f}s)"
        assert supervisor.watches["system"].health is TrackHealth.RECOVERING
        assert supervisor.watches["mic"].health is TrackHealth.RECORDING
    finally:
        hang.set()
        supervisor.stop()


def test_the_live_health_tells_a_lost_device_from_a_quiet_one(setup) -> None:
    """What a meter shows: recovering, then past the budget -- while the
    metadata keeps "incompleta" for a track that came back."""
    session = FakeSession({"system": FakeStream("dev-1", broken=True)})
    supervisor = DeviceSupervisor(session, recovery_budget_s=0.0, retry_interval_s=0.0)
    supervisor.watch(_watch())
    setup["open_fails"] = 1

    supervisor.check()
    assert supervisor.live_health() == {"system": "sem_dispositivo"}

    supervisor.check()  # the device is back
    assert supervisor.live_health() == {"system": "gravando"}
    assert supervisor.health() == {"system": "incompleta"}


def test_a_quiet_output_is_live_and_healthy(setup) -> None:
    session = FakeSession({"system": FakeStream("dev-1")})
    supervisor = DeviceSupervisor(session)
    supervisor.watch(_watch())

    supervisor.check()

    assert supervisor.live_health() == {"system": "gravando"}


# -- the call output of a headset -------------------------------------

def test_the_system_track_moves_to_the_call_output_when_a_call_starts(setup) -> None:
    """Scenario: Chamada que comeca depois da gravacao."""
    session = FakeSession({"system": FakeStream("dev-1"), "mic": FakeStream("mic-hf")})
    supervisor = DeviceSupervisor(session)
    supervisor.watch(_watch(endpoint_name="Fones de ouvido (JBL)"))
    supervisor.watch(_watch(track="mic", flow="entrada", endpoint_id="mic-hf"))

    setup["default_entrada"] = FakeEndpoint("mic-hf", "Microfone (JBL Hands-Free)")
    setup["call_output"] = FakeEndpoint(
        "hf-out", "Alto-falantes (JBL Hands-Free)", call=True
    )
    supervisor.check()

    watch = supervisor.watches["system"]
    assert watch.endpoint_id == "hf-out"
    assert watch.on_call_output
    assert "chamada" in supervisor.gaps()[0]["motivo"]


def test_the_system_track_returns_to_the_default_when_the_call_ends(setup) -> None:
    session = FakeSession({"system": FakeStream("dev-1"), "mic": FakeStream("mic-hf")})
    supervisor = DeviceSupervisor(session)
    supervisor.watch(_watch(endpoint_name="Fones de ouvido (JBL)"))
    supervisor.watch(_watch(track="mic", flow="entrada", endpoint_id="mic-hf"))
    setup["default_entrada"] = FakeEndpoint("mic-hf", "Microfone (JBL Hands-Free)")
    setup["call_output"] = FakeEndpoint(
        "hf-out", "Alto-falantes (JBL Hands-Free)", call=True
    )
    supervisor.check()

    setup["call_output"] = None  # the call ended
    supervisor.check()

    watch = supervisor.watches["system"]
    assert watch.endpoint_id == "dev-1"
    assert not watch.on_call_output
    assert "deixou de estar ativa" in supervisor.gaps()[-1]["motivo"]


def test_a_pinned_system_track_ignores_the_call_output(setup) -> None:
    session = FakeSession({"system": FakeStream("dev-1"), "mic": FakeStream("mic-hf")})
    supervisor = DeviceSupervisor(session)
    supervisor.watch(_watch(policy=POLICY_PINNED))
    supervisor.watch(_watch(track="mic", flow="entrada", endpoint_id="mic-hf"))
    setup["call_output"] = FakeEndpoint("hf-out", "Alto-falantes (Hands-Free)", call=True)

    supervisor.check()

    assert supervisor.watches["system"].endpoint_id == "dev-1"


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


def test_a_track_never_recovered_is_recorded_as_one_gap_to_the_end(setup) -> None:
    session = FakeSession({"system": FakeStream("dev-1", broken=True)})
    supervisor = DeviceSupervisor(session, recovery_budget_s=0.0)
    supervisor.watch(_watch())
    setup["open_fails"] = 99

    supervisor.check()
    supervisor.stop()  # the person ends the recording

    gaps = supervisor.gaps()
    assert len(gaps) == 1
    assert "nao recuperada ate o fim" in gaps[0]["motivo"]
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


# -- the case that lost a real meeting ---------------------------------

class CountingStream(FakeStream):
    """A stream that reports how many packets it has delivered.

    A Bluetooth headset whose battery dies does not always surface as an
    error: the stream stays "running" and the count simply stops moving.
    """

    def __init__(self, endpoint_id: str = "mic-1") -> None:
        super().__init__(endpoint_id)
        self.packets_captured = 0

    def deliver(self, count: int = 10) -> None:
        self.packets_captured += count


def test_a_microphone_that_stops_delivering_is_treated_as_lost(setup, monkeypatch) -> None:
    """The headset battery died at 38 minutes; nothing errored and nothing
    moved to the next device. A microphone is never silent at the packet
    level while it is alive, so a count that stops moving is a lost device."""
    import voxvault.session.supervisor as module

    clock = {"now": 1000.0}
    monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])

    stream = CountingStream("mic-1")
    session = FakeSession({"mic": stream})
    supervisor = DeviceSupervisor(session)
    supervisor.watch(_watch(track="mic", flow="entrada", endpoint_id="mic-1",
                            endpoint_name="JBL Tune Flex 2"))

    stream.deliver()
    supervisor.check()                      # first sight: remember the count
    clock["now"] += 2
    stream.deliver()
    supervisor.check()                      # still moving: healthy
    assert setup["opened"] == []

    clock["now"] += module.STALL_S + 1      # the battery dies here
    supervisor.check()

    assert setup["opened"] == ["mic-1"], "a trilha tem de ser reaberta"
    assert any("parou de entregar" in w for w in session._warnings)


def test_a_silent_loopback_is_never_a_stall(setup, monkeypatch) -> None:
    """Nothing playing is the normal state of the system track."""
    import voxvault.session.supervisor as module

    clock = {"now": 1000.0}
    monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])

    stream = CountingStream("dev-1")
    session = FakeSession({"system": stream})
    supervisor = DeviceSupervisor(session)
    supervisor.watch(_watch())

    supervisor.check()
    clock["now"] += 600                     # ten minutes of nobody talking
    supervisor.check()

    assert setup["opened"] == []
    assert supervisor.watches["system"].health is TrackHealth.RECORDING
