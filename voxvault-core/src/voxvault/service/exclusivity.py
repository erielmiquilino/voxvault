"""One service per machine, for capture and for transcription.

Scoped to the machine and not to the data directory, which was a real mistake
worth naming: two services pointed at different data directories are still two
processes trying to hold the same microphone and the same GPU. "One per data
directory" would have let exactly that happen while looking careful.

Implemented with a named kernel object rather than a lock file, because a lock
file survives a process that was killed and leaves the next start convinced
somebody else is recording.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: Global namespace so the name is shared across sessions of the same machine,
#: not just across processes of one login.
CAPTURE_MUTEX = "Global\\VoxVault.Captura"

ERROR_ALREADY_EXISTS = 183


@dataclass(slots=True)
class Claim:
    """A held exclusivity claim. Released when the process ends, whatever the cause."""

    name: str
    handle: int = 0
    held: bool = False

    def release(self) -> None:
        if not self.held or not self.handle:
            return
        if os.name == "nt":
            import ctypes

            ctypes.windll.kernel32.ReleaseMutex(self.handle)
            ctypes.windll.kernel32.CloseHandle(self.handle)
        self.handle = 0
        self.held = False


def acquire(name: str = CAPTURE_MUTEX) -> Claim:
    """Take the claim, or come back with ``held`` false.

    Never blocks: a second service must fail fast and say so, not wait behind
    the first one and start recording the moment it happens to exit.
    """
    if os.name != "nt":
        # Nothing to contend for off Windows, where capture does not run
        # anyway. Reported as held so the rest of the service is testable.
        return Claim(name=name, handle=0, held=True)

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]

    handle = kernel32.CreateMutexW(None, True, name)
    if not handle:
        return Claim(name=name, handle=0, held=False)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return Claim(name=name, handle=0, held=False)
    return Claim(name=name, handle=handle, held=True)


def describe_conflict() -> str:
    return (
        "Ja existe um servico do VoxVault em execucao nesta maquina. Captura e "
        "transcricao sao exclusivas por maquina, nao por diretorio de dados: "
        "dois servicos apontando para diretorios diferentes continuariam "
        "disputando o mesmo microfone e a mesma GPU."
    )
