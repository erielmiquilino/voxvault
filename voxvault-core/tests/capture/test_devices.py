"""Endpoint enumeration and the two device policies."""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="WASAPI so existe no Windows"
)

from voxvault.capture import devices  # noqa: E402
from voxvault.config import (  # noqa: E402
    DEVICE_ROLE_COMMUNICATIONS,
    DEVICE_ROLE_MULTIMEDIA,
)
from voxvault.errors import CaptureError  # noqa: E402


def test_the_configured_role_names_map_onto_windows_roles():
    assert devices.role_from_config(DEVICE_ROLE_COMMUNICATIONS) == (
        devices.ROLE_COMMUNICATIONS
    )
    assert devices.role_from_config(DEVICE_ROLE_MULTIMEDIA) == devices.ROLE_MULTIMEDIA


def test_an_unknown_role_is_refused_by_name():
    with pytest.raises(CaptureError, match="papel de dispositivo desconhecido"):
        devices.role_from_config("consola")


def test_the_three_windows_roles_are_kept_separate():
    """Collapsing them is how a meeting ends up recorded from the wrong endpoint."""
    assert set(devices.ALL_ROLES) == {
        devices.ROLE_CONSOLE,
        devices.ROLE_MULTIMEDIA,
        devices.ROLE_COMMUNICATIONS,
    }
    assert len(devices._ROLE_TO_WASAPI) == 3
    assert len(set(devices._ROLE_TO_WASAPI.values())) == 3


def test_headphone_detection_covers_the_worn_form_factors():
    def endpoint(form_factor: int) -> devices.AudioEndpoint:
        return devices.AudioEndpoint(
            id="x", name="x", flow=devices.FLOW_RENDER, state=1, form_factor=form_factor
        )

    assert endpoint(3).looks_like_headphones  # Headphones
    assert endpoint(5).looks_like_headphones  # Headset
    assert not endpoint(1).looks_like_headphones  # Speakers
    assert endpoint(1).form_factor_name == "Speakers"


def test_a_missing_pinned_device_fails_by_name_and_never_falls_back():
    """Falling back to the default would record the wrong endpoint silently."""
    with pytest.raises(CaptureError) as info:
        devices.resolve_endpoint(
            flow=devices.FLOW_RENDER,
            policy_pinned_id="{0.0.0.00000000}.{nao-existe}",
        )
    message = str(info.value)
    assert "nao-existe" in message
    assert "disponiveis" in message


def test_looking_up_a_nonexistent_identifier_returns_nothing():
    assert devices.endpoint_by_id("{0.0.0.00000000}.{nada}") is None


@pytest.mark.hardware
def test_endpoints_carry_a_persistent_identifier_and_a_name():
    endpoints = devices.list_endpoints()
    if not endpoints:
        pytest.skip("nenhum dispositivo de audio nesta sessao")
    for endpoint in endpoints:
        assert endpoint.id.startswith("{")
        assert endpoint.name
        assert endpoint.flow in (devices.FLOW_CAPTURE, devices.FLOW_RENDER)
    assert len({e.id for e in endpoints}) == len(endpoints)


@pytest.mark.hardware
def test_each_role_default_is_one_of_the_enumerated_endpoints():
    for flow in (devices.FLOW_CAPTURE, devices.FLOW_RENDER):
        endpoints = devices.list_endpoints(flow)
        if not endpoints:
            continue
        ids = {e.id for e in endpoints}
        for role in devices.ALL_ROLES:
            default = devices.default_endpoint(flow, role)
            if default is not None:
                assert default.id in ids
                assert role in default.default_for


@pytest.mark.hardware
def test_an_endpoint_can_be_found_again_by_its_identifier():
    endpoints = devices.list_endpoints()
    if not endpoints:
        pytest.skip("nenhum dispositivo de audio nesta sessao")
    first = endpoints[0]
    again = devices.endpoint_by_id(first.id)
    assert again is not None
    assert again.name == first.name
