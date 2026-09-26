"""Telling a capture refused to VoxVault apart from a broken audio system.

Found on a corporate machine: every capture -- the microphone, and the
loopback of the output -- failed ``Initialize`` with E_INVALIDARG, whatever
the format, the flags or the buffer, while a Teams call used that same
microphone and PowerShell, run as a control, opened all of it. The endpoint
security software kept capture from a program it did not know. Telling the
person to restart the Windows audio service, as the diagnosis did, sends them
the wrong way.

What tells the two apart is playback. An audio system that is broken does not
open an output either; one that refuses only this program's capture does.
Opening the output initializes it and nothing more: nothing is played.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import wasapi

#: What a refusal to open looks like: the opening itself failed, not the
#: device being missing.
_OPENING_FAILURES = ("IAudioClient::Initialize", "IMMDevice::Activate")


def playback_opens() -> bool:
    """Whether this process may open the default output for playback."""
    try:
        wasapi.co_initialize()
        enumerator = wasapi.create_enumerator()
        try:
            device = enumerator.default_endpoint(wasapi.E_RENDER, wasapi.E_COMMUNICATIONS)
            if device is None:
                return False
            try:
                client = device.activate_audio_client()
            finally:
                device.release()
        finally:
            enumerator.release()
        try:
            _raw, mix = client.mix_format()
            try:
                client.initialize(
                    share_mode=wasapi.AUDCLNT_SHAREMODE_SHARED,
                    stream_flags=0,
                    buffer_duration_hns=2_000_000,
                    periodicity_hns=0,
                    format_ptr=mix,
                )
            finally:
                wasapi._ole32.CoTaskMemFree(mix)
        finally:
            client.release()
    except Exception:
        return False
    return True


def executables_to_allow() -> list[str]:
    """The programs that open the audio, as security software names them.

    The environment's launcher starts the base interpreter, and that process
    is the one that opens the devices; both, and their windowless siblings.
    """
    found: list[str] = []
    for executable in (getattr(sys, "_base_executable", ""), sys.executable):
        if not executable:
            continue
        for name in ("python.exe", "pythonw.exe"):
            candidate = Path(executable).with_name(name)
            if candidate.is_file() and str(candidate) not in found:
                found.append(str(candidate))
    return found


def explain(error: str) -> str | None:
    """Why capture fails when playback opens; ``None`` when that is not the case."""
    if not any(failure in error for failure in _OPENING_FAILURES):
        return None
    if not playback_opens():
        return None
    allow = executables_to_allow()
    return (
        "O Windows abre a saida de audio para reproducao, mas recusa a captura "
        "ao VoxVault. Isso costuma ser um software de seguranca -- como a "
        "protecao de acesso ao microfone do Kaspersky Endpoint Security -- "
        "bloqueando um programa que ele nao conhece. Peca para liberar o "
        "acesso ao microfone"
        + (f" para: {'; '.join(allow)}." if allow else " para o Python do VoxVault.")
    )
