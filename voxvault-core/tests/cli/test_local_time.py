"""``list`` and ``show`` print the time the person lived, not UTC.

Meetings are stored in UTC. Everything else the command line prints for a
person to read -- the deletion preview, the notes, the exports -- is in local
time, and a meeting started at 15:42 was listed as 18:42.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from voxvault.layout import meeting_dir
from voxvault.store import TranscriptStore
from voxvault.types import MeetingState

STARTED = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
# Nine hours east of UTC, in the POSIX form the C runtime also honours on
# Windows: far from both this machine's zone and a runner's UTC.
ZONE = "VVT-9"
LOCAL = "02/03/2026 23:00"


def run(data_dir: Path, *args: str) -> str:
    root = Path(__file__).resolve().parents[2] / "src"
    env = {
        **os.environ,
        "PYTHONPATH": str(root),
        "PYTHONIOENCODING": "utf-8",
        "VOXVAULT_DATA_DIR": str(data_dir),
        "TZ": ZONE,
    }
    result = subprocess.run(
        [sys.executable, "-m", "voxvault.cli", "--data-dir", str(data_dir), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_list_and_show_print_the_local_time(tmp_path: Path) -> None:
    directory = meeting_dir(tmp_path, "abc123")
    directory.mkdir(parents=True)
    with TranscriptStore(tmp_path / "voxvault.db") as store:
        store.create_meeting(
            uid="abc123",
            title="Planejamento",
            started_at=STARTED,
            directory=directory,
        )
        store.finish_meeting(
            "abc123",
            ended_at=STARTED,
            duration_ms=90_000,
            state=MeetingState.RECORDED,
        )

    assert LOCAL in run(tmp_path, "list")
    assert LOCAL in run(tmp_path, "show", "abc123")
