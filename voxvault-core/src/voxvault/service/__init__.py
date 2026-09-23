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
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


#: How many events the service keeps. A client polling every few seconds
#: never falls this far behind; one that does resynchronizes from ``ultimo``.
EVENTS_KEPT = 200

class ResidentService:
    """Owns the capture and the transcription queue for this machine."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or load_config()
        self.started_at = datetime.now(UTC)
        self._claim: exclusivity.Claim | None = None
        self._store = None
        self._pipeline = None
        self._session = None
        #: True while a start is opening the devices. Until the session
        #: exists nothing else says a recording is on its way, and a second
        #: start in that window would open a second capture of the same
        #: microphone -- one nobody would own, and no icon would show.
        self._starting = False
        self._lock = threading.RLock()
        self._halt = threading.Event()
        self._last_client_seen = time.monotonic()
        self._server: ThreadingHTTPServer | None = None
        self._secret = ""
        #: The last events, each numbered by ``_seq``, which only grows
        #: during one run of the service. ``/eventos`` hands them out by
        #: cursor, so a client asks for what it has not seen yet.
        self._events: list[dict] = []
        self._seq = 0
        self._events_lock = threading.Lock()
        self._power = None
        self._suspended_session = ""
        self._detector = None

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
            from ..store import ThreadLocalStore

            self.config.data_dir.mkdir(parents=True, exist_ok=True)
            self._store = ThreadLocalStore(self.config.db_path)
        return self._store

    @property
    def pipeline(self):
        if self._pipeline is None:
            from ..pipeline import TranscriptionPipeline

            self._pipeline = TranscriptionPipeline(
                self.config, self.store,
                on_event=lambda kind, uid, detail: self._record_event(
                    kind, detail, uid=uid, title=self._title_of(uid)
                ),
            )
        return self._pipeline

    def release_thread_resources(self) -> None:
        """Close what the calling thread opened on the database.

        Every request runs on a thread of its own that ends with the request,
        so a connection left open by one is one more on every poll.
        """
        if self._store is not None:
            self._store.release()
        if self._pipeline is not None:
            self._pipeline.release_thread_store()

    def _record_event(
        self, kind: str, detail: str = "", *, uid: str = "", title: str = ""
    ) -> None:
        """Keep one event, numbered, with the meeting it is about on its own.

        The title travels with the identifier so a client can say which
        meeting finished transcribing without asking for the list.
        """
        with self._events_lock:
            self._seq += 1
            self._events.append({
                "seq": self._seq,
                "tipo": kind,
                "uid": uid,
                "titulo": title,
                "detalhe": detail,
                "instante": datetime.now(UTC).isoformat(),
            })
            del self._events[:-EVENTS_KEPT]

    def _title_of(self, uid: str) -> str:
        if not uid:
            return ""
        try:
            meeting = self.store.get_meeting(uid)
        except Exception:
            return ""
        return meeting.title if meeting is not None else ""

    def events_since(self, since: int | None) -> dict:
        """The events after ``since``, oldest first, and the newest number.

        Without ``since`` only the newest number comes back: that is how a
        client that just started learns where "now" is without being handed
        the history of things that happened before it was listening.
        ``execucao`` changes when the service restarts and numbering starts
        over, so a client holding an old cursor can tell a restart from a
        quiet spell.
        """
        with self._events_lock:
            latest = self._seq
            events = [] if since is None else [
                dict(e) for e in self._events if e["seq"] > since
            ]
        return {
            "eventos": events,
            "ultimo": latest,
            "execucao": self.started_at.isoformat(),
        }

    def recover(self) -> dict:
        """Pick up whatever the last run left unfinished.

        Runs before anything is served, so a client never sees a half-recovered
        picture: deletions a dead process left half done, finalizations that
        stopped mid-way, attempts that were running when the process died, and
        exports that drifted from their revision.

        Deletions come first. A tombstone is a meeting directory under another
        name, and nothing after this step should ever look inside one.
        """
        summary = {
            "exclusoes": 0, "finalizacoes": 0, "tentativas": 0, "exportacoes": 0,
        }

        try:
            from ..store.deletion import resolve_tombstones

            summary["exclusoes"] = resolve_tombstones(self.store, self.config.data_dir)
        except Exception as exc:
            self._record_event("aviso", f"exclusoes interrompidas: {exc}")

        from ..session.finalize import (
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
                self._record_event(
                    "aviso", f"finalizacao: {exc}", uid=directory.name
                )

        summary["tentativas"] = self.pipeline.recover_pending()

        try:
            from ..store import reconcile_exports

            summary["exportacoes"] = len(reconcile_exports(self.store))
        except Exception as exc:
            self._record_event("aviso", f"exportacoes: {exc}")

        return summary

    def _enqueue_quietly(self, meeting_uid: str) -> None:
        try:
            self.pipeline.enqueue(meeting_uid)
        except Exception as exc:
            self._record_event("aviso", f"fila: {exc}", uid=meeting_uid)

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
        self._watch_power()

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
            from ..capture.devices import (
                FLOW_CAPTURE,
                FLOW_RENDER,
                default_endpoint,
                role_from_config,
            )
            from ..capture.stream import prewarm

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

    def _watch_power(self) -> None:
        """React to suspend by making the recording durable, and nothing else."""
        from .power import PowerWatcher

        watcher = PowerWatcher(
            on_suspend=self._on_suspend, on_resume=self._on_resume
        )
        if watcher.start():
            self._power = watcher
            self._record_event("energia", "aviso de suspensao registrado")
        else:
            # Not fatal: the recorder still works, it just will not get the
            # warning, and the next start recovers the session instead.
            self._record_event(
                "aviso",
                f"sem aviso de suspensao ({watcher.failure}); uma suspensao "
                f"durante a gravacao sera recuperada na proxima inicializacao",
            )

    def _on_suspend(self) -> None:
        with self._lock:
            session = self._session
            self._session = None
        if session is None:
            return
        elapsed = session.durable_minimum("suspensao do sistema")
        self._suspended_session = session.uid
        from .power import DURABLE_MINIMUM_MS

        self._record_event(
            "suspensao",
            f"minimo duravel de '{session.title}' em {elapsed:.0f} ms"
            + ("" if elapsed <= DURABLE_MINIMUM_MS else
               f" (acima do teto de {DURABLE_MINIMUM_MS} ms)"),
            uid=session.uid,
        )

    def _on_resume(self) -> None:
        """Finish what the suspend deliberately left undone.

        Never reopens the recording: the meeting ended when the machine went
        to sleep, and resuming capture into the same file would silently join
        two different conversations.
        """
        uid, self._suspended_session = self._suspended_session, ""
        if not uid:
            return
        self._record_event(
            "retomada",
            f"a sessao '{uid}' foi encerrada pela suspensao; concluindo a "
            f"compressao e o enfileiramento",
            uid=uid,
        )
        try:
            summary = self.recover()
            self._record_event("retomada", f"recuperacao: {summary}")
        except Exception as exc:
            self._record_event("aviso", f"recuperacao apos retomada: {exc}")

    def _supervise(self) -> None:
        """End the service once there is genuinely nothing to hold it open."""
        while not self._halt.wait(SUPERVISION_INTERVAL_S):
            if self.has_work()[0]:
                # Idle time counts from when the work ended. Only work may push
                # the clock forward here: refreshing it for a merely *recent*
                # client made presence perpetuate itself -- seen 5 s ago, so
                # refreshed to now, so seen 5 s ago at the next check -- and a
                # service that one client had ever touched never ended. A
                # client that is really connected keeps touching on its own.
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

    def has_work(self) -> tuple[bool, str]:
        """Work that would be destroyed by ending now, and what it is.

        Deliberately excludes "a client is connected". Those are two different
        questions and conflating them made the service impossible to stop:
        every request refreshes client presence, including the request asking
        it to stop, so an explicit shutdown was always refused -- and refused
        with the wrong reason, because it fell through to blaming the queue.
        """
        with self._lock:
            if self._session is not None:
                return True, "ha uma gravacao em andamento"
            if self._starting:
                return True, "uma gravacao esta sendo iniciada"
        try:
            pending = self.pipeline.pending()
        except Exception:
            pending = []
        if pending:
            return True, (
                f"ainda ha {len(pending)} reuniao(oes) aguardando transcricao"
            )
        return False, ""

    def busy(self) -> bool:
        """Whether the idle timer should hold off.

        Broader than :meth:`has_work` on purpose: a connected client keeps the
        service alive even with nothing to do, because ending it under a
        surface that is still using it would just make that surface start it
        again a second later.
        """
        if self.has_work()[0]:
            return True
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
        if self._power is not None:
            self._power.stop()
            self._power = None
        if self._pipeline is not None:
            self._pipeline.stop()
        if self._store is not None:
            self._store.close_all()
            self._store = None
        rendezvous.withdraw()
        if self._claim is not None:
            self._claim.release()
            self._claim = None

    # -- meeting detection ---------------------------------------------

    @property
    def detector(self):
        if self._detector is None:
            from ..detect import MeetingDetector

            self._detector = MeetingDetector()
        return self._detector

    def detection(self) -> dict:
        """Whether a meeting looks like it started, and on what evidence.

        Sampled on the caller's rhythm rather than on a timer of its own: the
        sustained period is measured in wall clock, so polling more or less
        often changes nothing about when a detection becomes valid.
        """
        from ..detect import candidates

        available, reason = self.detector.available()
        if not available:
            return {"disponivel": False, "motivo": reason, "deteccao": None}

        detection = self.detector.observe()
        with self._lock:
            gravando = self._session is not None
        return {
            "disponivel": True,
            "gravando": gravando,
            "deteccao": detection.as_dict() if detection else None,
            "candidatos": [
                {"aplicativo": c.name, "sinal": str(c.signal)}
                for c in candidates()
            ],
        }

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
        """Open both tracks and begin. Yields the machine from transcription first.

        One start at a time. Opening a device normally takes a few hundred
        milliseconds, but a wedged audio service can hold it for minutes, and
        a person clicking again meanwhile must be told so rather than get a
        second recording started behind the first.
        """
        with self._lock:
            if self._session is not None:
                raise ServiceBusy(
                    "Ja existe uma gravacao em andamento. Encerre-a antes de "
                    "comecar outra."
                )
            if self._starting:
                raise ServiceBusy(
                    "Uma gravacao ja esta sendo iniciada: os dispositivos de "
                    "audio ainda estao abrindo. Aguarde alguns segundos."
                )
            self._starting = True
        try:
            return self._start_recording(title)
        finally:
            with self._lock:
                self._starting = False

    def _start_recording(self, title: str) -> dict:
        began = time.perf_counter()
        # The machine belongs to the recording: inference is killed before a
        # single sample is captured, and the cost of doing so is measured so a
        # missed budget is reported rather than hidden.
        released = self.pipeline.suspend_for_recording()

        from ..capture.devices import (
            FLOW_CAPTURE,
            FLOW_RENDER,
            resolve_endpoint,
            role_from_config,
        )
        from ..capture.stream import CaptureStream
        from ..config import POLICY_PINNED
        from ..session import RecordingSession
        from ..session.supervisor import TrackWatch
        from ..store import Origin
        from ..types import MeetingState

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
        self._record_event("gravando", uid=session.uid, title=session.title)
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

        from ..types import MeetingState

        try:
            self.store.finish_meeting(
                session.uid,
                ended_at=datetime.now(UTC),
                duration_ms=report.duration_ms,
                state=MeetingState.RECORDED,
            )
        except Exception as exc:
            self._record_event("aviso", f"metadados: {exc}", uid=session.uid)

        self.pipeline.resume_after_recording()
        self._record_event("encerrada", uid=session.uid, title=session.title)
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

        def log_message(self, *args) -> None:
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

            path, _, query = self.path.partition("?")
            route = (method, path.rstrip("/") or "/")
            try:
                handler = _ROUTES.get(route)
                if handler is None:
                    self._reply(HTTPStatus.NOT_FOUND, {"erro": f"rota desconhecida: {route[1]}"})
                    return
                self._reply(HTTPStatus.OK, handler(service, self._body(), _query(query)))
            except ServiceBusy as exc:
                self._reply(HTTPStatus.CONFLICT, {"erro": str(exc)})
            except VoxVaultError as exc:
                self._reply(HTTPStatus.BAD_REQUEST, {"erro": str(exc)})
            except Exception as exc:
                self._reply(HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "erro": f"{type(exc).__name__}: {exc}"
                })

        def do_GET(self) -> None:
            try:
                self._dispatch("GET")
            finally:
                service.release_thread_resources()

        def do_POST(self) -> None:
            try:
                self._dispatch("POST")
            finally:
                service.release_thread_resources()

    return Handler


def _query(raw: str) -> dict[str, str]:
    from urllib.parse import parse_qsl

    return dict(parse_qsl(raw, keep_blank_values=True))


def _since(query: dict[str, str]) -> int | None:
    raw = query.get("desde", "")
    if not raw:
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        raise VoxVaultError(
            f"'desde' tem de ser o numero de um evento, recebido '{raw}'."
        ) from None


_ROUTES = {
    ("GET", "/saude"): lambda svc, body, query: svc.health(),
    ("GET", "/gravacao"): lambda svc, body, query: svc.recording(),
    ("GET", "/deteccao"): lambda svc, body, query: svc.detection(),
    ("GET", "/eventos"): lambda svc, body, query: svc.events_since(_since(query)),
    ("POST", "/gravacao/iniciar"): lambda svc, body, query: svc.start_recording(
        title=str(body.get("titulo") or body.get("title") or "")
    ),
    ("POST", "/gravacao/pausar"): lambda svc, body, query: svc.pause_recording(),
    ("POST", "/gravacao/retomar"): lambda svc, body, query: svc.resume_recording(),
    ("POST", "/gravacao/encerrar"): lambda svc, body, query: svc.stop_recording(),
    ("POST", "/encerrar"): lambda svc, body, query: (_shutdown_if_idle(svc)),
}


def _shutdown_if_idle(service: ResidentService) -> dict:
    """Refuse to end while there is a recording or a queue.

    An app that could end the service at will would silently throw away a
    transcription queue when someone closed a window. A client merely being
    connected is not a reason to refuse: the client asking to stop is itself
    connected, so counting that would make the request impossible to honour.
    """
    blocked, reason = service.has_work()
    if blocked:
        raise ServiceBusy(f"O servico nao pode ser encerrado agora: {reason}.")
    service.stop()
    return {"encerrando": True}


__all__ = [
    "EVENTS_KEPT",
    "IDLE_SHUTDOWN_S",
    "RecordingView",
    "ResidentService",
    "ServiceBusy",
    "exclusivity",
    "rendezvous",
]
