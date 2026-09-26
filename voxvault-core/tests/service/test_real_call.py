"""What the evening of 25/09 asked of the service.

A Teams call on a Bluetooth headset was recorded without the other side: the
call played through the headset's hands-free output while the system track
recorded the communications default, the headset's silent stereo output.
Later the headset dropped, and ending the recording failed halfway and left
the meeting "recording" -- with nothing in the log to say any of it.
"""

from __future__ import annotations

import re
import wave
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from voxvault.config import Config
from voxvault.service import ResidentService, ServiceBusy, log_line
from voxvault.types import MeetingState


@pytest.fixture
def service(tmp_path: Path) -> ResidentService:
    return ResidentService(Config(data_dir=tmp_path))


def test_the_system_track_starts_on_the_call_output_of_a_headset(
    service: ResidentService, monkeypatch
) -> None:
    """Scenario: Chamada num headset Bluetooth."""
    from voxvault.capture import devices, stream

    microphone = SimpleNamespace(id="mic-hf", name="Microfone (JBL Hands-Free)")
    stereo = SimpleNamespace(id="estereo", name="Fones de ouvido (JBL)")
    call_output = SimpleNamespace(id="hf-out", name="Alto-falantes (JBL Hands-Free)")

    monkeypatch.setattr(
        devices, "resolve_endpoint",
        lambda *, flow, policy_pinned_id="", role="": (
            microphone if flow == devices.FLOW_CAPTURE else stereo
        ),
    )
    monkeypatch.setattr(
        devices, "system_track_endpoint",
        lambda *, role, microphone_id="": (
            call_output if microphone_id == "mic-hf" else stereo
        ),
    )
    opened: list[str] = []

    class _Stream:
        def __init__(self, endpoint_id, loopback=False, name=""):
            opened.append(endpoint_id)

    monkeypatch.setattr(stream, "CaptureStream", _Stream)

    class _Session:
        def __init__(self, *args, **kwargs):
            self.warnings = []

        def start(self):
            raise RuntimeError("o teste so olha o que foi aberto")

    import voxvault.session as sessao

    monkeypatch.setattr(sessao, "RecordingSession", _Session)
    monkeypatch.setattr(type(service.pipeline), "suspend_for_recording", lambda self: 0.0)
    monkeypatch.setattr(type(service.pipeline), "resume_after_recording", lambda self: None)

    with pytest.raises(ServiceBusy):
        service.start_recording("Diaria")

    assert opened == ["mic-hf", "hf-out"], "a trilha do sistema tem de ir para a saida da chamada"


def _meeting_being_recorded(service: ResidentService, uid: str, *, seconds: float) -> Path:
    from voxvault.layout import meeting_dir
    from voxvault.session.finalize import Metadata, Step, write_metadata

    directory = meeting_dir(service.config.data_dir, uid)
    directory.mkdir(parents=True)
    with wave.open(str(directory / "mic.wav"), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16_000)
        out.writeframes(b"\x01\x00" * int(16_000 * seconds))
    write_metadata(directory, Metadata(uid=uid, step=Step.NOT_STARTED.value))
    service.store.create_meeting(
        uid=uid, title="Reuniao de 25/09", directory=directory,
        started_at=datetime(2026, 9, 25, 22, 17, tzinfo=UTC),
        state=MeetingState.RECORDING,
    )
    return directory


def test_a_stop_that_fails_still_closes_the_meeting_from_disk(
    service: ResidentService, monkeypatch, capsys
) -> None:
    """Scenario: Falha no meio do encerramento."""
    from voxvault.session.finalize import Step, reached, read_metadata

    directory = _meeting_being_recorded(service, "1896cf74aaaa", seconds=6.0)
    queued: list[str] = []
    monkeypatch.setattr(type(service.pipeline), "enqueue", lambda self, uid: queued.append(uid))

    def stop(**_):
        raise RuntimeError("quebrou no meio do encerramento")

    session = SimpleNamespace(
        uid="1896cf74aaaa", title="Reuniao de 25/09", directory=directory,
        _warnings=["trilha 'mic': o dispositivo em uso foi perdido; recuperando"],
        stop=stop,
    )
    with service._lock:
        service._session = session

    answer = service.stop_recording()

    meeting = service.store.get_meeting("1896cf74aaaa")
    assert str(meeting.state) == MeetingState.RECORDED.value, "ficou 'gravando'"
    assert abs(meeting.duration_ms - 6000) <= 50, "a duracao vem dos arquivos"
    assert queued == ["1896cf74aaaa"], "a reuniao vai para a transcricao"
    assert reached(read_metadata(directory).step, Step.QUEUED)
    assert any("encerramento falhou" in aviso for aviso in answer["avisos"])
    assert service.has_work() == (False, "")

    log = capsys.readouterr().out
    assert "[falha] 1896cf74 o encerramento da gravacao falhou" in log
    assert "Traceback" in log, "a pilha vai junto, para saber de onde veio"


def test_a_log_line_has_the_time_the_kind_and_the_meeting_but_no_title(
    service: ResidentService, capsys
) -> None:
    service._record_event("gravando", uid="abcdef1234567890", title="Diaria secreta")

    line = capsys.readouterr().out.strip()
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \[gravando\] abcdef12", line
    ), line
    assert "secreta" not in line


def test_a_log_line_survives_what_the_log_cannot_encode(capsys) -> None:
    log_line("trilha", "gravando agora em 'Áudio Remoto' \U0001f3a7", uid="u")
    assert "Remoto" in capsys.readouterr().out


def test_the_recording_view_names_what_each_track_records(service: ResidentService) -> None:
    session = SimpleNamespace(
        paused=False, uid="u1", title="t", started_at=datetime(2026, 9, 25, tzinfo=UTC),
        duration_ms=1000, drift_ms=0, warnings=[],
        track_stats=lambda: {"mic": {"duracao_ms": 1000}},
        levels=lambda: {},
        supervisor=SimpleNamespace(
            device_names=lambda: {
                "mic": "Microfone (JBL Hands-Free)",
                "system": "Alto-falantes (JBL Hands-Free)",
            },
            live_health=lambda: {"mic": "gravando", "system": "recuperando"},
        ),
    )
    with service._lock:
        service._session = session

    view = service.recording()

    assert view["dispositivos"]["system"] == "Alto-falantes (JBL Hands-Free)"
    assert view["saude"] == {"mic": "gravando", "system": "recuperando"}
