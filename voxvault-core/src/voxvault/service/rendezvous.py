"""Where every surface finds the running service.

The service binds an ephemeral loopback port and mints a secret for that start,
then writes both to a fixed per-user file. That file is the whole discovery
mechanism, and it sits beside the configuration rather than beside the data:
the app, the command line and a diagnostic all have to find a service that
somebody else started, and only one of them knows the data directory.

The secret is not security theatre against a local attacker -- anything running
as this user can read the file. It is there so that a stale client, or another
build, cannot drive a service it was never handed to.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from ..config import user_config_path

FILE_NAME = "servico.json"
SECRET_HEADER = "X-VoxVault-Token"


def rendezvous_path() -> Path:
    """Fixed, per user, and never derived from a working directory."""
    return user_config_path().parent / FILE_NAME


@dataclass(slots=True)
class Rendezvous:
    endereco: str
    segredo: str
    pid: int
    diretorio_de_dados: str

    def to_dict(self) -> dict:
        return {
            "endereco": self.endereco,
            "segredo": self.segredo,
            "pid": self.pid,
            "diretorio_de_dados": self.diretorio_de_dados,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Rendezvous:
        return cls(
            endereco=str(raw.get("endereco", "")),
            segredo=str(raw.get("segredo", "")),
            pid=int(raw.get("pid", 0) or 0),
            diretorio_de_dados=str(raw.get("diretorio_de_dados", "")),
        )


def mint_secret() -> str:
    return secrets.token_urlsafe(32)


def publish(rendezvous: Rendezvous, path: Path | None = None) -> Path:
    """Write the rendezvous atomically, so no client reads half of it."""
    target = path or rendezvous_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(rendezvous.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def read(path: Path | None = None) -> Rendezvous | None:
    target = path or rendezvous_path()
    if not target.is_file():
        return None
    try:
        return Rendezvous.from_dict(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None


def withdraw(path: Path | None = None) -> None:
    """Remove the rendezvous. A file pointing at a dead process is worse than none."""
    target = path or rendezvous_path()
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass


def process_is_alive(pid: int) -> bool:
    """Whether the process that published a rendezvous is still there.

    A rendezvous outlives a process killed abruptly, so a client that trusted
    it blindly would wait for an answer that is never coming.
    """
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def live_rendezvous(path: Path | None = None) -> Rendezvous | None:
    """The rendezvous, but only when the process behind it still exists."""
    found = read(path)
    if found is None:
        return None
    if found.pid and not process_is_alive(found.pid):
        return None
    return found
