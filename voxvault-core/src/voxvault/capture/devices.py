"""Endpoint enumeration with persistent identifiers and per-role defaults.

Two things here are load-bearing.

**The identifier.** ``IMMDevice::GetId`` returns the endpoint ID string, which
survives reboots and reconnections of the same device. The friendly name does
not identify anything: two identical headsets share it, and renaming a device
in the Sound panel changes it. A pinned-device policy that stored the name
would silently follow the wrong device.

**The role.** Windows keeps a *separate* default endpoint for eConsole,
eMultimedia and eCommunications, and conferencing clients follow the
communications role. Reporting only "the default" would leave the choice to
whoever reads the list, with a good chance of picking the endpoint the meeting
is not using.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Iterable

from ..config import DEVICE_ROLE_COMMUNICATIONS, DEVICE_ROLE_MULTIMEDIA
from ..errors import CaptureError
from . import wasapi

FLOW_CAPTURE: Final = "entrada"
FLOW_RENDER: Final = "saida"

ROLE_CONSOLE: Final = "console"
ROLE_MULTIMEDIA: Final = "multimidia"
ROLE_COMMUNICATIONS: Final = "comunicacoes"

_FLOW_TO_WASAPI: Final[dict[str, int]] = {
    FLOW_CAPTURE: wasapi.E_CAPTURE,
    FLOW_RENDER: wasapi.E_RENDER,
}

_ROLE_TO_WASAPI: Final[dict[str, int]] = {
    ROLE_CONSOLE: wasapi.E_CONSOLE,
    ROLE_MULTIMEDIA: wasapi.E_MULTIMEDIA,
    ROLE_COMMUNICATIONS: wasapi.E_COMMUNICATIONS,
}

ALL_ROLES: Final = (ROLE_CONSOLE, ROLE_MULTIMEDIA, ROLE_COMMUNICATIONS)

#: ``config`` names the two roles the product exposes; both map straight onto
#: a Windows role. eConsole is enumerated too, because it is what the legacy
#: audio APIs follow and its divergence is worth reporting.
_CONFIG_ROLE_TO_ROLE: Final[dict[str, str]] = {
    DEVICE_ROLE_COMMUNICATIONS: ROLE_COMMUNICATIONS,
    DEVICE_ROLE_MULTIMEDIA: ROLE_MULTIMEDIA,
}

_STATE_NAMES: Final[dict[int, str]] = {
    wasapi.DEVICE_STATE_ACTIVE: "ativo",
    wasapi.DEVICE_STATE_DISABLED: "desabilitado",
    wasapi.DEVICE_STATE_NOTPRESENT: "ausente",
    wasapi.DEVICE_STATE_UNPLUGGED: "desconectado",
}

#: Form factors that look like something worn on the head. Used by the
#: echo-risk warning: with speakers, the microphone re-captures the other
#: participants and the same speech appears on both tracks.
_HEADPHONE_FORM_FACTORS: Final = frozenset({3, 5, 6})  # Headphones, Headset, Handset


def role_from_config(value: str) -> str:
    """Map a ``config.device_role`` value onto a Windows role."""
    try:
        return _CONFIG_ROLE_TO_ROLE[value]
    except KeyError:
        raise CaptureError(
            f"papel de dispositivo desconhecido: {value!r}; "
            f"esperado um de {sorted(_CONFIG_ROLE_TO_ROLE)}"
        ) from None


@dataclass(frozen=True, slots=True)
class AudioEndpoint:
    """One endpoint as the Sound panel would show it, plus what code needs."""

    id: str
    name: str
    flow: str
    state: int
    form_factor: int
    default_for: frozenset[str] = field(default_factory=frozenset)

    @property
    def state_name(self) -> str:
        return _STATE_NAMES.get(self.state, f"0x{self.state:X}")

    @property
    def form_factor_name(self) -> str:
        return wasapi.FORM_FACTOR_NAMES.get(self.form_factor, "desconhecido")

    @property
    def looks_like_headphones(self) -> bool:
        return self.form_factor in _HEADPHONE_FORM_FACTORS

    @property
    def is_active(self) -> bool:
        return self.state == wasapi.DEVICE_STATE_ACTIVE

    def describe(self) -> str:
        roles = ", ".join(sorted(self.default_for)) if self.default_for else "-"
        return (
            f"{self.name} [{self.flow}] padrao de: {roles} | "
            f"{self.form_factor_name} | {self.state_name}\n    {self.id}"
        )


def _read_endpoint(device: wasapi.IMMDevice, flow: str) -> AudioEndpoint:
    endpoint_id = device.id()
    state = device.state()
    store = device.property_store()
    try:
        name = store.get_string(wasapi.PKEY_Device_FriendlyName)
        if not name:
            name = store.get_string(
                wasapi.PKEY_DeviceInterface_FriendlyName, "(sem nome)"
            )
        form_factor = store.get_uint(wasapi.PKEY_AudioEndpoint_FormFactor, 10)
    finally:
        store.release()
    return AudioEndpoint(
        id=endpoint_id,
        name=name,
        flow=flow,
        state=state,
        form_factor=form_factor,
    )


def _defaults_by_role(
    enumerator: wasapi.IMMDeviceEnumerator, flow_value: int
) -> dict[str, str]:
    """Endpoint ID of the default device for each role, for one direction."""
    out: dict[str, str] = {}
    for role, role_value in _ROLE_TO_WASAPI.items():
        device = enumerator.default_endpoint(flow_value, role_value)
        if device is None:
            continue
        try:
            out[role] = device.id()
        finally:
            device.release()
    return out


def list_endpoints(
    flow: str | None = None,
    *,
    state_mask: int = wasapi.DEVICE_STATE_ACTIVE,
) -> list[AudioEndpoint]:
    """Every endpoint, tagged with the roles it is currently the default for.

    The caller's thread must already be in an apartment; :func:`co_initialize`
    is called here for convenience and left initialised, because the common
    caller enumerates repeatedly.
    """
    wasapi.co_initialize()
    flows = (flow,) if flow else (FLOW_CAPTURE, FLOW_RENDER)
    enumerator = wasapi.create_enumerator()
    result: list[AudioEndpoint] = []
    try:
        for one_flow in flows:
            flow_value = _FLOW_TO_WASAPI[one_flow]
            defaults = _defaults_by_role(enumerator, flow_value)
            collection = enumerator.enum_endpoints(flow_value, state_mask)
            try:
                for index in range(collection.count()):
                    device = collection.item(index)
                    try:
                        endpoint = _read_endpoint(device, one_flow)
                    finally:
                        device.release()
                    roles = frozenset(
                        role
                        for role, dev_id in defaults.items()
                        if dev_id == endpoint.id
                    )
                    result.append(
                        AudioEndpoint(
                            id=endpoint.id,
                            name=endpoint.name,
                            flow=endpoint.flow,
                            state=endpoint.state,
                            form_factor=endpoint.form_factor,
                            default_for=roles,
                        )
                    )
            finally:
                collection.release()
    finally:
        enumerator.release()
    return result


def default_endpoint(flow: str, role: str = ROLE_COMMUNICATIONS) -> AudioEndpoint | None:
    """The default endpoint for one role, or ``None`` if the role has none."""
    wasapi.co_initialize()
    enumerator = wasapi.create_enumerator()
    try:
        device = enumerator.default_endpoint(
            _FLOW_TO_WASAPI[flow], _ROLE_TO_WASAPI[role]
        )
        if device is None:
            return None
        try:
            endpoint = _read_endpoint(device, flow)
        finally:
            device.release()
        defaults = _defaults_by_role(enumerator, _FLOW_TO_WASAPI[flow])
        roles = frozenset(r for r, i in defaults.items() if i == endpoint.id)
        return AudioEndpoint(
            id=endpoint.id,
            name=endpoint.name,
            flow=endpoint.flow,
            state=endpoint.state,
            form_factor=endpoint.form_factor,
            default_for=roles,
        )
    finally:
        enumerator.release()


def endpoint_by_id(endpoint_id: str) -> AudioEndpoint | None:
    """Look an endpoint up by its persistent identifier."""
    wasapi.co_initialize()
    enumerator = wasapi.create_enumerator()
    try:
        device = enumerator.device(endpoint_id)
        if device is None:
            return None
        try:
            flow = (
                FLOW_RENDER
                if "{0.0.0.00000000}" in endpoint_id
                else FLOW_CAPTURE
                if "{0.0.1.00000000}" in endpoint_id
                else ""
            )
            endpoint = _read_endpoint(device, flow)
        finally:
            device.release()
        return endpoint
    finally:
        enumerator.release()


def resolve_endpoint(
    *,
    flow: str,
    policy_pinned_id: str = "",
    role: str = ROLE_COMMUNICATIONS,
) -> AudioEndpoint:
    """Apply one of the two device policies, failing loudly instead of guessing.

    A pinned device that is not present is an error naming the missing device
    and listing what is available. Falling back to the system default here
    would record the wrong endpoint while reporting success.
    """
    if policy_pinned_id:
        endpoint = endpoint_by_id(policy_pinned_id)
        if endpoint is not None and endpoint.is_active:
            return endpoint
        available = "\n".join(
            f"  - {e.name}\n    {e.id}" for e in list_endpoints(flow)
        )
        raise CaptureError(
            f"O dispositivo fixado nao esta disponivel: {policy_pinned_id}\n"
            f"Dispositivos de {flow} disponiveis:\n{available or '  (nenhum)'}"
        )
    endpoint = default_endpoint(flow, role)
    if endpoint is None:
        raise CaptureError(
            f"Nao existe dispositivo padrao de {flow} para o papel '{role}'."
        )
    return endpoint


def format_endpoints(endpoints: Iterable[AudioEndpoint]) -> str:
    lines: list[str] = []
    for endpoint in endpoints:
        lines.append("  " + endpoint.describe())
    return "\n".join(lines)
