"""Events with a cursor: how a client learns what happened while it looked away.

The desktop app turns these into notifications -- "transcription ready", with
a click that opens the meeting -- so two properties carry the weight: every
event names its meeting on its own, never inside a sentence, and a client that
just arrived is told where "now" is instead of being handed the past.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from voxvault.config import Config
from voxvault.layout import meeting_dir
from voxvault.pipeline import TrackOutcome
from voxvault.service import ResidentService, _make_handler, rendezvous
from voxvault.types import AttemptState, EngineInfo, MeetingState, Segment

SECRET = "segredo-de-teste"


class _Engine:
    """Just enough of the inference worker for one attempt."""

    engine = EngineInfo(
        name="falso", model="modelo-de-teste", compute_type="int8",
        device="cpu", version="1.0",
    )
    alive = True

    def __init__(self, fail: bool) -> None:
        self.fail = fail

    def transcribe(self, audio_path, track, language, vocabulary) -> TrackOutcome:
        if self.fail:
            return TrackOutcome(track, False, [], "a GPU ficou sem memoria")
        return TrackOutcome(track, True, [Segment(0, 1000, "fala")])

    def kill(self) -> float:
        return 0.0


@pytest.fixture
def service(tmp_path: Path) -> ResidentService:
    handle = ResidentService(Config(data_dir=tmp_path))
    handle._secret = SECRET
    return handle


def _meeting(service: ResidentService, uid: str) -> None:
    directory = meeting_dir(service.config.data_dir, uid)
    directory.mkdir(parents=True)
    (directory / "mic.wav").write_bytes(b"RIFF....WAVEfmt ")
    service.store.create_meeting(
        uid=uid, title=f"Reuniao {uid}", directory=directory,
        started_at=datetime(2026, 3, 2, 14, 0, tzinfo=UTC),
        state=MeetingState.RECORDED,
    )


def _transcribe(service: ResidentService, uid: str, *, fail: bool) -> None:
    pipeline = service.pipeline
    pipeline._worker_factory = lambda config, model: _Engine(fail)
    pipeline._release_worker()
    service.store.set_attempt_state(uid, AttemptState.QUEUED)
    pipeline._process(service.store.get_meeting(uid))


def test_ready_and_failed_carry_the_meeting_and_a_growing_number(service) -> None:
    _meeting(service, "reuniao-boa")
    _meeting(service, "reuniao-ruim")

    _transcribe(service, "reuniao-boa", fail=False)
    _transcribe(service, "reuniao-ruim", fail=True)

    events = service.events_since(0)["eventos"]
    by_kind = {(e["tipo"], e["uid"]): e for e in events}
    assert ("pronta", "reuniao-boa") in by_kind
    assert ("falhou", "reuniao-ruim") in by_kind
    failed = by_kind[("falhou", "reuniao-ruim")]
    assert "sem memoria" in failed["detalhe"]
    assert "reuniao-ruim" not in failed["detalhe"], "o uid nao vem mais no texto"
    assert by_kind[("pronta", "reuniao-boa")]["titulo"] == "Reuniao reuniao-boa"
    assert failed["titulo"] == "Reuniao reuniao-ruim"
    numbers = [e["seq"] for e in events]
    assert numbers == sorted(numbers) and len(set(numbers)) == len(numbers)
    assert by_kind[("pronta", "reuniao-boa")]["seq"] < failed["seq"]


def test_a_client_without_a_cursor_only_learns_where_now_is(service) -> None:
    service._record_event("aviso", "antes de o cliente chegar")
    service._record_event("pronta", uid="r1")

    answer = service.events_since(None)

    assert answer["eventos"] == []
    assert answer["ultimo"] == 2
    assert answer["execucao"] == service.started_at.isoformat()


# -- the route ---------------------------------------------------------

@pytest.fixture
def served(service):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _get(base: str, route: str, secret: str | None = SECRET) -> tuple[int, dict]:
    request = urllib.request.Request(base + route)
    if secret is not None:
        request.add_header(rendezvous.SECRET_HEADER, secret)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_the_route_without_since_returns_only_the_latest(service, served) -> None:
    service._record_event("pronta", uid="r1")
    service._record_event("pronta", uid="r2")

    status, payload = _get(served, "/eventos")

    assert status == 200
    assert payload["eventos"] == []
    assert payload["ultimo"] == 2


def test_the_route_with_an_old_cursor_returns_what_came_after(service, served) -> None:
    for uid in ("r1", "r2", "r3"):
        service._record_event("pronta", uid=uid)

    status, payload = _get(served, "/eventos?desde=1")

    assert status == 200
    assert [(e["seq"], e["uid"]) for e in payload["eventos"]] == [(2, "r2"), (3, "r3")]
    assert set(payload["eventos"][0]) == {
        "seq", "tipo", "uid", "titulo", "detalhe", "instante",
    }
    assert payload["ultimo"] == 3


def test_the_route_refuses_without_the_secret(service, served) -> None:
    service._record_event("pronta", uid="r1")

    status, payload = _get(served, "/eventos?desde=0", secret=None)
    assert status == 403
    assert "eventos" not in payload

    status, _ = _get(served, "/eventos?desde=0", secret="outro")
    assert status == 403


def test_a_malformed_cursor_is_refused_by_name(service, served) -> None:
    status, payload = _get(served, "/eventos?desde=ontem")
    assert status == 400
    assert "desde" in payload["erro"]


def test_only_the_newest_events_are_kept(service) -> None:
    from voxvault.service import EVENTS_KEPT

    for index in range(EVENTS_KEPT + 50):
        service._record_event("aviso", str(index))

    kept = service.events_since(0)["eventos"]
    assert len(kept) == EVENTS_KEPT
    assert kept[0]["seq"] == 51
    assert service.events_since(None)["ultimo"] == EVENTS_KEPT + 50


def test_polling_does_not_leave_a_connection_per_request(service, served) -> None:
    """The HTTP server runs each request on a thread that ends with it. A
    connection per request left open grew a resident service by 2 MB a minute
    under the app's polling, with a file handle each."""
    _meeting(service, "reuniao-1")
    _ = service.store.handle  # the test thread's own handle, which stays
    antes = service.store.open_handles

    for _ in range(40):
        for rota in ("/saude", "/eventos?desde=0"):
            status, _ = _get(served, rota)
            assert status == 200

    assert service.store.open_handles == antes
    handles = service.pipeline._handles
    assert handles is None or handles.open_handles == 0
