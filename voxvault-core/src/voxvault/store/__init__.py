"""Local storage: meetings, revisions, timeline, search and export.

Reading order for anyone new to this package:

``schema.py``
    The tables and the migrations that build them.
``connection.py``
    Opening the database: write-ahead logging, cross-process migration
    serialisation, and the short write transaction with its contention budget.
``store.py``
    The queries and the revision rules.
``writer.py``
    Why the audio capture path never waits on the database.
``exporting.py``
    Derived files and how they are kept honest about which revision made them.

Imports here stay cheap on purpose: the command line and the MCP server open
this package on every invocation, and startup time is a product requirement.
Nothing in this package imports numpy, an inference runtime or any audio
library.
"""

from __future__ import annotations

from ..errors import SchemaTooNewError, StorageBusyError, StorageError
from .connection import (
    DEFAULT_BUSY_TIMEOUT_S,
    DEFAULT_MIGRATION_TIMEOUT_S,
    capture_execution_path,
    in_capture_path,
)
from .exporting import (
    READABLE_NAME,
    STRUCTURED_NAME,
    current_export_paths,
    export_paths,
    export_status,
    load_structured,
    recorded_revision_uid,
    reconcile_exports,
    regenerate_exports,
    render_readable,
    render_structured,
    timeline_from_structured,
)
from .models import (
    SPEAKER_UNKNOWN,
    TRACK_IMPORTED,
    ExportStatus,
    Meeting,
    Origin,
    PublishOutcome,
    Revision,
    RevisionStatus,
    SearchHit,
    TranscriptState,
    from_ms,
    now_ms,
    speaker_for,
    to_ms,
)
from .schema import SCHEMA_VERSION
from .store import TranscriptStore
from .timeline import (
    find_overlaps,
    format_duration,
    format_offset,
    merge,
    order_key,
    overlapping_ids,
)
from .writer import AsyncWriter, WriteFailure

__all__ = [
    "AsyncWriter",
    "DEFAULT_BUSY_TIMEOUT_S",
    "DEFAULT_MIGRATION_TIMEOUT_S",
    "ExportStatus",
    "Meeting",
    "Origin",
    "PublishOutcome",
    "READABLE_NAME",
    "Revision",
    "RevisionStatus",
    "SCHEMA_VERSION",
    "SPEAKER_UNKNOWN",
    "STRUCTURED_NAME",
    "SchemaTooNewError",
    "SearchHit",
    "StorageBusyError",
    "StorageError",
    "TRACK_IMPORTED",
    "TranscriptState",
    "TranscriptStore",
    "WriteFailure",
    "capture_execution_path",
    "current_export_paths",
    "export_paths",
    "export_status",
    "find_overlaps",
    "format_duration",
    "format_offset",
    "from_ms",
    "in_capture_path",
    "load_structured",
    "merge",
    "now_ms",
    "order_key",
    "overlapping_ids",
    "reconcile_exports",
    "recorded_revision_uid",
    "regenerate_exports",
    "render_readable",
    "render_structured",
    "speaker_for",
    "timeline_from_structured",
    "to_ms",
]
