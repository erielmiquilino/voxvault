"""The resident service: it owns the capture and it owns the queue.

Something has to hold a recording open between "start" and "stop", and it
cannot be the command that started it -- that process exits. So one resident
process holds both, and every surface becomes a client of it: the command
line, the desktop app, and anything else added later.

Two consequences shape the design.

**Exclusivity is per machine.** A second service pointed at a different data
directory is still a second process reaching for the same microphone and the
same GPU.

**It stays alive for work, not for callers.** It shuts down after an idle
period, but an active recording, a non-empty queue, or a single connected
client all count as not idle. Ending a service that still had a queue would
silently throw away transcriptions nobody asked it to drop.

The transport is HTTP on loopback with an ephemeral port and a per-start
secret, both published at the rendezvous point. Loopback because the desktop
app is a separate process in a separate language; `http.server` from the
standard library because a web framework would be startup cost bought for
nothing.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import __version__
from ..config import Config, load_config
from ..errors import VoxVaultError
from . import exclusivity, rendezvous

#: How long without anything to do before the service ends itself.
IDLE_SHUTDOWN_S = 300.0
#: A client that has spoken this recently counts as connected.
CLIENT_PRESENCE_S = 30.0
#: How often the idle check runs.
SUPERVISION_INTERVAL_S = 5.0


class ServiceBusy(VoxVaultError):
    """The requested operation conflicts with what the service is doing."""


@dataclass(slots=True)
class RecordingView:
    """What a surface needs to render the current recording."""

    ativa: bool = False
    pausada: bool = False
    uid: str = ""
    titulo: str = ""
    inicio: str = ""
    duracao_ms: int = 0
    divergencia_ms: int = 0
    trilhas: dict = field(default_factory=dict)
    avisos: list = field(default_factory=list)
    #: Per-track peak since the last read, so a meter can show that the
    #: microphone is picking something up. Reading resets it, which caps the
    #: update rate at whatever the caller polls at.
    niveis: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ativa": self.ativa,
            "pausada": self.pausada,
            "uid": self.uid,
            "titulo": self.titulo,
            "inicio": self.inicio,
            "duracao_ms": self.duracao_ms,
            "divergencia_ms": self.divergencia_ms,
            "trilhas": self.trilhas,
            "avisos": self.avisos,
            "niveis": self.niveis,
        }


class ResidentService:
    """Owns the capture and the transcription queue for this machine."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or load_config()
        self.started_at = datetime.now(timezone.utc)
        self._claim: exclusivity.Claim | None = None
        self._store = None
        self._pipeline = None
        self._session = None
        self._lock = threading.RLock()
        self._halt = threading.Event()
        self._last_client_seen = time.monotonic()
        self._server: ThreadingHTTPServer | None = None
        self._secret = ""
        self._events: list[tuple[str, str, float]] = []

    # -- lifecycle -----------------------------------------------------

    def claim_machine(self) -> None:
        claim = exclusivity.acquire()
        if not claim.held:
            raise ServiceBusy(exclusivity.describe_conflict())
        self._claim = claim

    @property
    def store(self):
        """One connection per thread: the HTTP server answers each request on
        its own worker, and a sqlite3 connection belongs to its opener."""
        if self._store is None:
            from ..store import ThreadLocalStore  # noqa: PLC0415

            self.config.data_dir.mkdir(parents=True, exist_ok=True)
            self._store = ThreadLocalStore(self.config.db_path)
        return self._store

    @property
    def pipeline(self):
        if self._pipeline is None:
            from ..pipeline import TranscriptionPipeline  # noqa: PLC0415

            self._pipeline = TranscriptionPipeline(
                self.config, self.store, on_event=self._record_event
            )
        return self._pipeline

    def _record_event(self, kind: str, detail: str) -> None:
        self._events.append((kind, detail, time.time()))
        del self._events[:-200]

    def recover(self) -> dict:
        """Pick up whatever the last run left unfinished.

        Runs before anything is served, so a client never sees a half-recovered
        picture: finalizations that stopped mid-way, attempts that were running
        when the process died, and exports that drifted from their revision.
        """
        summary = {"finalizacoes": 0, "tentativas": 0, "exportacoes": 0}

        from ..session.finalize import (  # noqa: PLC0415
            finalize_session,
            now_iso,
            pending_finalizations,
            read_metadata,
        )

        for directory in pending_finalizations(self.config.data_dir):
            metadata = read_metadata(directory)
            if metadata is None:
                continue
            try:
                finalize_session(
                    directory,
                    tracks=list(metadata.tracks) or ["mic", "system"],
                    duration_ms=metadata.duration_ms,
                    ended_at=metadata.ended_at or now_iso(),
                    submit=lambda uid=metadata.uid: self._enqueue_quietly(uid),
                )
                summary["finalizacoes"] += 1
            except Exception as exc:
                self._record_event("aviso", f"finalizacao de {directory.name}: {exc}")

        summary["tentativas"] = self.pipeline.recover_pending()

        try:
            from ..store import reconcile_exports  # noqa: PLC0415

            summary["exportacoes"] = len(reconcile_exports(self.store))
        except Exception as exc:
            self._record_event("aviso", f"exportacoes: {exc}")

        return summary

    def _enqueue_quietly(self, meeting_uid: str) -> None:
        try:
            self.pipeline.enqueue(meeting_uid)
        except Exception as exc:
            self._record_event("aviso", f"fila de {meeting_uid}: {exc}")

    def serve(self) -> int:
        """Bind, publish the rendezvous, and run until idle or stopped."""
        self.claim_machine()
        self._secret = rendezvous.mint_secret()

        handler = _make_handler(self)
        # Port 0: the operating system picks a free one, and the rendezvous is
        # how anybody finds out which. A fixed port would collide with whatever
        # else the machine happens to be running.
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        host, port = self._server.server_address[:2]

        import os

        rendezvous.publish(rendezvous.Rendezvous(
            endereco=f"{host}:{port}",
            segredo=self._secret,
            pid=os.getpid(),
            diretorio_de_dados=str(self.config.data_dir),
        ))

        self.recover()
        self.pipeline.start()
        self._prewarm_devices()

        supervisor = threading.Thread(
            target=self._supervise, name="voxvault-supervisao", daemon=True
        )
        supervisor.start()

        try:
            self._server.serve_forever(poll_interval=0.5)
        finally:
            self.shutdown()
        return 0

    def _prewarm_devices(self) -> None:
        """Pay the first device-open cost now, not when someone presses record.

        The first ``IAudioClient::Initialize`` in a process is orders of
        magnitude slower than every later one -- measured on this machine at
        between 11 and 120 seconds, entirely inside the operating system call,
        against 8 to 185 milliseconds once warm. The specification caps
        starting a recording at 1500 ms, and that budget is unreachable on a
        cold process. So the resident service opens and closes both endpoints
        as soon as it starts, on its own thread, where the delay costs nobody
        anything.
        """
        def warm() -> None:
            from ..capture.devices import (  # noqa: PLC0415
                FLOW_CAPTURE,
                FLOW_RENDER,
                default_endpoint,
                role_from_config,
            )
            from ..capture.stream import prewarm  # noqa: PLC0415

            role = role_from_config(self.config.device_role)
            for flow, loopback in ((FLOW_CAPTURE, False), (FLOW_RENDER, True)):
                try:
                    endpoint = default_endpoint(flow, role)
                    if endpoint is None:
                        continue
                    cost = prewarm(endpoint.id, loopback=loopback)
                    self._record_event(
                        "aquecido", f"{flow} em {cost:.0f} ms ({endpoint.name})"
                    )
                except Exception as exc:
                    self._record_event("aviso", f"aquecimento de {flow}: {exc}")

        threading.Thread(
            target=warm, name="voxvault-aquecimento", daemon=True
        ).start()

    def _supervise(self) -> None:
        """End the service once there is genuinely nothing to hold it open."""
        while not self._halt.wait(SUPERVISION_INTERVAL_S):
            if self.busy():
                self._last_client_seen = max(
                    self._last_client_seen, time.monotonic()
                )
                continue
            idle_for = time.monotonic() - self._last_client_seen
            if idle_for >= IDLE_SHUTDOWN_S:
                self._record_event(
                    "encerrando", f"ocioso por {idle_for:.0f}s sem trabalho nem cliente"
                )
                self.stop()
                return

    def busy(self) -> bool:
        """Work that must not be interrupted by an idle shutdown."""
        with self._lock:
            if self._session is not None:
                return True
        try:
            if self.pipeline.pending():
                return True
        except Exception:
            pass
        return (time.monotonic() - self._last_client_seen) < CLIENT_PRESENCE_S

    def touch(self) -> None:
        self._last_client_seen = time.monotonic()

    def stop(self) -> None:
        self._halt.set()
        server, self._server = self._server, None
        if server is not None:
            threading.Thread(target=server.shutdown, daemon=True).start()

    def shutdown(self) -> None:
        self._halt.set()
        with self._lock:
            session = self._session
        if session is not None:
            # A service ending with a recording open still owes that recording
            # a finalization; dropping it would lose the meeting.
            try:
                self.stop_recording()
            except Exception:
                pass
        if self._pipeline is not None:
            self._pipeline.stop()
        if self._store is not None:
            self._store.close_all()
            self._store = None
        rendezvous.withdraw()
        if self._claim is not None:
            self._claim.release()
            self._claim = None

    # -- recording -----------------------------------------------------

    def health(self) -> dict:
        try:
            pendentes = len(self.pipeline.pending())
        except Exception:
            pendentes = 0
        with self._lock:
            gravando = self._session is not None
        return {
            "gravacao_ativa": gravando,
            "fila_pendente": pendentes,
            "transcrevendo": not self.pipeline.suspended and pendentes > 0,
            "diretorio_de_dados": str(self.config.data_dir),
            "versao": __version__,
            "desde": self.started_at.isoformat(),
        }

    def recording(self) -> dict:
        with self._lock:
            session = self._session
            if session is None:
                return RecordingView().to_dict()
            return RecordingView(
                ativa=True,
                pausada=session.paused,
                uid=session.uid,
                titulo=session.title,
                inicio=session.started_at.isoformat(),
                duracao_ms=session.duration_ms,
                divergencia_ms=session.drift_ms,
                trilhas={t: s["duracao_ms"] for t, s in session.track_stats().items()},
                avisos=session.warnings,
                niveis=session.levels(),
            ).to_dict()

    def start_recording(self, title: str = "") -> dict:
        """Open both tracks and begin. Yields the machine from transcription first."""
        with self._lock:
            if self._session is not None:
                raise ServiceBusy(
                    "Ja existe uma gravacao em andamento. Encerre-a antes de "
                    "comecar outra."
                )

        began = time.perf_counter()
        # The machine belongs to the recording: inference is killed before a
        # single sample is captured, and the cost of doing so is measured so a
        # missed budget is reported rather than hidden.
        released = self.pipeline.suspend_for_recording()

        from ..capture.devices import (  # noqa: PLC0415
            FLOW_CAPTURE,
            FLOW_RENDER,
            resolve_endpoint,
            role_from_config,
        )
        from ..capture.stream import CaptureStream  # noqa: PLC0415
        from ..config import POLICY_PINNED  # noqa: PLC0415
        from ..session import RecordingSession  # noqa: PLC0415
        from ..store import Origin  # noqa: PLC0415
        from ..types import MeetingState  # noqa: PLC0415

        from ..session.supervisor import TrackWatch  # noqa: PLC0415

        role = role_from_config(self.config.device_role)
        streams: dict[str, object] = {}
        watches: list[TrackWatch] = []
        problems: list[str] = []
        for track, flow, policy, pinned in (
            ("mic", FLOW_CAPTURE, self.config.mic_policy, self.config.mic_device_id),
            ("system", FLOW_RENDER, self.config.system_policy, self.config.system_device_id),
        ):
            try:
                endpoint = resolve_endpoint(
                    flow=flow,
                    policy_pinned_id=pinned if policy == POLICY_PINNED else "",
                    role=role,
                )
                streams[track] = CaptureStream(
                    endpoint.id, loopback=(flow == FLOW_RENDER), name=track
                )
                watches.append(TrackWatch(
                    track=track, flow=flow, policy=policy, pinned_id=pinned,
                    role=role, endpoint_id=endpoint.id, endpoint_name=endpoint.name,
                ))
            except Exception as exc:
                problems.append(f"{track}: {exc}")

        if not streams:
            self.pipeline.resume_after_recording()
            raise ServiceBusy(
                "Nenhuma trilha pode ser aberta. " + "; ".join(problems)
            )

        session = RecordingSession(
            self.config, title=title,
            mic_stream=streams.get("mic"), system_stream=streams.get("system"),
        )
        try:
            session.start()
        except Exception as exc:
            self.pipeline.resume_after_recording()
            # The session knows why each track refused; without those the
            # caller only learns that nothing opened, which is the least
            # useful half of the story.
            detail = "; ".join(session.warnings + problems)
            raise ServiceBusy(
                f"{exc}{(' Motivos: ' + detail) if detail else ''}"
            ) from exc

        # Only the tracks that actually opened are watched: a track that never
        # started has nothing to migrate.
        session.supervise_devices([w for w in watches if w.track in streams])

        self.store.create_meeting(
            uid=session.uid, title=session.title, started_at=session.started_at,
            directory=session.directory, origin=Origin.RECORDED,
            state=MeetingState.RECORDING,
        )
        with self._lock:
            self._session = session

        elapsed_ms = (time.perf_counter() - began) * 1000
        self._record_event("gravando", session.uid)
        payload = self.recording()
        payload["atraso_de_inicio_ms"] = round(elapsed_ms, 1)
        payload["liberacao_de_inferencia_ms"] = round(released * 1000, 1)
        payload["avisos"] = payload.get("avisos", []) + problems
        return payload

    def pause_recording(self) -> dict:
        with self._lock:
            session = self._session
        if session is None:
            raise ServiceBusy("Nao ha gravacao em andamento para pausar.")
        session.pause()
        return self.recording()

    def resume_recording(self) -> dict:
        with self._lock:
            session = self._session
        if session is None:
            raise ServiceBusy("Nao ha gravacao em andamento para retomar.")
        session.resume()
        return self.recording()

    def stop_recording(self) -> dict:
        with self._lock:
            session, self._session = self._session, None
        if session is None:
            raise ServiceBusy("Nao ha gravacao em andamento para encerrar.")

        report = session.stop(submit=lambda: self._enqueue_quietly(session.uid))

        from ..types import MeetingState  # noqa: PLC0415

        try:
            self.store.finish_meeting(
                session.uid,
                ended_at=datetime.now(timezone.utc),
                duration_ms=report.duration_ms,
                state=MeetingState.RECORDED,
            )
        except Exception as exc:
            self._record_event("aviso", f"metadados de {session.uid}: {exc}")

        self.pipeline.resume_after_recording()
        self._record_event("encerrada", session.uid)
        return {
            "uid": report.uid,
            "duracao_ms": report.duration_ms,
            "divergencia_ms": report.drift_ms,
            "trilhas": report.tracks,
            "avisos": report.warnings,
            "diretorio": str(report.directory),
        }


# -- HTTP ---------------------------------------------------------------

def _make_handler(service: ResidentService):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"VoxVault/{__version__}"

        def log_message(self, *args) -> None:  # noqa: D102
            """Silence the default stderr logging; the service has its own."""

        # -- plumbing --------------------------------------------------

        def _authorized(self) -> bool:
            offered = self.headers.get(rendezvous.SECRET_HEADER, "")
            import hmac

            return hmac.compare_digest(offered, service._secret)

        def _reply(self, status: HTTPStatus, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}

        def _dispatch(self, method: str) -> None:
            if not self._authorized():
                self._reply(HTTPStatus.FORBIDDEN, {
                    "erro": "segredo invalido para esta sessao do servico"
                })
                return
            service.touch()

            route = (method, self.path.split("?")[0].rstrip("/") or "/")
            try:
                handler = _ROUTES.get(route)
                if handler is None:
                    self._reply(HTTPStatus.NOT_FOUND, {"erro": f"rota desconhecida: {route[1]}"})
                    return
                self._reply(HTTPStatus.OK, handler(service, self._body()))
            except ServiceBusy as exc:
                self._reply(HTTPStatus.CONFLICT, {"erro": str(exc)})
            except VoxVaultError as exc:
                self._reply(HTTPStatus.BAD_REQUEST, {"erro": str(exc)})
            except Exception as exc:
                self._reply(HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "erro": f"{type(exc).__name__}: {exc}"
                })

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

    return Handler


_ROUTES = {
    ("GET", "/saude"): lambda svc, body: svc.health(),
    ("GET", "/gravacao"): lambda svc, body: svc.recording(),
    ("POST", "/gravacao/iniciar"): lambda svc, body: svc.start_recording(
        title=str(body.get("titulo") or body.get("title") or "")
    ),
    ("POST", "/gravacao/pausar"): lambda svc, body: svc.pause_recording(),
    ("POST", "/gravacao/retomar"): lambda svc, body: svc.resume_recording(),
    ("POST", "/gravacao/encerrar"): lambda svc, body: svc.stop_recording(),
    ("POST", "/encerrar"): lambda svc, body: (_shutdown_if_idle(svc)),
}


def _shutdown_if_idle(service: ResidentService) -> dict:
    """Refuse to end while there is a recording or a queue.

    An app that could end the service at will would silently throw away a
    transcription queue when someone closed a window.
    """
    if service.busy():
        with service._lock:
            gravando = service._session is not None
        raise ServiceBusy(
            "O servico nao pode ser encerrado agora: "
            + ("ha uma gravacao em andamento." if gravando
               else "ainda ha reunioes aguardando transcricao.")
        )
    service.stop()
    return {"encerrando": True}


__all__ = [
    "IDLE_SHUTDOWN_S",
    "RecordingView",
    "ResidentService",
    "ServiceBusy",
    "exclusivity",
    "rendezvous",
]
