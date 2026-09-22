"""Fixtures for the storage tests.

Every test gets a real database on disk under ``tmp_path``. Nothing here mocks
SQLite: the properties under test -- write-ahead logging, contention, migration
serialisation -- are properties of the real engine and of real processes, and a
double would only prove that the double behaves as written.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from voxvault.store import Origin, TranscriptStore
from voxvault.types import EngineInfo, MeetingState, Segment, Track

CHILD = Path(__file__).with_name("_child.py")

BASE_TIME = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "voxvault.db"


@pytest.fixture
def store(db_path: Path) -> Iterator[TranscriptStore]:
    handle = TranscriptStore(db_path)
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture
def engine() -> EngineInfo:
    return EngineInfo(
        name="faster-whisper",
        model="large-v3",
        compute_type="float16",
        device="cuda",
        version="1.1",
    )


@pytest.fixture
def other_engine() -> EngineInfo:
    return EngineInfo(
        name="whisper.cpp",
        model="medium",
        compute_type="int8",
        device="cpu",
        version="0.9",
    )


def make_meeting(
    store: TranscriptStore,
    tmp_path: Path,
    uid: str = "reuniao-1",
    *,
    title: str = "Reuniao de planejamento",
    started_at: datetime | None = None,
    origin: Origin = Origin.RECORDED,
    duration_ms: int = 3_600_000,
):
    directory = tmp_path / uid
    directory.mkdir(parents=True, exist_ok=True)
    meeting = store.create_meeting(
        uid=uid,
        title=title,
        started_at=started_at or BASE_TIME,
        directory=directory,
        origin=origin,
    )
    store.finish_meeting(
        uid,
        ended_at=(started_at or BASE_TIME) + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        state=MeetingState.RECORDED,
    )
    return store.get_meeting(meeting.uid)


def transcribe(
    store: TranscriptStore,
    uid: str,
    engine: EngineInfo,
    *,
    mic: list[Segment] | None = None,
    system: list[Segment] | None = None,
    tracks_failed: list[Track] | None = None,
    failure_reason: str = "",
    vocabulary: str = "",
    language: str = "pt",
):
    """One complete transcription attempt, start to publication."""
    if tracks_failed is None:
        tracks_failed = []
    revision = store.begin_revision(
        uid, engine=engine, vocabulary=vocabulary, language=language
    )
    ok: list[Track] = []
    if mic is not None:
        store.add_segments(revision.id, Track.MIC, mic)
        ok.append(Track.MIC)
    if system is not None:
        store.add_segments(revision.id, Track.SYSTEM, system)
        ok.append(Track.SYSTEM)
    outcome = store.publish_revision(
        revision.id,
        tracks_ok=ok,
        tracks_failed=tracks_failed,
        failure_reason=failure_reason,
    )
    return revision, outcome


def spawn(*args: str) -> subprocess.Popen:
    """Launch the child helper as a genuinely separate process."""
    return subprocess.Popen(
        [sys.executable, str(CHILD), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )


def wait_ready(process: subprocess.Popen, timeout_s: float = 30.0) -> None:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        line = process.stdout.readline()
        if line == "":
            raise AssertionError(
                f"processo filho terminou antes de ficar pronto: "
                f"{process.stderr.read()}"
            )
        if line.strip() == "READY":
            return
    raise AssertionError("processo filho nao ficou pronto a tempo")


def read_json_line(process: subprocess.Popen, timeout_s: float = 30.0) -> dict:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        line = process.stdout.readline()
        if line == "":
            raise AssertionError(
                f"processo filho terminou sem emitir JSON: {process.stderr.read()}"
            )
        stripped = line.strip()
        if stripped and stripped != "READY":
            return json.loads(stripped)
    raise AssertionError("processo filho nao emitiu JSON a tempo")


def result_of(process: subprocess.Popen, timeout_s: float = 60.0) -> dict:
    stdout, stderr = process.communicate(timeout=timeout_s)
    lines = [ln for ln in stdout.splitlines() if ln.strip() and ln.strip() != "READY"]
    if not lines:
        raise AssertionError(f"processo filho nao devolveu resultado: {stderr}")
    return json.loads(lines[-1])
