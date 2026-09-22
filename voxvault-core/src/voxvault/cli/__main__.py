"""voxvault command line.

Uses argparse from the standard library rather than a CLI framework: a third
party parser costs import time on every invocation, and this tool exists
because a competitor was unusable on Windows. Every handler imports what it
needs lazily, so `--help` never loads an inference runtime.
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
    doctor.add_argument(
        "-v", "--verbose", action="store_true",
        help="Mostra a origem de todos os valores de configuracao.",
    )
    doctor.set_defaults(handler=_cmd_doctor)

    transcribe = sub.add_parser(
        "transcribe", help="Transcreve um arquivo de audio ou video."
    )
    transcribe.add_argument("arquivo", type=Path)
    transcribe.add_argument("--model", help="Modelo a usar (padrao: o configurado).")
    transcribe.add_argument("--language", default=None, help="Codigo de idioma.")
    transcribe.add_argument(
        "--vocabulary", default=None,
        help="Texto com nomes, siglas e jargao para orientar a transcricao.",
    )
    transcribe.add_argument(
        "--json", action="store_true", help="Emite JSON em vez de texto legivel."
    )
    transcribe.set_defaults(handler=_cmd_transcribe)

    bench = sub.add_parser(
        "bench",
        help="Compara varias configuracoes de modelo sobre o mesmo audio.",
    )
    bench.add_argument("arquivo", type=Path)
    bench.add_argument(
        "--models", default="large-v3,large-v3-turbo",
        help="Modelos separados por virgula.",
    )
    bench.add_argument("--language", default=None)
    bench.add_argument("--vocabulary", default=None)
    bench.set_defaults(handler=_cmd_bench)

    config_cmd = sub.add_parser(
        "config", help="Mostra ou altera a configuracao compartilhada."
    )
    config_cmd.add_argument(
        "atribuicao", nargs="*", metavar="CAMPO=VALOR",
        help="Sem argumentos, mostra a configuracao efetiva e a origem de cada valor.",
    )
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


# -- handlers ----------------------------------------------------------


def _cmd_doctor(args: argparse.Namespace) -> int:
    from ..doctor import format_report, run_diagnostics  # noqa: PLC0415

    config = _load(args)
    report = run_diagnostics(config)
    sys.stdout.write(format_report(report, config, verbose=args.verbose))
    return 1 if report.failed else 0


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
