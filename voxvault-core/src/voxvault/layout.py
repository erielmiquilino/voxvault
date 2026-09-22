"""Where a meeting's files live on disk.

One module owns this because three layers need to agree on it: the session
writes the tracks, the pipeline reads them to transcribe, and the library plays
them back. A convention repeated in three places is a convention that drifts.

Layout under the data directory::

    recordings/<uid>/
        mic.wav        while recording; mic.flac once finalized
        system.wav     while recording; system.flac once finalized
        source.<ext>   for imported media, a copy of what was imported
        metadados.json
        transcricao.md
        transcricao.json
"""

from __future__ import annotations

from pathlib import Path

from .types import Track

#: Written during capture: raw PCM, cheap to append to, safe to lose the tail of.
RECORDING_SUFFIX = ".wav"
#: Written at finalization: lossless, so a better model can re-transcribe later.
FINAL_SUFFIX = ".flac"

TRACK_BASENAME: dict[str, str] = {
    Track.MIC.value: "mic",
    Track.SYSTEM.value: "system",
}

#: Track name the store uses for audio that came from an imported file.
IMPORTED_TRACK = "importada"
IMPORTED_BASENAME = "source"

METADATA_NAME = "metadados.json"


def meeting_dir(data_dir: Path, uid: str) -> Path:
    return data_dir / "recordings" / uid


def track_path(directory: Path, track: str, *, final: bool = False) -> Path:
    """The canonical path of one track, recording or finalized."""
    base = TRACK_BASENAME.get(str(track), str(track))
    return directory / f"{base}{FINAL_SUFFIX if final else RECORDING_SUFFIX}"


def existing_track_path(directory: Path, track: str) -> Path | None:
    """Find a track's audio whichever stage it is in.

    The finalized file is preferred: if both exist, the recording-time WAV is
    a leftover from a finalization that had not yet removed it, and the FLAC is
    the one that was verified.
    """
    base = TRACK_BASENAME.get(str(track), str(track))
    for suffix in (FINAL_SUFFIX, RECORDING_SUFFIX):
        candidate = directory / f"{base}{suffix}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def imported_source(directory: Path) -> Path | None:
    for candidate in sorted(directory.glob(f"{IMPORTED_BASENAME}.*")):
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def available_tracks(directory: Path) -> dict[str, Path]:
    """Every track of a meeting that has audio on disk right now.

    Returns an empty mapping when the audio was removed, which is what makes
    "cannot reprocess a meeting whose audio is gone" checkable rather than
    assumed.
    """
    found: dict[str, Path] = {}
    for track in (Track.MIC.value, Track.SYSTEM.value):
        path = existing_track_path(directory, track)
        if path is not None:
            found[track] = path
    if not found:
        source = imported_source(directory)
        if source is not None:
            found[IMPORTED_TRACK] = source
    return found
