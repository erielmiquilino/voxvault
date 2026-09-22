"""Row-shaped value types for the store.

These describe what the database holds. The domain vocabulary that other
layers share lives in ``voxvault.types``; everything here is either a storage
concern (revision status, transcript availability) or a value the spec asks
for that ``types.py`` does not yet model.

Two of those gaps are deliberate and documented rather than papered over:

``SPEAKER_UNKNOWN``
    The spec requires segments of imported sessions to be attributed to an
    unknown speaker, but ``types.Speaker`` only declares ME and OTHERS. The
    store keeps attribution as text and uses this constant. When ``types.py``
    gains ``Speaker.UNKNOWN`` this constant should become an alias for it.

``TRACK_IMPORTED``
    An imported file has neither a microphone nor a system track. Forcing it
    into one of the two would make the speaker mapping in ``TRACK_TO_SPEAKER``
    produce a false attribution, so imported audio gets a track of its own.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from ..types import MeetingState, RevisionState, Speaker, Track

#: Attribution for segments whose audio did not come from a captured track.
SPEAKER_UNKNOWN: Final = "desconhecido"

#: Track value for audio extracted from an imported media file.
TRACK_IMPORTED: Final = "importada"

#: Every value accepted in ``segments.track``. Ordering of the timeline
#: tie-break is the lexicographic order of these strings, which is total.
VALID_TRACKS: Final = frozenset({Track.MIC.value, Track.SYSTEM.value, TRACK_IMPORTED})

#: What a piece of found text is. A transcript is a record of what was said; a
#: note is somebody's reading of it. Every search result answers with one of
#: these, because a surface that cannot tell them apart will eventually
#: present interpretation as evidence.
NATURE_TRANSCRIPT: Final = "transcricao"
NATURE_NOTE: Final = "nota"


class Origin(enum.StrEnum):
    """Where a meeting's audio came from."""

    RECORDED = "gravada"
    IMPORTED = "importada"


class RevisionStatus(enum.StrEnum):
    """Lifecycle of a revision, independent of how complete it is.

    Only ``PUBLISHED`` revisions are ever visible. ``BUILDING`` is what a
    transcription attempt writes into; ``DISCARDED`` records an attempt that
    was not allowed to replace what the meeting already had, which is the
    evidence the user is shown when a reprocess did not take effect.
    """

    BUILDING = "em_construcao"
    PUBLISHED = "publicada"
    SUPERSEDED = "substituida"
    DISCARDED = "descartada"


class TranscriptState(enum.StrEnum):
    """Availability of a transcript, which is not the state of an attempt.

    A meeting being reprocessed keeps ``COMPLETE`` availability for the whole
    attempt. Collapsing the two is what makes a surface claim a meeting has no
    transcript while it is being re-transcribed.
    """

    NONE = "nenhuma"
    PARTIAL = "parcial"
    COMPLETE = "completa"


def speaker_for(origin: Origin | str, track: Track | str) -> str:
    """Attribution is physical: it follows the track, never an inference."""
    if str(origin) == Origin.IMPORTED.value or str(track) == TRACK_IMPORTED:
        return SPEAKER_UNKNOWN
    try:
        return TRACK_TO_SPEAKER_TEXT[str(track)]
    except KeyError:
        raise ValueError(f"trilha desconhecida: {track!r}") from None


TRACK_TO_SPEAKER_TEXT: Final[dict[str, str]] = {
    Track.MIC.value: Speaker.ME.value,
    Track.SYSTEM.value: Speaker.OTHERS.value,
}


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def to_ms(moment: datetime) -> int:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


@dataclass(frozen=True, slots=True)
class Meeting:
    id: int
    uid: str
    title: str
    started_at_ms: int
    ended_at_ms: int | None
    duration_ms: int
    state: MeetingState
    attempt_state: str
    attempt_error: str
    transcript_state: TranscriptState
    origin: Origin
    source_path: str
    directory: str
    active_revision_id: int | None
    exports_revision_uid: str

    @property
    def started_at(self) -> datetime:
        return from_ms(self.started_at_ms)

    @property
    def ended_at(self) -> datetime | None:
        return None if self.ended_at_ms is None else from_ms(self.ended_at_ms)


@dataclass(frozen=True, slots=True)
class Revision:
    """One transcription run.

    The engine, the configuration fingerprint and the vocabulary are captured
    when the attempt starts and are immutable afterwards -- enforced by a
    trigger, not by convention. Without that, transcribing the second track
    after a settings change would leave the revision describing its own
    contents wrongly.
    """

    id: int
    uid: str
    meeting_id: int
    status: RevisionStatus
    state: RevisionState | None
    engine_id: str
    engine_name: str
    engine_model: str
    engine_device: str
    engine_compute: str
    engine_version: str
    config_fingerprint: str
    vocabulary: str
    language: str
    tracks_ok: tuple[str, ...]
    tracks_failed: tuple[str, ...]
    failure_reason: str
    started_at_ms: int
    completed_at_ms: int | None


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    """What happened to a revision that asked to be published.

    ``published`` false is a normal outcome, not an error: a partial result
    must not evict a better one. ``message`` is written for a person, because
    the spec requires the user to be told the reprocess did not replace what
    was there.
    """

    published: bool
    revision_uid: str
    state: RevisionState | None
    replaced_revision_uid: str | None
    kept_revision_uid: str | None
    reason: str
    message: str


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A search result that came from a transcribed segment.

    ``nature`` is what tells it apart from a note result without an
    ``isinstance``. It is a property and not a field so that the two kinds of
    result answer the same question while staying different shapes -- a note
    has no instant, no track and no speaker.
    """

    meeting_uid: str
    meeting_title: str
    meeting_started_at_ms: int
    segment_id: int
    start_ms: int
    end_ms: int
    track: str
    speaker: str
    excerpt: str
    text: str

    @property
    def meeting_started_at(self) -> datetime:
        return from_ms(self.meeting_started_at_ms)

    @property
    def nature(self) -> str:
        return NATURE_TRANSCRIPT


@dataclass(frozen=True, slots=True)
class ExportStatus:
    meeting_uid: str
    active_revision_uid: str | None
    exported_revision_uid: str
    readable_path: str
    structured_path: str
    current: bool
    reason: str
