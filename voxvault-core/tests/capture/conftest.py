"""Helpers for the capture tests.

The point of these fixtures is that the alignment logic can be exercised with
packets we build ourselves, on any machine. A test that needs a real endpoint
carries ``@pytest.mark.hardware`` and is skipped when none is present, so the
suite still says something useful on a machine with no audio devices -- or, as
on a Remote Desktop session, with no capture endpoint at all.
"""

from __future__ import annotations

import pytest

from voxvault.types import CapturePacket

NS = 1_000_000_000
RATE = 48_000


def packet(
    *,
    device_position: int,
    frames: int,
    qpc_ns: int,
    discontinuity: bool = False,
    silent: bool = False,
    timestamp_valid: bool = True,
    fill: int = 0x01,
) -> CapturePacket:
    """A packet shaped exactly like one the reader loop produces."""
    return CapturePacket(
        data=b"" if silent else bytes([fill]) * (frames * 4),
        frames=frames,
        device_position=device_position,
        qpc_ns=qpc_ns,
        discontinuity=discontinuity,
        silent=silent,
        timestamp_valid=timestamp_valid,
    )


def coherent_stream(
    *,
    count: int,
    frames: int = 480,
    rate: int = RATE,
    start_position: int = 0,
    start_qpc_ns: int = 10 * NS,
):
    """A stream where the timestamp is a strict function of the position.

    This is the shape a correct backend produces: it is what the measured
    WASAPI stream does, to within about 20 microseconds.
    """
    for index in range(count):
        position = start_position + index * frames
        yield packet(
            device_position=position,
            frames=frames,
            qpc_ns=start_qpc_ns + (position - start_position) * NS // rate,
        )


def _has_endpoint(flow: str) -> bool:
    try:
        from voxvault.capture import devices
    except Exception:  # pragma: no cover - non-Windows
        return False
    try:
        return bool(devices.list_endpoints(flow))
    except Exception:
        return False


def _usable(endpoint, *, loopback: bool) -> str:
    """Open and close the endpoint once, returning '' or the reason it failed.

    Enumerable is not the same as usable. A Remote Desktop endpoint whose
    audio channel has dropped still appears in the list but answers
    ``GetMixFormat`` with ``REGDB_E_CLASSNOTREG``. That is an environment
    fault, so the hardware tests must skip on it rather than report a failure
    of code that was fine an hour earlier.
    """
    from voxvault.capture.stream import CaptureStream

    stream = CaptureStream(
        endpoint.id, loopback=loopback, name="probe", pro_audio_priority=False
    )
    try:
        stream.start(timeout_s=20.0)
    except Exception as exc:
        return str(exc)
    finally:
        stream.stop()
    return ""


@pytest.fixture(scope="session")
def render_endpoint():
    from voxvault.capture import devices

    if not _has_endpoint(devices.FLOW_RENDER):
        pytest.skip("nenhum dispositivo de saida nesta sessao")
    endpoint = devices.default_endpoint(
        devices.FLOW_RENDER, devices.ROLE_COMMUNICATIONS
    )
    reason = _usable(endpoint, loopback=True)
    if reason:
        pytest.skip(f"dispositivo de saida presente mas inutilizavel: {reason}")
    return endpoint


@pytest.fixture(scope="session")
def capture_endpoint():
    from voxvault.capture import devices

    if not _has_endpoint(devices.FLOW_CAPTURE):
        pytest.skip(
            "nenhum dispositivo de entrada nesta sessao "
            "(sessao de Area de Trabalho Remota nao expoe microfone)"
        )
    endpoint = devices.default_endpoint(
        devices.FLOW_CAPTURE, devices.ROLE_COMMUNICATIONS
    )
    reason = _usable(endpoint, loopback=False)
    if reason:
        pytest.skip(f"microfone presente mas inutilizavel: {reason}")
    return endpoint
