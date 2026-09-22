"""Registering the server with an MCP client.

Writing into another application's configuration is not something to do
quietly, so this reports what it would change and only writes when told to.
The existing file is backed up first: a client that cannot parse its own
configuration loses every server the person had, not just ours.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

SERVER_KEY = "voxvault"


@dataclass(slots=True)
class ClientConfig:
    name: str
    path: Path

    @property
    def exists(self) -> bool:
        return self.path.is_file()


def known_clients() -> list[ClientConfig]:
    """Where the MCP clients on this machine keep their configuration."""
    import os

    appdata = os.environ.get("APPDATA", "")
    home = Path.home()
    candidates = [
        ClientConfig("Claude Desktop", Path(appdata) / "Claude" / "claude_desktop_config.json")
        if appdata else None,
        ClientConfig("Codex", home / ".codex" / "config.json"),
    ]
    return [c for c in candidates if c is not None]


def server_entry() -> dict:
    """What to add under ``mcpServers``.

    The interpreter is named by absolute path rather than by relying on PATH:
    the client launches this from its own environment, which is not the one a
    terminal has.
    """
    return {
        "command": sys.executable,
        "args": ["-m", "voxvault.mcp"],
    }


def render_snippet() -> str:
    return json.dumps(
        {"mcpServers": {SERVER_KEY: server_entry()}}, indent=2, ensure_ascii=False
    )


def inspect(client: ClientConfig) -> tuple[str, str]:
    """Report whether this server is registered, and how it looks now."""
    if not client.exists:
        return "ausente", f"o arquivo nao existe: {client.path}"
    try:
        raw = json.loads(client.path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return "ilegivel", f"o arquivo nao e JSON valido ({exc})"

    servers = raw.get("mcpServers") or {}
    if SERVER_KEY not in servers:
        return "nao registrado", f"{len(servers)} servidor(es) registrado(s)"

    current = servers[SERVER_KEY]
    if current == server_entry():
        return "registrado", "a entrada esta correta"
    return "divergente", f"registrado com outro comando: {current.get('command', '?')}"


def apply(client: ClientConfig) -> Path:
    """Add or correct the entry, keeping a backup of what was there."""
    if not client.exists:
        raw: dict = {}
        client.path.parent.mkdir(parents=True, exist_ok=True)
    else:
        try:
            raw = json.loads(client.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"O arquivo de configuracao de {client.name} nao e JSON valido "
                f"({exc}). Nada foi alterado -- corrigir o arquivo a mao e mais "
                f"seguro do que sobrescreve-lo."
            ) from None
        backup = client.path.with_suffix(client.path.suffix + ".voxvault-backup")
        shutil.copy2(client.path, backup)

    raw.setdefault("mcpServers", {})[SERVER_KEY] = server_entry()
    temporary = client.path.with_suffix(client.path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(client.path)
    return client.path
