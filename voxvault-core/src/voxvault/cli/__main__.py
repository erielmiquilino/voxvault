"""voxvault command line.

Uses argparse from the standard library rather than a CLI framework: a third
party parser costs import time on every invocation, and this tool exists
because a competitor was unusable on Windows. Every handler imports what it
needs lazily, so `--help` never loads an inference runtime -- measured at
about 220 ms for the whole process.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC
from pathlib import Path

from ..layout import available_tracks

PROG = "voxvault"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Grava, transcreve e indexa reunioes localmente.",
    )
    parser.add_argument(
        "--data-dir", metavar="CAMINHO",
        help="Sobrepoe o diretorio de dados para esta invocacao.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser(
        "doctor", help="Verifica o ambiente e relata cada pre-requisito."
    )
    doctor.add_argument("-v", "--verbose", action="store_true",
                        help="Mostra a origem de todos os valores de configuracao.")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler=_cmd_doctor)

    record = sub.add_parser(
        "record", help="Grava uma reuniao: microfone e audio do sistema."
    )
    record.add_argument("--title", default="", help="Titulo da reuniao.")
    record.add_argument("--seconds", type=float, default=0.0,
                        help="Encerra sozinha apos N segundos (0 = ate Ctrl+C).")
    record.add_argument("--no-compress", action="store_true",
                        help="Nao comprime o audio ao finalizar.")
    record.set_defaults(handler=_cmd_record)

    importar = sub.add_parser(
        "import", help="Importa um arquivo de audio ou video ja existente."
    )
    importar.add_argument("arquivo", type=Path)
    importar.add_argument("--title", default="")
    importar.set_defaults(handler=_cmd_import)

    listar = sub.add_parser("list", help="Lista as reunioes guardadas.")
    listar.add_argument("-n", "--limit", type=int, default=20)
    listar.add_argument("--json", action="store_true")
    listar.set_defaults(handler=_cmd_list)

    rename = sub.add_parser("rename", help="Muda o titulo de uma reuniao.")
    rename.add_argument("uid")
    rename.add_argument("--title", required=True, metavar="TITULO")
    rename.set_defaults(handler=_cmd_rename)

    remove_audio = sub.add_parser(
        "remove-audio",
        help="Remove o audio de uma reuniao, preservando a transcricao.",
    )
    remove_audio.add_argument("uid")
    remove_audio.add_argument(
        "--yes", action="store_true",
        help="Confirma a remocao, que nao pode ser desfeita.",
    )
    remove_audio.set_defaults(handler=_cmd_remove_audio)

    show = sub.add_parser("show", help="Mostra a transcricao de uma reuniao.")
    show.add_argument("uid")
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=_cmd_show)

    search = sub.add_parser("search", help="Busca no historico de transcricoes.")
    search.add_argument("termo")
    search.add_argument("-n", "--limit", type=int, default=20)
    search.add_argument(
        "--scope", default="ambos",
        choices=["transcricoes", "notas", "ambos"],
        help="Onde procurar. O padrao alcanca transcricoes e notas.",
    )
    search.add_argument("--json", action="store_true")
    search.set_defaults(handler=_cmd_search)

    # The accepted note types are spelled out here instead of read from
    # ``NoteKind``: importing the store to build the parser would put its cost
    # on every invocation, `--help` included. A test keeps the two in step.
    notes = sub.add_parser(
        "notes", help="Lista, cria, atualiza e remove notas de uma reuniao."
    )
    notes_sub = notes.add_subparsers(dest="notes_command", required=True)
    notes_list = notes_sub.add_parser("list", help="Lista as notas de uma reuniao.")
    notes_list.add_argument("uid")
    notes_add = notes_sub.add_parser("add", help="Grava uma nota numa reuniao.")
    notes_add.add_argument("uid")
    notes_add.add_argument(
        "--type", required=True, metavar="TIPO",
        help="Tipo da nota: resumo, decisoes, pendencias ou livre.",
    )
    notes_add.add_argument("--content", required=True, metavar="TEXTO")
    notes_update = notes_sub.add_parser(
        "update", help="Substitui o conteudo de uma nota existente."
    )
    notes_update.add_argument("id", help="Identificador da nota, ou um prefixo unico.")
    notes_update.add_argument("--content", required=True, metavar="TEXTO")
    notes_remove = notes_sub.add_parser("remove", help="Remove uma nota.")
    notes_remove.add_argument("id", help="Identificador da nota, ou um prefixo unico.")
    notes.set_defaults(handler=_cmd_notes)

    export = sub.add_parser("export", help="Regenera as exportacoes de uma reuniao.")
    export.add_argument("uid")
    export.set_defaults(handler=_cmd_export)

    reprocess = sub.add_parser(
        "reprocess", help="Transcreve novamente uma reuniao cujo audio ainda exista."
    )
    reprocess.add_argument("uid")
    reprocess.set_defaults(handler=_cmd_reprocess)

    queue = sub.add_parser(
        "queue", help="Mostra e processa a fila de transcricao."
    )
    queue.add_argument("--run", action="store_true",
                       help="Processa a fila ate esvaziar.")
    queue.set_defaults(handler=_cmd_queue)

    transcribe = sub.add_parser(
        "transcribe", help="Transcreve um arquivo avulso, sem guardar."
    )
    transcribe.add_argument("arquivo", type=Path)
    transcribe.add_argument("--model")
    transcribe.add_argument("--language", default=None)
    transcribe.add_argument("--vocabulary", default=None)
    transcribe.add_argument("--json", action="store_true")
    transcribe.set_defaults(handler=_cmd_transcribe)

    # One command, two names: `benchmark` is what the specification calls it,
    # `bench` is what anyone types twenty times in an afternoon. An alias, not
    # a second parser -- a second one appears in --help as its own entry.
    bench = sub.add_parser(
        "benchmark",
        aliases=["bench"],
        help="Compara varias configuracoes de modelo sobre o mesmo audio.",
    )
    bench.add_argument("arquivo", type=Path)
    bench.add_argument(
        "--config", type=Path, metavar="ARQUIVO",
        help="Arquivo JSON com as configuracoes a comparar.",
    )
    bench.add_argument(
        "--models", default="",
        help="Modelos separados por virgula, quando nao ha arquivo.",
    )
    bench.add_argument("--language", default=None)
    bench.add_argument("--vocabulary", default=None)
    bench.set_defaults(handler=_cmd_bench)

    config_cmd = sub.add_parser(
        "config", help="Mostra ou altera a configuracao compartilhada."
    )
    config_cmd.add_argument("atribuicao", nargs="*", metavar="CAMPO=VALOR")
    config_cmd.add_argument("--json", action="store_true")
    config_cmd.set_defaults(handler=_cmd_config)

    devices = sub.add_parser(
        "devices", help="Lista os dispositivos de audio e os padroes por papel."
    )
    devices.add_argument("--json", action="store_true")
    devices.set_defaults(handler=_cmd_devices)

    serve = sub.add_parser(
        "serve",
        help="Executa o servico residente, dono da captura e da fila.",
    )
    serve.add_argument(
        "--status", action="store_true",
        help="Apenas informa se ha um servico em execucao.",
    )
    serve.add_argument(
        "--stop", action="store_true",
        help="Pede ao servico em execucao que encerre.",
    )
    serve.add_argument(
        "--force", action="store_true",
        help="Com --stop, encerra mesmo havendo gravacao ou fila pendente.",
    )
    serve.set_defaults(handler=_cmd_serve)

    detect = sub.add_parser(
        "detect",
        help="Mostra quem esta usando o microfone e se parece uma reuniao.",
    )
    detect.add_argument(
        "--watch", type=float, default=0.0, metavar="SEGUNDOS",
        help="Observa por N segundos, relatando quando a deteccao se sustenta.",
    )
    detect.add_argument("--json", action="store_true")
    detect.set_defaults(handler=_cmd_detect)

    mcp_cmd = sub.add_parser(
        "mcp", help="Verifica e registra o servidor MCP nos clientes de agente."
    )
    mcp_cmd.add_argument(
        "--apply", action="store_true",
        help="Grava o registro. Sem isso, apenas mostra o que seria alterado.",
    )
    mcp_cmd.set_defaults(handler=_cmd_mcp)

    return parser


def _load(args: argparse.Namespace):
    from ..config import load_config

    overrides = {}
    if getattr(args, "data_dir", None):
        overrides["data_dir"] = args.data_dir
    return load_config(overrides)


def _open_store(config):
    from ..store import TranscriptStore

    config.data_dir.mkdir(parents=True, exist_ok=True)
    return TranscriptStore(config.db_path)


# -- handlers ----------------------------------------------------------


def _cmd_doctor(args: argparse.Namespace) -> int:
    from ..doctor import format_report, run_diagnostics

    config = _load(args)
    report = run_diagnostics(config)

    if args.json:
        import json

        sys.stdout.write(json.dumps({
            "itens": [
                {
                    "chave": item.key,
                    "rotulo": item.label,
                    "estado": item.status,
                    "detalhe": item.detail,
                    "acao": item.remedy or None,
                }
                for item in report.items
            ],
            "falhou": report.failed,
            "avisou": report.warned,
            "configuracao": {
                nome: {"valor": str(getattr(config, nome)), "origem": origem}
                for nome, origem in sorted(report.config_sources.items())
            },
        }, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        return 1 if report.failed else 0

    sys.stdout.write(format_report(report, config, verbose=args.verbose))
    return 1 if report.failed else 0


def _cmd_record(args: argparse.Namespace) -> int:
    import time
    from datetime import datetime

    from ..capture.devices import (
        FLOW_CAPTURE,
        FLOW_RENDER,
        resolve_endpoint,
        role_from_config,
    )
    from ..capture.stream import CaptureStream
    from ..config import POLICY_PINNED
    from ..session import RecordingSession
    from ..store import Origin
    from ..types import MeetingState

    config = _load(args)
    role = role_from_config(config.device_role)

    streams = {}
    for track, flow, policy, pinned in (
        ("mic", FLOW_CAPTURE, config.mic_policy, config.mic_device_id),
        ("system", FLOW_RENDER, config.system_policy, config.system_device_id),
    ):
        try:
            endpoint = resolve_endpoint(
                flow=flow,
                policy_pinned_id=pinned if policy == POLICY_PINNED else "",
                role=role,
            )
        except Exception as exc:
            sys.stderr.write(f"trilha '{track}': {exc}\n")
            continue
        streams[track] = CaptureStream(
            endpoint.id, loopback=(flow == FLOW_RENDER), name=track
        )
        sys.stdout.write(f"  {track:<7} {endpoint.name}\n")

    if not streams:
        sys.stderr.write(
            "Nenhum dispositivo de audio disponivel. Rode 'voxvault doctor'.\n"
        )
        return 1

    store = _open_store(config)
    session = RecordingSession(
        config, title=args.title,
        mic_stream=streams.get("mic"), system_stream=streams.get("system"),
    )
    try:
        latency = session.start()
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    store.create_meeting(
        uid=session.uid, title=session.title, started_at=session.started_at,
        directory=session.directory, origin=Origin.RECORDED,
        state=MeetingState.RECORDING,
    )
    sys.stdout.write(
        f"\nGravando '{session.title}'  (inicio em {latency:.0f} ms)\n"
        f"  {session.directory}\n"
        f"  Ctrl+C para encerrar.\n\n"
    )

    deadline = time.monotonic() + args.seconds if args.seconds > 0 else None
    try:
        while True:
            time.sleep(0.5)
            sys.stdout.write(
                f"\r  {session.duration_ms / 1000:7.1f}s  "
                f"divergencia {session.drift_ms:4d} ms   "
            )
            sys.stdout.flush()
            if deadline is not None and time.monotonic() >= deadline:
                break
    except KeyboardInterrupt:
        pass

    sys.stdout.write("\n\nEncerrando...\n")
    report = session.stop(compress=not args.no_compress)

    from ..pipeline import TranscriptionPipeline

    store.finish_meeting(
        session.uid,
        ended_at=datetime.now(UTC),
        duration_ms=report.duration_ms,
    )
    pipeline = TranscriptionPipeline(config, store)
    try:
        pipeline.enqueue(session.uid)
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")

    sys.stdout.write(
        f"Reuniao {session.uid}\n"
        f"  duracao: {report.duration_ms / 1000:.1f}s\n"
        f"  trilhas: {', '.join(f'{t} {d/1000:.1f}s' for t, d in report.tracks.items())}\n"
        f"  divergencia final: {report.drift_ms} ms\n"
    )
    for warning in report.warnings:
        sys.stdout.write(f"  aviso: {warning}\n")
    sys.stdout.write("\nEnfileirada para transcricao. Rode 'voxvault queue --run'.\n")
    store.close()
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    from ..pipeline import TranscriptionPipeline
    from ..session.importing import import_media

    config = _load(args)
    store = _open_store(config)
    pipeline = TranscriptionPipeline(config, store)
    try:
        uid, directory = import_media(
            config, args.arquivo, title=args.title, store=store,
            submit=pipeline.enqueue,
        )
    finally:
        store.close()

    sys.stdout.write(
        f"Importada como {uid}\n  {directory}\n"
        f"\nEnfileirada para transcricao. Rode 'voxvault queue --run'.\n"
    )
    return 0


def _meeting_json(store, meeting) -> dict:
    """One meeting, with availability and attempt as separate attributes.

    They are not the same thing and never collapse: a meeting being
    reprocessed has a readable transcript AND a running attempt, and a single
    field would have to lie about one of them. A surface reading this can tell
    a failed transcription from a queued one, which a status string could not.
    """
    revision = store.active_revision(meeting.uid)
    return {
        "id": meeting.uid,
        "titulo": meeting.title,
        "inicio": meeting.started_at.isoformat(),
        "fim": meeting.ended_at.isoformat() if meeting.ended_at else None,
        "duracao_ms": meeting.duration_ms,
        "origem": str(meeting.origin),
        "estado_da_gravacao": str(meeting.state),
        "diretorio": meeting.directory,
        "transcricao_disponivel": revision is not None,
        "completude": str(revision.state) if revision else None,
        "revisao_ativa": revision.uid if revision else None,
        "motor": revision.engine_id if revision else None,
        "estado_da_tentativa": str(meeting.attempt_state),
        "motivo_da_falha": meeting.attempt_error or None,
        "tem_audio": bool(available_tracks(Path(meeting.directory))),
    }


def _cmd_list(args: argparse.Namespace) -> int:
    import json

    config = _load(args)
    store = _open_store(config)
    try:
        meetings = store.list_meetings(limit=args.limit)

        if args.json:
            sys.stdout.write(json.dumps(
                {"reunioes": [_meeting_json(store, m) for m in meetings]},
                ensure_ascii=False, indent=2,
            ))
            sys.stdout.write("\n")
            return 0

        if not meetings:
            sys.stdout.write("Nenhuma reuniao guardada ainda.\n")
            return 0
        for meeting in meetings:
            revision = store.active_revision(meeting.uid)
            # Availability and attempt are separate attributes on purpose: a
            # meeting being reprocessed has a readable transcript AND a running
            # attempt, and one field would have to lie about one of them.
            disponivel = (
                f"transcricao {revision.state}" if revision else "sem transcricao"
            )
            tentativa = str(meeting.attempt_state)
            sys.stdout.write(
                f"{meeting.uid[:8]}  {meeting.started_at:%d/%m/%Y %H:%M}  "
                f"{meeting.duration_ms / 1000:7.1f}s  {meeting.title[:40]:<40}  "
                f"{disponivel} | tentativa: {tentativa}\n"
            )
            if meeting.attempt_error:
                sys.stdout.write(f"{'':>10}motivo: {meeting.attempt_error}\n")
    finally:
        store.close()
    return 0


def _resolve_uid(store, prefix: str) -> str:
    """Accept a unique prefix, the way git accepts a short hash."""
    matches = [m.uid for m in store.iter_meetings() if m.uid.startswith(prefix)]
    if not matches:
        raise SystemExit(f"Nenhuma reuniao comeca com '{prefix}'.")
    if len(matches) > 1:
        raise SystemExit(
            f"'{prefix}' e ambiguo: {len(matches)} reunioes comecam assim."
        )
    return matches[0]


def _cmd_show(args: argparse.Namespace) -> int:
    import json

    config = _load(args)
    store = _open_store(config)
    try:
        uid = _resolve_uid(store, args.uid)
        meeting = store.get_meeting(uid)
        revision = store.active_revision(uid)
        entries = store.timeline(uid)

        if args.json:
            sys.stdout.write(json.dumps({
                "uid": meeting.uid,
                "titulo": meeting.title,
                "inicio": meeting.started_at.isoformat(),
                "duracao_ms": meeting.duration_ms,
                "revisao_ativa": revision.uid if revision else None,
                "estado_da_tentativa": str(meeting.attempt_state),
                "segmentos": [
                    {"inicio_ms": e.start_ms, "fim_ms": e.end_ms,
                     "falante": str(e.speaker), "trilha": str(e.track),
                     "texto": e.text}
                    for e in entries
                ],
            }, ensure_ascii=False, indent=2))
            sys.stdout.write("\n")
            return 0

        sys.stdout.write(f"\n{meeting.title}\n")
        sys.stdout.write(f"{meeting.started_at:%d/%m/%Y %H:%M} | "
                         f"{meeting.duration_ms / 1000:.1f}s\n")
        if revision is None:
            sys.stdout.write(
                f"\nSem transcricao ativa. Tentativa: {meeting.attempt_state}\n"
            )
            if meeting.attempt_error:
                sys.stdout.write(f"Motivo: {meeting.attempt_error}\n")
            return 0
        sys.stdout.write(f"revisao {revision.uid[:8]} ({revision.state}) | "
                         f"motor {revision.engine_id}\n\n")
        for entry in entries:
            sys.stdout.write(
                f"[{_stamp(entry.start_ms)}] {entry.speaker}: {entry.text}\n"
            )
    finally:
        store.close()
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    import json

    config = _load(args)
    store = _open_store(config)
    try:
        hits = store.search(args.termo, scope=args.scope, limit=args.limit)

        if args.json:
            items = []
            for hit in hits:
                # A note has no instant, no track and no speaker. Filling those
                # with zeros would render an interpretation as though somebody
                # had said it at the start of the meeting.
                item = {
                    "reuniao": hit.meeting_uid,
                    "titulo": hit.meeting_title,
                    "natureza": getattr(hit, "nature", "transcricao"),
                    "recorte": hit.excerpt,
                    "texto": hit.text,
                }
                if hasattr(hit, "start_ms"):
                    item["inicio_ms"] = hit.start_ms
                    item["fim_ms"] = hit.end_ms
                    item["falante"] = hit.speaker
                    item["trilha"] = hit.track
                nota = getattr(hit, "note_uid", None) or getattr(hit, "uid", None)
                if nota:
                    item["nota"] = nota
                    item["tipo"] = str(getattr(hit, "kind", ""))
                items.append(item)
            sys.stdout.write(json.dumps(
                {"termo": args.termo, "escopo": args.scope, "resultados": items},
                ensure_ascii=False, indent=2,
            ))
            sys.stdout.write("\n")
            return 0

        if not hits:
            sys.stdout.write(f"Nada encontrado para '{args.termo}'.\n")
            return 0
        for hit in hits:
            instante = (
                f"[{_stamp(hit.start_ms)}]" if hasattr(hit, "start_ms") else "[nota   ]"
            )
            sys.stdout.write(
                f"{hit.meeting_uid[:8]}  {instante}  "
                f"{hit.meeting_title[:30]:<30}  {hit.excerpt.strip()[:80]}\n"
            )
    finally:
        store.close()
    return 0


def _cmd_rename(args: argparse.Namespace) -> int:
    config = _load(args)
    store = _open_store(config)
    try:
        uid = _resolve_uid(store, args.uid)
        store.rename_meeting(uid, args.title)
        # The title is in the readable export's heading, so the file on disk
        # would otherwise keep the old one.
        try:
            from ..store import regenerate_exports

            if store.get_meeting(uid).active_revision_id is not None:
                regenerate_exports(store, uid)
        except Exception as exc:
            sys.stderr.write(f"aviso: exportacoes nao regeradas: {exc}\n")
        sys.stdout.write(f"{uid} agora se chama '{args.title}'.\n")
    finally:
        store.close()
    return 0


def _cmd_remove_audio(args: argparse.Namespace) -> int:
    """Delete a meeting's audio, keeping everything that was said.

    Refused when there is no transcript: removing the audio then would leave
    nothing at all of the meeting, which is not a trade anybody means to make.
    """
    config = _load(args)
    store = _open_store(config)
    try:
        uid = _resolve_uid(store, args.uid)
        meeting = store.get_meeting(uid)
        directory = Path(meeting.directory)
        tracks = available_tracks(directory)

        if not tracks:
            sys.stdout.write(f"{uid} ja nao tem audio em disco.\n")
            return 0

        if str(meeting.state) == "gravando":
            sys.stderr.write(
                "A reuniao ainda esta gravando. Encerre a gravacao antes.\n"
            )
            return 1

        if store.active_revision(uid) is None:
            sys.stderr.write(
                f"'{meeting.title}' nao tem transcricao. Remover o audio agora "
                f"apagaria a reuniao inteira, sem deixar nada do que foi dito. "
                f"Transcreva primeiro, com: voxvault queue --run\n"
            )
            return 1

        total = sum(p.stat().st_size for p in tracks.values())
        if not args.yes:
            sys.stdout.write(
                f"Remover {len(tracks)} arquivo(s) de audio de '{meeting.title}', "
                f"{total / (1024 * 1024):.1f} MB.\n"
                f"A transcricao e as notas permanecem. O audio nao volta.\n"
                f"Confirme com --yes.\n"
            )
            return 0

        for track, path in sorted(tracks.items()):
            path.unlink(missing_ok=True)
            sys.stdout.write(f"  removida a trilha '{track}'\n")
        sys.stdout.write(
            f"Audio de {uid} removido. A transcricao continua legivel, e "
            f"reprocessar deixa de ser possivel.\n"
        )
    finally:
        store.close()
    return 0


def _cmd_notes(args: argparse.Namespace) -> int:
    """Notes are interpretation, so nothing here touches a transcript.

    Every write path ends by regenerating the exports, because the readable
    and structured files carry the notes and stop being true the moment one
    changes. A meeting with no active revision has nothing to export and is
    skipped rather than failed.
    """
    from ..store import NoteAuthor, regenerate_exports

    config = _load(args)
    store = _open_store(config)
    try:
        touched = ""
        if args.notes_command == "list":
            uid = _resolve_uid(store, args.uid)
            notes = store.notes_of(uid)
            if not notes:
                sys.stdout.write("Nenhuma nota nessa reuniao.\n")
                return 0
            for note in notes:
                resumo = " ".join(note.content.split())
                sys.stdout.write(
                    f"{note.uid[:8]}  {note.created_at.astimezone():%d/%m/%Y %H:%M}  "
                    f"{note.kind!s:<11}  {note.author.describe()[:24]:<24}  "
                    f"{resumo[:60]}\n"
                )
            return 0

        if args.notes_command == "add":
            touched = _resolve_uid(store, args.uid)
            note = store.create_note(
                touched,
                kind=args.type,
                content=args.content,
                author=NoteAuthor.user(),
            )
            sys.stdout.write(
                f"Nota {note.uid} criada em {touched} (tipo {note.kind}).\n"
            )
        elif args.notes_command == "update":
            note = store.update_note(
                store.resolve_note_uid(args.id), content=args.content
            )
            touched = note.meeting_uid
            sys.stdout.write(f"Nota {note.uid} atualizada.\n")
        else:
            note_uid = store.resolve_note_uid(args.id)
            note = store.get_note(note_uid)
            touched = note.meeting_uid
            store.delete_note(note_uid)
            sys.stdout.write(f"Nota {note_uid} removida de {touched}.\n")

        if store.active_revision(touched) is not None:
            regenerate_exports(store, touched)
            sys.stdout.write("Exportacoes regeneradas.\n")
    finally:
        store.close()
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    from ..store import current_export_paths

    config = _load(args)
    store = _open_store(config)
    try:
        uid = _resolve_uid(store, args.uid)
        # Regenerates when the files diverge from the active revision, and
        # returns nothing rather than handing back a stale export.
        paths = current_export_paths(store, uid, regenerate=True)
        if paths is None:
            sys.stderr.write(
                "A reuniao nao tem revisao ativa, entao nao ha o que exportar.\n"
            )
            return 1
        for path in paths:
            sys.stdout.write(f"{path}\n")
    finally:
        store.close()
    return 0


def _cmd_reprocess(args: argparse.Namespace) -> int:
    from ..pipeline import TranscriptionPipeline

    config = _load(args)
    store = _open_store(config)
    try:
        uid = _resolve_uid(store, args.uid)
        pipeline = TranscriptionPipeline(config, store)
        pipeline.enqueue(uid)
        sys.stdout.write(
            f"{uid} enfileirada para nova transcricao.\n"
            f"A transcricao atual continua legivel ate a nova ser publicada.\n"
        )
    finally:
        store.close()
    return 0


def _cmd_queue(args: argparse.Namespace) -> int:
    import time

    from ..pipeline import TranscriptionPipeline

    config = _load(args)
    store = _open_store(config)
    try:
        pipeline = TranscriptionPipeline(
            config, store,
            on_event=lambda kind, detail: sys.stdout.write(f"  {kind}: {detail}\n"),
        )
        recovered = pipeline.recover_pending()
        if recovered:
            sys.stdout.write(
                f"{recovered} tentativa(s) interrompida(s) devolvida(s) a fila.\n"
            )
        pending = pipeline.pending()
        if not pending:
            sys.stdout.write("Fila vazia.\n")
            return 0

        sys.stdout.write(f"{len(pending)} reuniao(oes) na fila:\n")
        for meeting in pending:
            sys.stdout.write(
                f"  {meeting.uid[:8]}  {meeting.title[:40]:<40}  "
                f"{meeting.attempt_state}\n"
            )
        if not args.run:
            sys.stdout.write("\nUse --run para processar.\n")
            return 0

        sys.stdout.write("\nProcessando...\n")
        pipeline.start()
        try:
            while pipeline.pending():
                time.sleep(0.5)
            pipeline.wait_idle(timeout=30)
        except KeyboardInterrupt:
            sys.stdout.write("\nInterrompido; a fila e retomavel.\n")
        finally:
            pipeline.stop()
        sys.stdout.write("Fila processada.\n")
    finally:
        store.close()
    return 0


def _cmd_transcribe(args: argparse.Namespace) -> int:
    import json
    import time

    from ..engine import build_engine

    config = _load(args)
    path: Path = args.arquivo
    if not path.exists():
        sys.stderr.write(f"Arquivo nao encontrado: {path}\n")
        return 2

    engine = build_engine(config, model=args.model)
    started = time.monotonic()
    result = engine.transcribe(
        path,
        language=args.language or config.language,
        vocabulary=args.vocabulary if args.vocabulary is not None else config.vocabulary,
    )
    elapsed = time.monotonic() - started

    if args.json:
        sys.stdout.write(json.dumps({
            "arquivo": str(path),
            "motor": result.engine.identifier(),
            "idioma": result.language,
            "duracao_ms": result.duration_ms,
            "segundos_de_processamento": round(elapsed, 2),
            "segmentos": [
                {"inicio_ms": s.start_ms, "fim_ms": s.end_ms, "texto": s.text}
                for s in result.segments
            ],
        }, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        return 0

    for segment in result.segments:
        sys.stdout.write(f"[{_stamp(segment.start_ms)}] {segment.text}\n")
    speed = (result.duration_ms / 1000 / elapsed) if elapsed > 0 else 0
    sys.stdout.write(
        f"\n{len(result.segments)} segmento(s) | motor {result.engine.identifier()} | "
        f"{elapsed:.1f}s de processamento para {result.duration_ms / 1000:.1f}s de "
        f"audio ({speed:.1f}x tempo real)\n"
    )
    return 0


def _bench_configurations(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    """The configurations to compare, from a file or from the command line.

    The file form exists because a comparison worth making twice is worth
    writing down: it carries a label per configuration, so the report names
    them the way the person thinks of them rather than by model string.
    """
    import json

    if args.config:
        try:
            raw = json.loads(args.config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Nao foi possivel ler as configuracoes em '{args.config}': {exc}"
            ) from None
        entries = raw.get("configuracoes") if isinstance(raw, dict) else raw
        if not isinstance(entries, list) or not entries:
            raise ValueError(
                f"'{args.config}' precisa conter uma lista 'configuracoes', "
                f"cada item com 'modelo' e opcionalmente 'rotulo'."
            )
        models, labels = [], []
        for entry in entries:
            if isinstance(entry, str):
                models.append(entry)
                labels.append(entry)
                continue
            modelo = entry.get("modelo") or entry.get("model")
            if not modelo:
                raise ValueError(
                    f"Uma das configuracoes em '{args.config}' nao nomeia o modelo."
                )
            models.append(modelo)
            labels.append(entry.get("rotulo") or entry.get("label") or modelo)
        return models, labels

    models = [m.strip() for m in (args.models or "").split(",") if m.strip()]
    return models, models


def _cmd_bench(args: argparse.Namespace) -> int:
    from ..bench import run_benchmark

    config = _load(args)
    path: Path = args.arquivo
    if not path.exists():
        sys.stderr.write(f"Arquivo nao encontrado: {path}\n")
        return 2

    models, labels = _bench_configurations(args)
    if not models:
        sys.stderr.write(
            "Indique as configuracoes a comparar, com --config ARQUIVO ou "
            "--models a,b.\n"
        )
        return 2

    sys.stdout.write(
        f"Comparando {len(models)} configuracao(oes) sobre {path.name}.\n"
        f"Cada uma roda em processo proprio, para que a memoria de GPU seja "
        f"liberada entre elas.\n\n"
    )
    report = run_benchmark(
        config, path, models, labels=labels,
        language=args.language or config.language,
        vocabulary=args.vocabulary if args.vocabulary is not None else config.vocabulary,
    )

    for run in report.runs:
        if run.ok:
            peak = f"{run.peak_gpu_mb} MB" if run.peak_gpu_mb is not None else "n/a"
            sys.stdout.write(
                f"  [ok]    {run.label:<20} {run.realtime_factor:>5.1f}x tempo real | "
                f"carga {run.load_seconds:5.1f}s | decode {run.decode_seconds:5.1f}s | "
                f"pico {peak}\n"
            )
        else:
            sys.stdout.write(f"  [FALHA] {run.label:<20} {run.error}\n")

    sys.stdout.write(f"\nRelatorio: {report.output_dir / 'relatorio.md'}\n")
    return 1 if report.any_failed else 0


def _cmd_config(args: argparse.Namespace) -> int:
    from ..config import load_config, user_config_path, write_config_file

    if not args.atribuicao:
        config = _load(args)
        if args.json:
            import json

            sys.stdout.write(json.dumps({
                "arquivo": str(user_config_path()),
                "valores": {
                    nome: {
                        "valor": str(getattr(config, nome)),
                        "origem": config.source_of(nome),
                    }
                    for nome in sorted(config.sources)
                },
            }, ensure_ascii=False, indent=2))
            sys.stdout.write("\n")
            return 0
        sys.stdout.write(f"arquivo: {user_config_path()}\n\n")
        for name in sorted(config.sources):
            sys.stdout.write(
                f"  {name} = {getattr(config, name)}  <- {config.source_of(name)}\n"
            )
        return 0

    values: dict[str, str] = {}
    for item in args.atribuicao:
        field, sep, value = item.partition("=")
        if not sep:
            sys.stderr.write(f"Esperado CAMPO=VALOR, recebido: {item}\n")
            return 2
        values[field.strip()] = value.strip()

    path = write_config_file(values)
    sys.stdout.write(f"Gravado em {path}\n")
    for name in values:
        sys.stdout.write(f"  {name} = {getattr(load_config(), name)}\n")
    sys.stdout.write(
        "\nOs processos ja em execucao mantem a configuracao anterior. "
        "Reinicie o servico residente e os clientes MCP para que leiam a nova.\n"
    )
    return 0


def _cmd_devices(args: argparse.Namespace) -> int:
    try:
        from ..capture.devices import format_endpoints, list_endpoints
    except Exception as exc:
        sys.stderr.write(f"Backend de captura indisponivel: {exc}\n")
        return 1

    endpoints = list_endpoints()

    if args.json:
        import json

        sys.stdout.write(json.dumps({
            "dispositivos": [
                {
                    "id": e.id,
                    "nome": e.name,
                    "fluxo": e.flow,
                    "padrao_de": sorted(e.default_for),
                    "estado": e.state_name,
                    "formato": e.form_factor_name,
                    "parece_fone": e.looks_like_headphones,
                }
                for e in endpoints
            ],
        }, ensure_ascii=False, indent=2))
        sys.stdout.write("\n")
        return 0

    sys.stdout.write(format_endpoints(endpoints))
    sys.stdout.write("\n")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from ..service import ResidentService, ServiceBusy
    from ..service.rendezvous import live_rendezvous, rendezvous_path

    config = _load(args)

    if args.stop:
        return _stop_service(force=args.force)

    if args.status:
        found = live_rendezvous()
        if found is None:
            sys.stdout.write(
                f"Nenhum servico em execucao.\n  ponto de encontro: "
                f"{rendezvous_path()}\n"
            )
            return 1
        sys.stdout.write(
            f"Servico em execucao\n"
            f"  endereco:  {found.endereco}\n"
            f"  processo:  {found.pid}\n"
            f"  dados:     {found.diretorio_de_dados}\n"
        )
        return 0

    service = ResidentService(config)
    try:
        sys.stdout.write(
            f"Servico residente do VoxVault\n"
            f"  dados: {config.data_dir}  <- {config.source_of('data_dir')}\n"
        )
        service.claim_machine()
    except ServiceBusy as exc:
        sys.stderr.write(f"{exc}\n")
        found = live_rendezvous()
        if found is not None:
            sys.stderr.write(f"O servico em execucao atende em {found.endereco}.\n")
        return 1

    # The claim is re-taken inside serve(); release this one so it is not held
    # twice by the same process.
    if service._claim is not None:
        service._claim.release()
        service._claim = None

    try:
        return service.serve()
    except KeyboardInterrupt:
        sys.stdout.write("\nEncerrando o servico.\n")
        service.shutdown()
        return 0


def _cmd_detect(args: argparse.Namespace) -> int:
    import json
    import time

    from ..detect import (
        SUSTAIN_APP_S,
        SUSTAIN_BROWSER_S,
        MeetingDetector,
        active_users,
        candidates,
    )

    detector = MeetingDetector()
    available, reason = detector.available()
    if not available:
        sys.stderr.write(f"{reason}\n")
        return 1

    def snapshot() -> dict:
        return {
            "segurando_o_microfone": [u.describe for u in active_users()],
            "candidatos": [
                {"aplicativo": c.name, "sinal": str(c.signal),
                 "periodo_s": c.sustain_s}
                for c in candidates()
            ],
        }

    if not args.watch:
        estado = snapshot()
        if args.json:
            sys.stdout.write(json.dumps(estado, ensure_ascii=False, indent=2))
            sys.stdout.write("\n")
            return 0
        if not estado["segurando_o_microfone"]:
            sys.stdout.write("Ninguem esta usando o microfone agora.\n")
            return 0
        sys.stdout.write("Usando o microfone:\n")
        for nome in estado["segurando_o_microfone"]:
            sys.stdout.write(f"  {nome}\n")
        if estado["candidatos"]:
            sys.stdout.write("\nReconhecidos como possivel reuniao:\n")
            for c in estado["candidatos"]:
                sys.stdout.write(
                    f"  {c['aplicativo']}  ({c['sinal']}, precisa de "
                    f"{c['periodo_s']:.0f}s sustentados)\n"
                )
        else:
            sys.stdout.write("\nNenhum deles e um aplicativo de reuniao conhecido.\n")
        return 0

    sys.stdout.write(
        f"Observando por {args.watch:.0f}s. Aplicativo reconhecido precisa de "
        f"{SUSTAIN_APP_S:.0f}s sustentados; navegador, {SUSTAIN_BROWSER_S:.0f}s.\n"
        f"Nada do conteudo e lido: apenas qual processo detem o microfone.\n\n"
    )
    deadline = time.monotonic() + args.watch
    anterior = None
    while time.monotonic() < deadline:
        deteccao = detector.observe()
        atual = deteccao.name if deteccao else None
        if atual != anterior:
            if deteccao is not None:
                sys.stdout.write(
                    f"  reuniao detectada: {deteccao.name} "
                    f"({'sinal fraco' if deteccao.weak else 'aplicativo reconhecido'})\n"
                )
            else:
                sys.stdout.write("  a reuniao terminou\n")
            anterior = atual
        time.sleep(2.0)
    sys.stdout.write("\nFim da observacao.\n")
    return 0


def _stop_service(*, force: bool) -> int:
    """Ask the running service to end, and say plainly when it refuses.

    A refusal is the service protecting work: it will not end with a recording
    open or a queue pending, because ending would throw both away. ``--force``
    is for when the person has decided otherwise, and it says what that costs.
    """
    import json
    import subprocess
    import urllib.error
    import urllib.request

    from ..service.rendezvous import SECRET_HEADER, live_rendezvous

    found = live_rendezvous()
    if found is None:
        sys.stdout.write("Nenhum servico em execucao.\n")
        return 0

    request = urllib.request.Request(
        f"http://{found.endereco}/encerrar", data=b"{}", method="POST"
    )
    request.add_header(SECRET_HEADER, found.segredo)
    request.add_header("Content-Type", "application/json")

    detalhe = ""
    try:
        with urllib.request.urlopen(request, timeout=10):
            sys.stdout.write(f"Servico {found.pid} encerrando.\n")
            return 0
    except urllib.error.HTTPError as exc:
        try:
            detalhe = json.loads(exc.read()).get("erro", "")
        except Exception:
            detalhe = str(exc)
        if exc.code == 409 and not force:
            sys.stderr.write(
                f"{detalhe}\n"
                f"Use --force para encerrar assim mesmo: a gravacao em "
                f"andamento e finalizada e a fila continua de onde parou na "
                f"proxima inicializacao.\n"
            )
            return 1
    except urllib.error.URLError as exc:
        detalhe = f"o servico nao respondeu ({exc.reason})"
        if not force:
            sys.stderr.write(f"{detalhe}\n")
            return 1

    if not force:
        sys.stderr.write(f"{detalhe}\n")
        return 1

    # Terminating the tree is what releases the machine-wide claim; a child
    # left behind would keep the next start from ever succeeding.
    result = subprocess.run(
        ["taskkill", "/PID", str(found.pid), "/T", "/F"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        sys.stdout.write(f"Servico {found.pid} encerrado a forca.\n")
        return 0
    sys.stderr.write(f"Nao foi possivel encerrar o processo {found.pid}.\n")
    return 1


def _cmd_mcp(args: argparse.Namespace) -> int:
    from ..mcp.install import (
        apply,
        inspect,
        known_clients,
        render_snippet,
    )

    config = _load(args)
    sys.stdout.write(
        f"Servidor MCP do VoxVault\n"
        f"  diretorio de dados: {config.data_dir}  <- {config.source_of('data_dir')}\n\n"
    )

    pendentes = []
    for client in known_clients():
        estado, detalhe = inspect(client)
        sys.stdout.write(f"  {client.name}\n")
        sys.stdout.write(f"    {client.path}\n")
        sys.stdout.write(f"    {estado}: {detalhe}\n")
        if estado in {"nao registrado", "divergente", "ausente"}:
            pendentes.append(client)
        sys.stdout.write("\n")

    if not pendentes:
        sys.stdout.write("Tudo registrado.\n")
        return 0

    if not args.apply:
        sys.stdout.write(
            "Trecho a acrescentar em 'mcpServers':\n\n"
            f"{render_snippet()}\n\n"
            "Para o VoxVault gravar isso sozinho, rode de novo com --apply. "
            "O arquivo atual e copiado antes de qualquer alteracao.\n"
        )
        return 0

    for client in pendentes:
        try:
            path = apply(client)
        except ValueError as exc:
            sys.stderr.write(f"  {client.name}: {exc}\n")
            continue
        sys.stdout.write(f"  {client.name} registrado em {path}\n")
    sys.stdout.write(
        "\nReinicie o cliente para que ele carregue o servidor.\n"
    )
    return 0


def _stamp(ms: int) -> str:
    seconds, millis = divmod(int(ms), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _force_utf8_output() -> None:
    """Make accented Portuguese survive the Windows console.

    A terminal still on a legacy code page turns every transcript into
    mojibake, which makes the tool look broken when only the encoding is.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        sys.stderr.write("\nInterrompido.\n")
        return 130
    except Exception as exc:
        from ..errors import VoxVaultError

        if isinstance(exc, VoxVaultError):
            sys.stderr.write(f"{exc}\n")
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
