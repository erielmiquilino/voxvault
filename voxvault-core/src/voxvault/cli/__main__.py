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
from pathlib import Path

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
    listar.set_defaults(handler=_cmd_list)

    show = sub.add_parser("show", help="Mostra a transcricao de uma reuniao.")
    show.add_argument("uid")
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=_cmd_show)

    search = sub.add_parser("search", help="Busca no historico de transcricoes.")
    search.add_argument("termo")
    search.add_argument("-n", "--limit", type=int, default=20)
    search.set_defaults(handler=_cmd_search)

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

    bench = sub.add_parser(
        "bench", help="Compara varias configuracoes de modelo sobre o mesmo audio."
    )
    bench.add_argument("arquivo", type=Path)
    bench.add_argument("--models", default="large-v3,large-v3-turbo")
    bench.add_argument("--language", default=None)
    bench.add_argument("--vocabulary", default=None)
    bench.set_defaults(handler=_cmd_bench)

    config_cmd = sub.add_parser(
        "config", help="Mostra ou altera a configuracao compartilhada."
    )
    config_cmd.add_argument("atribuicao", nargs="*", metavar="CAMPO=VALOR")
    config_cmd.set_defaults(handler=_cmd_config)

    devices = sub.add_parser(
        "devices", help="Lista os dispositivos de audio e os padroes por papel."
    )
    devices.set_defaults(handler=_cmd_devices)

    return parser


def _load(args: argparse.Namespace):
    from ..config import load_config  # noqa: PLC0415

    overrides = {}
    if getattr(args, "data_dir", None):
        overrides["data_dir"] = args.data_dir
    return load_config(overrides)


def _open_store(config):
    from ..store import TranscriptStore  # noqa: PLC0415

    config.data_dir.mkdir(parents=True, exist_ok=True)
    return TranscriptStore(config.db_path)


# -- handlers ----------------------------------------------------------


def _cmd_doctor(args: argparse.Namespace) -> int:
    from ..doctor import format_report, run_diagnostics  # noqa: PLC0415

    config = _load(args)
    report = run_diagnostics(config)
    sys.stdout.write(format_report(report, config, verbose=args.verbose))
    return 1 if report.failed else 0


def _cmd_record(args: argparse.Namespace) -> int:
    import time  # noqa: PLC0415
    from datetime import datetime, timezone  # noqa: PLC0415

    from ..capture.devices import FLOW_CAPTURE, FLOW_RENDER, resolve_endpoint, role_from_config  # noqa: PLC0415
    from ..capture.stream import CaptureStream  # noqa: PLC0415
    from ..config import POLICY_PINNED  # noqa: PLC0415
    from ..session import RecordingSession  # noqa: PLC0415
    from ..store import Origin  # noqa: PLC0415
    from ..types import MeetingState  # noqa: PLC0415

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

    from ..pipeline import TranscriptionPipeline  # noqa: PLC0415

    store.finish_meeting(
        session.uid,
        ended_at=datetime.now(timezone.utc),
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
    from ..pipeline import TranscriptionPipeline  # noqa: PLC0415
    from ..session.importing import import_media  # noqa: PLC0415

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


def _cmd_list(args: argparse.Namespace) -> int:
    config = _load(args)
    store = _open_store(config)
    try:
        meetings = store.list_meetings(limit=args.limit)
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
    import json  # noqa: PLC0415

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
    config = _load(args)
    store = _open_store(config)
    try:
        hits = store.search(args.termo, limit=args.limit)
        if not hits:
            sys.stdout.write(f"Nada encontrado para '{args.termo}'.\n")
            return 0
        for hit in hits:
            sys.stdout.write(
                f"{hit.meeting_uid[:8]}  [{_stamp(hit.start_ms)}]  "
                f"{hit.meeting_title[:30]:<30}  {hit.excerpt.strip()[:80]}\n"
            )
    finally:
        store.close()
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    from ..store import current_export_paths  # noqa: PLC0415

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
    from ..pipeline import TranscriptionPipeline  # noqa: PLC0415

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
    import time  # noqa: PLC0415

    from ..pipeline import TranscriptionPipeline  # noqa: PLC0415

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
    import json  # noqa: PLC0415
    import time  # noqa: PLC0415

    from ..engine import build_engine  # noqa: PLC0415

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


def _cmd_bench(args: argparse.Namespace) -> int:
    from ..bench import run_benchmark  # noqa: PLC0415

    config = _load(args)
    path: Path = args.arquivo
    if not path.exists():
        sys.stderr.write(f"Arquivo nao encontrado: {path}\n")
        return 2

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        sys.stderr.write("Indique ao menos um modelo com --models.\n")
        return 2

    sys.stdout.write(
        f"Comparando {len(models)} configuracao(oes) sobre {path.name}.\n"
        f"Cada uma roda em processo proprio, para que a memoria de GPU seja "
        f"liberada entre elas.\n\n"
    )
    report = run_benchmark(
        config, path, models,
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
    from ..config import load_config, user_config_path, write_config_file  # noqa: PLC0415

    if not args.atribuicao:
        config = _load(args)
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
        from ..capture.devices import format_endpoints, list_endpoints  # noqa: PLC0415
    except Exception as exc:
        sys.stderr.write(f"Backend de captura indisponivel: {exc}\n")
        return 1

    sys.stdout.write(format_endpoints(list_endpoints()))
    sys.stdout.write("\n")
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
        import ctypes  # noqa: PLC0415

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
        from ..errors import VoxVaultError  # noqa: PLC0415

        if isinstance(exc, VoxVaultError):
            sys.stderr.write(f"{exc}\n")
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
