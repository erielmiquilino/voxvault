"""Fixtures for the MCP surface.

The store is real and the tools are called through the server, not around it:
what is being tested is what an agent would actually receive, and a test that
calls the inner function directly would miss everything the protocol layer
does to the payload on the way out.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from voxvault.config import Config
from voxvault.store import Origin, TranscriptStore
from voxvault.types import EngineInfo, MeetingState, Segment, Track

BASE_TIME = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)

ENGINE = EngineInfo(
    name="falso", model="modelo-de-teste", compute_type="int8",
    device="cpu", version="1.0",
)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path)


@pytest.fixture
def store(config: Config) -> Iterator[TranscriptStore]:
    handle = TranscriptStore(config.db_path)
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture
def server(config: Config, store: TranscriptStore):
    """A server wired to this test's store, not to the user's real one."""
    from voxvault.mcp import server as module

    # Only the configuration is injected. The session opens its own connection
    # per thread, exactly as it does in production -- injecting one here would
    # test a setup that never happens.
    module.SESSION.config = config
    module.SESSION._local = threading.local()
    module.SESSION.client = "cliente-de-teste"
    yield module.build_server(config)
    module.SESSION.close()


def call(server, name: str, **arguments) -> Any:
    """Invoke a tool the way a client would, and hand back its payload."""
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple):
        return result[1]
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    content = getattr(result, "content", None)
    if content:
        import json

        return json.loads(content[0].text)
    return result


@pytest.fixture
def make_meeting(store: TranscriptStore, config: Config):
    """Create a meeting, optionally with a published transcription."""
    def _make(
        uid: str = "reuniao-1",
        *,
        title: str = "Alinhamento da sprint",
        minutes_ago: int = 0,
        segments: list[tuple[str, int, int, str]] | None = None,
        publish: bool = True,
        origin: Origin = Origin.RECORDED,
    ):
        directory = config.data_dir / "recordings" / uid
        directory.mkdir(parents=True, exist_ok=True)
        started = BASE_TIME - timedelta(minutes=minutes_ago)
        meeting = store.create_meeting(
            uid=uid, title=title, started_at=started, directory=directory,
            origin=origin, state=MeetingState.RECORDED, duration_ms=600_000,
        )
        if segments is None:
            segments = [
                (Track.MIC.value, 0, 2000, "Bom dia, vamos comecar."),
                (Track.SYSTEM.value, 2500, 5000, "Bom dia a todos."),
                (Track.MIC.value, 6000, 9000, "O primeiro ponto e a migracao."),
            ]
        if segments:
            revision = store.begin_revision(uid, engine=ENGINE, language="pt")
            by_track: dict[str, list[Segment]] = {}
            for track, start, end, text in segments:
                by_track.setdefault(track, []).append(Segment(start, end, text))
            for track, items in by_track.items():
                store.add_segments(revision.id, track, items)
            if publish:
                store.publish_revision(revision.id, tracks_ok=list(by_track))
        return meeting
    return _make
