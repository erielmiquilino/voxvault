"""Entry point for the MCP server.

Deliberately thin, and deliberately free of heavy imports. An MCP client
launches this on every session start, and the difference between a server that
appears instantly and one that takes several seconds is the difference between
a tool people use and one they turn off.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if "--check" in argv:
        return _check()

    from .server import build_server

    build_server().run(transport="stdio")
    return 0


def _check() -> int:
    """Report whether the server can reach its store, without serving.

    Meant for `voxvault doctor` and for anyone debugging a client that shows
    the server as failed: it answers "can it work here" without needing a
    client to ask.
    """
    from ..config import load_config

    config = load_config()
    sys.stdout.write(f"diretorio de dados: {config.data_dir}\n")
    sys.stdout.write(f"origem do valor:    {config.source_of('data_dir')}\n")
    sys.stdout.write(f"banco:              {config.db_path}\n")

    if not config.db_path.exists():
        sys.stdout.write(
            "\nO banco ainda nao existe. Grave ou importe uma reuniao "
            "primeiro; o servidor responde, mas o acervo esta vazio.\n"
        )
        return 0

    try:
        from ..store import TranscriptStore

        store = TranscriptStore(config.db_path)
    except Exception as exc:
        sys.stderr.write(f"\nNao foi possivel abrir o armazenamento: {exc}\n")
        return 1

    try:
        total = sum(1 for _ in store.iter_meetings())
        sys.stdout.write(f"esquema:            versao {store.schema_version}\n")
        sys.stdout.write(f"reunioes:           {total}\n")
        sys.stdout.write("\nO servidor MCP pode operar neste ambiente.\n")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
