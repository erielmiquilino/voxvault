"""Endpoint enumeration and the two device policies."""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="WASAPI so existe no Windows"
)

from voxvault.capture import devices
from voxvault.config import (
    DEVICE_ROLE_COMMUNICATIONS,
    DEVICE_ROLE_MULTIMEDIA,
)
from voxvault.errors import CaptureError


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


JBL = "{B671E6E1-1B66-5FA4-8792-EAB1C6B58E79}"


def _endpoint(name: str, flow: str, form_factor: int, *, container: str = JBL,
              state: int = 1) -> devices.AudioEndpoint:
    return devices.AudioEndpoint(
        id=f"{{{name}}}", name=name, flow=flow, state=state,
        form_factor=form_factor, container_id=container,
    )


# A Bluetooth headset as Windows showed it on 25/09: a stereo output that was
# the communications default, and the hands-free pair a Teams call used.
HANDS_FREE_MIC = _endpoint("Microfone (JBL Tune Flex 2 Hands-Free)", devices.FLOW_CAPTURE, 5)
STEREO = _endpoint("Fones de ouvido (JBL Tune Flex 2)", devices.FLOW_RENDER, 3)
HANDS_FREE_OUT = _endpoint("Alto-falantes (JBL Tune Flex 2 Hands-Free)", devices.FLOW_RENDER, 5)
MONITOR = _endpoint("SyncMaster", devices.FLOW_RENDER, 1, container="{MONITOR}")


def test_a_headset_microphone_leads_to_the_hands_free_output_of_the_same_device():
    outputs = [MONITOR, STEREO, HANDS_FREE_OUT]
    assert devices.pick_call_output(HANDS_FREE_MIC, outputs) == HANDS_FREE_OUT


def test_a_microphone_that_is_not_a_headsets_leads_nowhere():
    webcam = _endpoint("Microfone (C920)", devices.FLOW_CAPTURE, 4, container="{C920}")
    assert devices.pick_call_output(webcam, [MONITOR, STEREO, HANDS_FREE_OUT]) is None


def test_a_headset_without_an_active_call_output_leads_nowhere():
    """Before the call, or after it: the stereo output alone is no call."""
    unplugged = _endpoint("Alto-falantes (JBL Tune Flex 2 Hands-Free)",
                          devices.FLOW_RENDER, 5, state=8)
    assert devices.pick_call_output(HANDS_FREE_MIC, [STEREO, unplugged]) is None


def test_another_headsets_call_output_is_never_taken():
    other = _endpoint("Alto-falantes (Outro Hands-Free)", devices.FLOW_RENDER, 5,
                      container="{OUTRO}")
    assert devices.pick_call_output(HANDS_FREE_MIC, [STEREO, other]) is None


def test_an_unknown_device_matches_nothing():
    """No container means no evidence two endpoints are one headset."""
    anonymous = _endpoint("Microfone (sem aparelho)", devices.FLOW_CAPTURE, 5, container="")
    assert devices.pick_call_output(anonymous, [HANDS_FREE_OUT]) is None


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
