"""Starting the resident service from a surface that needs it.

Exists because of a real recording that lost the second half of a meeting.
The command line used to fall back to capturing on its own when no service was
running -- a second, lesser implementation of recording, older than the
service, with no device supervisor, no suspend handling and no level meters.
When a Bluetooth headset's battery died mid-meeting, nothing noticed, nothing
moved to the next device, and the counter sat frozen for three and a half hours
while the meeting carried on unrecorded.

So there is one implementation of recording now, and it lives in the service.
A surface that finds no service running starts one and becomes its client.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .rendezvous import Rendezvous, live_rendezvous, rendezvous_path

#: How long to wait for a freshly started service to publish its address.
#: Publishing happens before recovery and device warm-up, so this is short.
STARTUP_TIMEOUT_S = 30.0


def log_path() -> Path:
    """Where the service writes, beside the rendezvous -- the same place the
    desktop app looks, so either surface can show the other's failures."""
    return rendezvous_path().parent / "servico.log"


def ensure_running(*, timeout_s: float = STARTUP_TIMEOUT_S) -> Rendezvous:
    """Return the running service, starting one if there is none.

    Raises ``RuntimeError`` naming the log when the service does not come up,
    because "it did not start" is useless without "and here is why".
    """
    found = live_rendezvous()
    if found is not None:
        return found

    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log, "a", encoding="utf-8")  # noqa: SIM115 -- handed to the child
    handle.write(f"\n--- iniciado pela linha de comando em {time.ctime()} ---\n")
    handle.flush()

    flags = 0
    if os.name == "nt":
        # Detached so the service outlives the terminal that started it: a
        # meeting recorded from a console that is later closed must still be
        # finalized and transcribed.
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    subprocess.Popen(
        [sys.executable, "-m", "voxvault.cli", "serve"],
        stdout=handle, stderr=handle, stdin=subprocess.DEVNULL,
        creationflags=flags, close_fds=True,
    )
    handle.close()

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        found = live_rendezvous()
        if found is not None:
            return found
        time.sleep(0.25)

    raise RuntimeError(
        f"O servico residente nao respondeu em {timeout_s:.0f}s. "
        f"O registro dele esta em {log}."
    )
