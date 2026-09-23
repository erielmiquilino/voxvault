"""The resident service: discovery, exclusivity, and what keeps it alive.

The behaviours here are the ones that lose work when they are wrong. A
rendezvous pointing at a dead process makes every surface wait for an answer
that is not coming; exclusivity scoped too narrowly lets two processes reach
for the same microphone; and a service that agrees to shut down while a queue
is pending throws away transcriptions nobody asked it to drop.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from voxvault.config import Config
from voxvault.service import (
    ResidentService,
    ServiceBusy,
    _shutdown_if_idle,
    exclusivity,
    rendezvous,
)

# -- rendezvous --------------------------------------------------------

@pytest.fixture
def ponto(tmp_path: Path) -> Path:
    return tmp_path / "servico.json"


def test_a_rendezvous_round_trips(ponto: Path) -> None:
    original = rendezvous.Rendezvous(
        endereco="127.0.0.1:1234", segredo="s3gr3d0",
        pid=os.getpid(), diretorio_de_dados=r"D:\VoxVault",
    )
    rendezvous.publish(original, ponto)
    back = rendezvous.read(ponto)

    assert back is not None
    assert back.endereco == "127.0.0.1:1234"
    assert back.segredo == "s3gr3d0"
    assert back.diretorio_de_dados == r"D:\VoxVault"


def test_the_file_is_written_whole_or_not_at_all(ponto: Path) -> None:
    """A client reading half a rendezvous would connect to nothing."""
    rendezvous.publish(
        rendezvous.Rendezvous("127.0.0.1:1", "x", os.getpid(), "d"), ponto
    )
    assert not list(ponto.parent.glob("*.tmp"))
    assert json.loads(ponto.read_text(encoding="utf-8"))["endereco"] == "127.0.0.1:1"


def test_a_missing_or_unreadable_file_reads_as_absent(ponto: Path) -> None:
    assert rendezvous.read(ponto) is None
    ponto.write_text("{ nao e json", encoding="utf-8")
    assert rendezvous.read(ponto) is None


def test_a_rendezvous_for_a_dead_process_is_not_live(ponto: Path) -> None:
    """A file outlives a process killed abruptly; trusting it blindly means
    waiting forever for a service that is gone."""
    rendezvous.publish(
        # A pid that cannot be running: the kernel never assigns this one.
        rendezvous.Rendezvous("127.0.0.1:1", "x", 999_999_999, "d"), ponto
    )
    assert rendezvous.read(ponto) is not None
    assert rendezvous.live_rendezvous(ponto) is None


def test_our_own_process_reads_as_live(ponto: Path) -> None:
    rendezvous.publish(
        rendezvous.Rendezvous("127.0.0.1:1", "x", os.getpid(), "d"), ponto
    )
    assert rendezvous.live_rendezvous(ponto) is not None


def test_withdrawing_is_safe_when_there_is_nothing_there(ponto: Path) -> None:
    rendezvous.withdraw(ponto)  # must not raise
    rendezvous.publish(rendezvous.Rendezvous("a", "b", 1, "c"), ponto)
    rendezvous.withdraw(ponto)
    assert not ponto.exists()


def test_secrets_differ_between_starts() -> None:
    """The secret exists so a stale client cannot drive a later service."""
    assert rendezvous.mint_secret() != rendezvous.mint_secret()
    assert len(rendezvous.mint_secret()) >= 32


def test_the_rendezvous_sits_beside_the_configuration() -> None:
    """Every surface has to find a service somebody else started, and only one
    of them knows the data directory."""
    from voxvault.config import user_config_path

    assert rendezvous.rendezvous_path().parent == user_config_path().parent


# -- exclusivity -------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="exclusividade por objeto nomeado do Windows")
def test_a_second_claim_on_the_same_name_fails_fast() -> None:
    name = f"Global\\VoxVault.Teste.{os.getpid()}"
    first = exclusivity.acquire(name)
    try:
        assert first.held is True
        began = time.monotonic()
        second = exclusivity.acquire(name)
        assert second.held is False, "duas gravacoes simultaneas seriam possiveis"
        assert time.monotonic() - began < 1.0, (
            "a segunda tentativa tem de falhar de imediato, nao esperar a primeira"
        )
    finally:
        first.release()


@pytest.mark.skipif(os.name != "nt", reason="exclusividade por objeto nomeado do Windows")
def test_releasing_lets_the_next_one_in() -> None:
    name = f"Global\\VoxVault.Teste.Liberacao.{os.getpid()}"
    first = exclusivity.acquire(name)
    assert first.held
    first.release()

    second = exclusivity.acquire(name)
    try:
        assert second.held is True
    finally:
        second.release()


def test_the_conflict_message_explains_the_scope() -> None:
    """Scoping to the data directory was the mistake; the message says why."""
    message = exclusivity.describe_conflict()
    assert "maquina" in message
    assert "diretorio de dados" in message


# -- staying alive -----------------------------------------------------

@pytest.fixture
def service(tmp_path: Path) -> ResidentService:
    return ResidentService(Config(data_dir=tmp_path))


def test_a_recent_client_counts_as_busy(service: ResidentService) -> None:
    service.touch()
    assert service.busy() is True


def test_nothing_happening_is_not_busy(service: ResidentService) -> None:
    from voxvault.service import CLIENT_PRESENCE_S

    service._last_client_seen = time.monotonic() - CLIENT_PRESENCE_S - 1
    assert service.busy() is False


@pytest.fixture
def quick_clocks(monkeypatch):
    """The production ratios -- presence window well above the check period --
    shrunk so the real supervision loop runs in a fraction of a second."""
    import voxvault.service as module

    monkeypatch.setattr(module, "SUPERVISION_INTERVAL_S", 0.02)
    monkeypatch.setattr(module, "CLIENT_PRESENCE_S", 0.3)
    monkeypatch.setattr(module, "IDLE_SHUTDOWN_S", 0.6)


def _supervise_until_stop(service: ResidentService, timeout: float) -> float | None:
    """Run the real loop; return how long it took to end the service, or None."""
    stopped = threading.Event()
    service.stop = stopped.set
    started = time.monotonic()
    threading.Thread(target=service._supervise, daemon=True).start()
    if not stopped.wait(timeout):
        service._halt.set()
        return None
    return time.monotonic() - started


def test_one_client_touching_once_does_not_keep_the_service_forever(
    service: ResidentService, monkeypatch, quick_clocks
) -> None:
    """The bug this guards: the loop refreshed "last client seen" whenever a
    client had been seen recently, so every check re-armed the presence
    window it was checking. A service one client had ever touched never ended
    -- measured on this machine, still resident 38 minutes into doing nothing.
    """
    monkeypatch.setattr(type(service.pipeline), "pending", lambda self: [])
    service.touch()

    took = _supervise_until_stop(service, timeout=3.0)

    assert took is not None, "servico tocado uma vez nunca encerrou por ociosidade"
    assert took >= 0.5, "encerrou antes do prazo de ociosidade"


def test_work_holds_the_idle_clock_until_it_ends(
    service: ResidentService, monkeypatch, quick_clocks
) -> None:
    queue = ["uma reuniao"]
    monkeypatch.setattr(type(service.pipeline), "pending", lambda self: list(queue))
    threading.Timer(0.5, queue.clear).start()

    took = _supervise_until_stop(service, timeout=3.0)

    assert took is not None
    # The clock starts when the queue empties, not when the loop started.
    assert took >= 0.5 + 0.6 - 0.1


def test_an_active_recording_counts_as_busy(service: ResidentService) -> None:
    from voxvault.service import CLIENT_PRESENCE_S

    service._last_client_seen = time.monotonic() - CLIENT_PRESENCE_S - 1
    service._session = object()  # stands in for a live session
    try:
        assert service.busy() is True
    finally:
        service._session = None


def test_a_pending_queue_counts_as_busy(service: ResidentService, monkeypatch) -> None:
    from voxvault.service import CLIENT_PRESENCE_S

    service._last_client_seen = time.monotonic() - CLIENT_PRESENCE_S - 1
    monkeypatch.setattr(
        type(service.pipeline), "pending", lambda self: ["uma reuniao"]
    )
    assert service.busy() is True


def test_shutdown_is_refused_while_a_recording_is_open(
    service: ResidentService,
) -> None:
    service._session = object()
    try:
        with pytest.raises(ServiceBusy, match="gravacao em andamento"):
            _shutdown_if_idle(service)
    finally:
        service._session = None


def test_shutdown_is_refused_while_the_queue_is_not_empty(
    service: ResidentService, monkeypatch
) -> None:
    """An app that could end the service at will would silently drop a queue."""
    from voxvault.service import CLIENT_PRESENCE_S

    service._last_client_seen = time.monotonic() - CLIENT_PRESENCE_S - 1
    monkeypatch.setattr(
        type(service.pipeline), "pending", lambda self: ["uma reuniao"]
    )
    with pytest.raises(ServiceBusy, match="aguardando transcricao"):
        _shutdown_if_idle(service)


def test_shutdown_is_allowed_when_genuinely_idle(
    service: ResidentService, monkeypatch
) -> None:
    from voxvault.service import CLIENT_PRESENCE_S

    service._last_client_seen = time.monotonic() - CLIENT_PRESENCE_S - 1
    monkeypatch.setattr(type(service.pipeline), "pending", lambda self: [])
    assert _shutdown_if_idle(service) == {"encerrando": True}


# -- recording guards --------------------------------------------------

def test_pausing_without_a_recording_says_so(service: ResidentService) -> None:
    with pytest.raises(ServiceBusy, match="Nao ha gravacao"):
        service.pause_recording()


def test_stopping_without_a_recording_says_so(service: ResidentService) -> None:
    with pytest.raises(ServiceBusy, match="Nao ha gravacao"):
        service.stop_recording()


def test_health_reports_where_the_data_lives(service: ResidentService) -> None:
    saude = service.health()
    assert saude["diretorio_de_dados"] == str(service.config.data_dir)
    assert saude["gravacao_ativa"] is False
    assert saude["versao"]


def test_an_idle_service_reports_no_recording(service: ResidentService) -> None:
    assert service.recording()["ativa"] is False


# -- one connection per thread ----------------------------------------

def test_each_thread_gets_its_own_store_handle(service: ResidentService) -> None:
    """The HTTP server answers each request on its own worker thread, and a
    sqlite3 connection belongs to the thread that opened it."""
    seen: list[int] = []
    errors: list[BaseException] = []

    def touch_from_thread() -> None:
        try:
            handle = service.store.handle
            seen.append(id(handle))
            list(handle.iter_meetings())
        except BaseException as exc:
            errors.append(exc)

    main_handle = id(service.store.handle)
    threads = [threading.Thread(target=touch_from_thread) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == [], f"uma thread nao conseguiu usar o armazenamento: {errors}"
    assert main_handle not in seen, "cada thread precisa da propria conexao"
    assert len(set(seen)) == 3

    service.store.close_all()


# -- the shutdown request must be honourable ---------------------------

def test_a_connected_client_does_not_block_an_explicit_shutdown(
    service: ResidentService, monkeypatch
) -> None:
    """The bug this guards: every request refreshes client presence, including
    the request asking the service to stop. Counting that made the shutdown
    route impossible to honour -- and it refused with the wrong reason, because
    it fell through to blaming an empty queue."""
    monkeypatch.setattr(type(service.pipeline), "pending", lambda self: [])
    service.touch()  # exactly what the /encerrar request itself does

    assert service.busy() is True, "o temporizador de ociosidade deve segurar"
    assert service.has_work() == (False, ""), "mas nao ha trabalho a perder"
    assert _shutdown_if_idle(service) == {"encerrando": True}


def test_the_refusal_names_the_real_reason(service: ResidentService, monkeypatch) -> None:
    monkeypatch.setattr(
        type(service.pipeline), "pending", lambda self: ["uma", "outra"]
    )
    with pytest.raises(ServiceBusy) as caught:
        _shutdown_if_idle(service)
    assert "2 reuniao" in str(caught.value)

    monkeypatch.setattr(type(service.pipeline), "pending", lambda self: [])
    service._session = object()
    try:
        with pytest.raises(ServiceBusy, match="gravacao em andamento"):
            _shutdown_if_idle(service)
    finally:
        service._session = None


def test_health_and_the_shutdown_guard_agree(service: ResidentService, monkeypatch) -> None:
    """They disagreed, and a surface trusting one was more permissive than the
    service's own guard."""
    monkeypatch.setattr(type(service.pipeline), "pending", lambda self: [])
    service.touch()

    saude = service.health()
    blocked, _reason = service.has_work()

    assert saude["fila_pendente"] == 0
    assert saude["gravacao_ativa"] is False
    assert blocked is False, (
        "saude diz que nao ha trabalho; a guarda tem de concordar"
    )


# -- deletions a dead process left half done ---------------------------

def _meeting_on_disk(service: ResidentService, uid: str) -> Path:
    from datetime import UTC, datetime

    from voxvault.layout import meeting_dir
    from voxvault.types import MeetingState

    directory = meeting_dir(service.config.data_dir, uid)
    directory.mkdir(parents=True)
    (directory / "mic.flac").write_bytes(b"m" * 64)
    (directory / "metadados.json").write_text("{}", encoding="utf-8")
    service.store.create_meeting(
        uid=uid, title="Reuniao interrompida", directory=directory,
        started_at=datetime(2026, 3, 2, 14, 0, tzinfo=UTC),
        state=MeetingState.RECORDED,
    )
    return directory


class _Crash(BaseException):
    """The process dying: nothing after this point runs."""


def _delete_until(service: ResidentService, uid: str, point: str) -> None:
    from voxvault.store import deletion

    def hook(label: str) -> None:
        if label == point:
            raise _Crash(point)

    deletion.interruption_hook = hook
    try:
        with pytest.raises(_Crash):
            deletion.delete_meeting(service.store.handle, uid)
    finally:
        deletion.interruption_hook = None


def test_recovery_restores_a_meeting_whose_rows_were_never_deleted(
    service: ResidentService,
) -> None:
    """Scenario: Interrupcao no meio da exclusao -- before the transaction."""
    directory = _meeting_on_disk(service, "reuniao-a")
    _delete_until(service, "reuniao-a", "depois_de_renomear")
    assert not directory.exists()

    summary = service.recover()

    assert summary["exclusoes"] == 1
    assert (directory / "mic.flac").is_file()
    assert service.store.get_meeting("reuniao-a") is not None
    assert not list(directory.parent.glob(".excluindo-*"))


def test_recovery_finishes_a_deletion_whose_rows_are_gone(
    service: ResidentService,
) -> None:
    """Scenario: Interrupcao no meio da exclusao -- after the transaction."""
    directory = _meeting_on_disk(service, "reuniao-b")
    _delete_until(service, "reuniao-b", "depois_do_banco")
    assert list(directory.parent.glob(".excluindo-*"))

    summary = service.recover()

    assert summary["exclusoes"] == 1
    assert service.store.get_meeting("reuniao-b") is None
    assert list(directory.parent.iterdir()) == []
