"""Fixtures for the transcription queue tests.

The store is real -- the queue's correctness is mostly about what it writes and
when, and a fake store would only prove the fake behaves as written. The
*engine* is faked, because loading a multi-gigabyte model to prove that a
failing track does not stop the other one would test the wrong thing.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from voxvault.config import Config
from voxvault.layout import meeting_dir
from voxvault.pipeline import TrackOutcome
from voxvault.store import Origin, TranscriptStore
from voxvault.types import EngineInfo, MeetingState, Segment

BASE_TIME = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)

ENGINE = EngineInfo(
    name="falso", model="modelo-de-teste", compute_type="int8",
    device="cpu", version="1.0",
)


class FakeWorker:
    """Stands in for the inference subprocess.

    Records every job so tests can assert what was asked of the engine, and can
    be told to fail a specific track or to die mid-job the way a killed process
    does.
    """

    def __init__(self, config: Config, model: str | None = None) -> None:
        self.config = config
        self.engine = ENGINE
        self.alive = True
        self.jobs: list[tuple[str, str, str]] = []
        #: every audio file the engine was asked to read, in order
        self.paths: list[Path] = []
        self.killed = 0
        #: track name -> error message, for tracks that should fail
        self.fail_tracks: dict[str, str] = {}
        #: raise the interrupted signal on the job with this index
        self.interrupt_at: int | None = None
        self.text_by_track: dict[str, str] = {}

    def transcribe(
        self, audio_path: Path, track: str, language: str, vocabulary: str
    ) -> TrackOutcome:
        from voxvault.pipeline import _Interrupted

        index = len(self.jobs)
        self.jobs.append((track, language, vocabulary))
        self.paths.append(audio_path)
        if self.interrupt_at is not None and index >= self.interrupt_at:
            raise _Interrupted()
        if track in self.fail_tracks:
            return TrackOutcome(track, False, [], self.fail_tracks[track])
        text = self.text_by_track.get(track, f"fala da trilha {track}")
        return TrackOutcome(track, True, [Segment(0, 2000, text)])

    def kill(self) -> float:
        self.killed += 1
        self.alive = False
        return 0.0


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path,
        model="modelo-de-teste",
        language="pt",
        vocabulary="Kubernetes",
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[TranscriptStore]:
    handle = TranscriptStore(tmp_path / "voxvault.db")
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture
def make_meeting(config: Config, store: TranscriptStore):
    """Create a meeting with real audio files on disk.

    Real files because the queue decides what to transcribe by looking at what
    exists -- "no audio means no reprocessing" is only checkable against a real
    directory.
    """
    def _make(
        uid: str = "reuniao-1",
        *,
        tracks: tuple[str, ...] = ("mic", "system"),
        title: str = "Reuniao de teste",
        origin: Origin = Origin.RECORDED,
    ):
        directory = meeting_dir(config.data_dir, uid)
        directory.mkdir(parents=True, exist_ok=True)
        for track in tracks:
            # Content is irrelevant: the fake engine never reads it, and the
            # queue only checks that the file exists and is not empty.
            (directory / f"{track}.wav").write_bytes(b"RIFF....WAVEfmt ")
        meeting = store.create_meeting(
            uid=uid, title=title, started_at=BASE_TIME,
            directory=directory, origin=origin, state=MeetingState.RECORDED,
        )
        return meeting
    return _make
