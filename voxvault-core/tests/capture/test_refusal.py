"""A capture refused to VoxVault is told apart from a broken audio system.

The corporate machine of 25/09: every capture failed Initialize with
E_INVALIDARG while playback opened -- endpoint security keeping an unknown
program off the microphone. The diagnosis said to restart the audio service.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="WASAPI so existe no Windows"
)

from voxvault.capture import refusal

REFUSED = (
    "trilha 'mic' nao pode ser aberta: IAudioClient::Initialize falhou: 0x80070057; "
    "trilha 'system' nao pode ser aberta: IAudioClient::Initialize falhou: 0x80070057"
)


@pytest.fixture
def playback(monkeypatch):
    state = {"opens": True, "asked": 0}

    def opens() -> bool:
        state["asked"] += 1
        return state["opens"]

    monkeypatch.setattr(refusal, "playback_opens", opens)
    monkeypatch.setattr(
        refusal, "executables_to_allow",
        lambda: [r"C:\Users\x\.voxvault\runtime\python\cpython-3.12\python.exe"],
    )
    return state


def test_a_refused_capture_with_working_playback_names_security_software(playback) -> None:
    text = refusal.explain(REFUSED)

    assert text is not None
    assert "software de seguranca" in text
    assert r"cpython-3.12\python.exe" in text, "diz o que a TI precisa liberar"


def test_a_broken_audio_system_is_not_blamed_on_security(playback) -> None:
    playback["opens"] = False
    assert refusal.explain(REFUSED) is None


def test_a_missing_device_is_not_a_refusal(playback) -> None:
    assert refusal.explain("dispositivo nao encontrado: {0.0.1.00000000}.{x}") is None
    assert playback["asked"] == 0, "nem chega a abrir a saida"


def test_the_diagnosis_points_at_security_software_instead_of_the_audio_service(
    playback, monkeypatch
) -> None:
    import voxvault.capture.devices as devices
    from voxvault import doctor

    monkeypatch.setattr(devices, "list_endpoints", lambda *a, **k: [
        SimpleNamespace(name="Microfone (Realtek)", flow=devices.FLOW_CAPTURE,
                        default_for=frozenset(), looks_like_headphones=False, id="m"),
        SimpleNamespace(name="Fones (JBL)", flow=devices.FLOW_RENDER,
                        default_for=frozenset({"comunicacoes", "multimidia"}),
                        looks_like_headphones=True, id="f"),
    ])
    monkeypatch.setattr(
        doctor, "_probe_open",
        lambda endpoint: (False, "IAudioClient::Initialize falhou: 0x80070057"),
    )

    item = doctor._check_capture()

    assert item.status == "falha"
    assert "software de seguranca" in item.remedy
    assert "servico de audio" not in item.remedy


def test_a_start_refused_says_why_and_what_to_allow(playback, monkeypatch, tmp_path) -> None:
    from voxvault.capture import devices, stream
    from voxvault.config import Config
    from voxvault.errors import CaptureError
    from voxvault.service import ResidentService, ServiceBusy

    service = ResidentService(Config(data_dir=tmp_path))
    endpoint = SimpleNamespace(id="x", name="Dispositivo")
    monkeypatch.setattr(devices, "resolve_endpoint", lambda **_: endpoint)
    monkeypatch.setattr(devices, "system_track_endpoint", lambda **_: endpoint)
    monkeypatch.setattr(stream, "CaptureStream", lambda *a, **k: object())

    class _Refused:
        def __init__(self, *args, **kwargs):
            self.warnings = REFUSED.split("; ")

        def start(self):
            raise CaptureError("Nenhuma trilha pode ser aberta; a gravacao nao foi iniciada.")

    import voxvault.session as sessao

    monkeypatch.setattr(sessao, "RecordingSession", _Refused)
    monkeypatch.setattr(type(service.pipeline), "suspend_for_recording", lambda self: 0.0)
    monkeypatch.setattr(type(service.pipeline), "resume_after_recording", lambda self: None)

    with pytest.raises(ServiceBusy, match="software de seguranca"):
        service.start_recording("Reuniao")


@pytest.mark.hardware
def test_playback_really_opens_here() -> None:
    """On a machine with an output: the probe itself, without faking it."""
    from voxvault.capture.devices import FLOW_RENDER, list_endpoints

    if not list_endpoints(FLOW_RENDER):
        pytest.skip("nenhuma saida de audio nesta sessao")
    assert refusal.playback_opens() is True
