"""Error types shared across the core.

Every error carries a message meant to be shown to a person: it names the
field, path or identifier involved and the probable cause. Stack traces belong
in logs, never in what a surface displays.
"""

from __future__ import annotations


class VoxVaultError(Exception):
    """Base for every error the core raises deliberately."""


class ConfigError(VoxVaultError):
    """A configuration value is missing or invalid.

    Always names the field and the source it came from, so a divergence
    between processes can be diagnosed instead of guessed at.
    """

    def __init__(self, field: str, source: str, reason: str) -> None:
        self.field = field
        self.source = source
        self.reason = reason
        super().__init__(
            f"Configuracao invalida no campo '{field}' "
            f"(origem: {source}): {reason}"
        )


class StorageError(VoxVaultError):
    """The store could not be opened, migrated or written to."""


class StorageBusyError(StorageError):
    """A write could not acquire the database within the allowed wait."""


class DeletionRefused(StorageError):
    """A meeting was not deleted, and nothing of it was removed.

    ``gone`` is true when the refusal is that the meeting no longer exists,
    which is the one refusal whose files are nobody's any more.
    """

    def __init__(self, reason: str, *, gone: bool = False) -> None:
        self.reason = reason
        self.gone = gone
        super().__init__(reason)


class SchemaTooNewError(StorageError):
    """The database was written by a newer version than this build knows."""

    def __init__(self, found: int, known: int) -> None:
        self.found = found
        self.known = known
        super().__init__(
            f"O banco esta na versao de esquema {found}, mas esta versao do "
            f"VoxVault conhece ate a {known}. Atualize o VoxVault. "
            f"Nada foi escrito."
        )


class CaptureError(VoxVaultError):
    """Audio capture could not start, or failed irrecoverably."""


class DeviceLostError(CaptureError):
    """The capture device disappeared and could not be recovered in time."""


class EngineError(VoxVaultError):
    """The transcription engine failed."""


class MediaDecodeError(VoxVaultError):
    """The external media decoder is missing or could not read the input."""
