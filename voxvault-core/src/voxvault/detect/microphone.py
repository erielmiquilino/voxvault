"""Who is holding the microphone right now.

Read from the same place Windows itself reads to show the "an app is using
your microphone" indicator: the capability access store. It records, per
application, when microphone use started and when it stopped; an entry whose
stop instant is zero is one that has not stopped.

What this gives is an executable path and two timestamps. It is worth being
precise about what that means, because the detector's whole licence to exist
depends on it: **no audio is read, no window title, no address, no content of
any kind.** The question asked is "is some process capturing", never "what is
being said or shown".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Where Windows records microphone use per application.
CONSENT_STORE = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone"
)
#: Desktop applications live under this subkey, with their path encoded.
NON_PACKAGED = "NonPackaged"


@dataclass(frozen=True, slots=True)
class MicrophoneUser:
    """One application the system says is using the microphone."""

    #: Executable path for a desktop application, or the family name of a
    #: packaged one.
    identity: str
    #: Lowercased executable file name, which is what recognition matches on.
    executable: str
    packaged: bool
    started_at: int = 0

    @property
    def describe(self) -> str:
        return self.executable or self.identity


def _decode_identity(key_name: str, packaged: bool) -> tuple[str, str]:
    if packaged:
        return key_name, ""
    # The store escapes a path's separators; nothing else is transformed.
    path = key_name.replace("#", "\\")
    return path, Path(path).name.lower()


def active_users() -> list[MicrophoneUser]:
    """Every application currently holding the microphone.

    An empty list on a system without the store, or without permission to read
    it, rather than an error: detection is a convenience, and a machine that
    cannot answer the question should simply never detect anything.
    """
    if os.name != "nt":
        return []

    import winreg

    found: list[MicrophoneUser] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONSENT_STORE)
    except OSError:
        return []

    try:
        for packaged_scope in (True, False):
            if packaged_scope:
                scopes = [(root, True)]
            else:
                try:
                    scopes = [
                        (winreg.OpenKey(root, NON_PACKAGED), False)
                    ]
                except OSError:
                    continue
            for handle, is_packaged in scopes:
                found.extend(_scan(handle, is_packaged, winreg))
                if not is_packaged:
                    handle.Close()
    finally:
        root.Close()
    return found


def _scan(handle, packaged: bool, winreg) -> list[MicrophoneUser]:
    out: list[MicrophoneUser] = []
    index = 0
    while True:
        try:
            name = winreg.EnumKey(handle, index)
        except OSError:
            break
        index += 1
        if packaged and name == NON_PACKAGED:
            continue
        try:
            entry = winreg.OpenKey(handle, name)
        except OSError:
            continue
        try:
            stop = _value(entry, "LastUsedTimeStop", winreg)
            start = _value(entry, "LastUsedTimeStart", winreg)
            # Zero means "has not stopped": the application still holds it.
            if stop == 0 and start is not None:
                identity, executable = _decode_identity(name, packaged)
                out.append(MicrophoneUser(
                    identity=identity,
                    executable=executable or identity.lower(),
                    packaged=packaged,
                    started_at=start or 0,
                ))
        finally:
            entry.Close()
    return out


def _value(key, name: str, winreg) -> int | None:
    try:
        raw, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def store_is_readable() -> bool:
    """Whether this machine can answer the question at all."""
    if os.name != "nt":
        return False
    import winreg

    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONSENT_STORE).Close()
    except OSError:
        return False
    return True
