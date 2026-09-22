"""Direct WASAPI (COM) bindings built on plain ctypes.

Why this module exists at all
-----------------------------
PortAudio's WASAPI host API -- and therefore ``PyAudioWPatch``, which wraps it
-- passes ``NULL`` for the last two parameters of
``IAudioCaptureClient::GetBuffer``. It throws away the device position and the
QPC timestamp, and synthesises ``inputBufferAdcTime`` as "wall clock read
inside the callback, plus an estimated latency". That value *carries* the
scheduling delay that alignment exists to remove, so building on it would
reproduce the defect while looking correct. It also never inspects the
discontinuity flag.

So the backend has to call ``GetBuffer`` itself, with real out-pointers for
``pu64DevicePosition`` and ``pu64QPCPosition``. That is the single reason this
file is hand-written COM instead of a dependency.

Why plain ctypes and not comtypes/pywin32
-----------------------------------------
``comtypes`` generates type-library wrappers at import time and ``pywin32``
loads a large extension; both are startup cost on every surface that touches
audio. Everything needed here is eight interfaces and about twenty methods,
which ctypes expresses directly. Fewer dependencies is a product requirement,
not a preference.

Threading model
---------------
All COM work happens in the multi-threaded apartment (MTA). WASAPI's objects
are free-threaded, so a stream opened on its reader thread can be stopped from
another thread without marshalling. Every thread that touches these objects
must call :func:`co_initialize` first.
"""

from __future__ import annotations

import ctypes
from ctypes import (
    POINTER,
    Structure,
    Union,
    byref,
    c_byte,
    c_int,
    c_int32,
    c_int64,
    c_uint32,
    c_uint64,
    c_ulong,
    c_ushort,
    c_void_p,
    c_wchar_p,
)
from typing import Final

from ..errors import CaptureError, DeviceLostError

# --------------------------------------------------------------------------
# primitive aliases, named the way the Windows headers name them
# --------------------------------------------------------------------------

HRESULT = c_int32
REFERENCE_TIME = c_int64  # 100-nanosecond units
DWORD = c_uint32
HANDLE = c_void_p

_ole32 = ctypes.WinDLL("ole32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:  # present since Vista; absent only on stripped images
    _avrt: ctypes.WinDLL | None = ctypes.WinDLL("avrt", use_last_error=True)
except OSError:  # pragma: no cover - not reachable on a normal desktop
    _avrt = None

# --------------------------------------------------------------------------
# HRESULT values we branch on
# --------------------------------------------------------------------------

S_OK: Final = 0x00000000
S_FALSE: Final = 0x00000001
RPC_E_CHANGED_MODE: Final = 0x80010106

AUDCLNT_S_BUFFER_EMPTY: Final = 0x08890001
AUDCLNT_E_DEVICE_INVALIDATED: Final = 0x88890004
AUDCLNT_E_BUFFER_TOO_LARGE: Final = 0x88890006
AUDCLNT_E_UNSUPPORTED_FORMAT: Final = 0x88890008
AUDCLNT_E_DEVICE_IN_USE: Final = 0x8889000A
AUDCLNT_E_ENDPOINT_CREATE_FAILED: Final = 0x8889001A
AUDCLNT_E_SERVICE_NOT_RUNNING: Final = 0x88890010
AUDCLNT_E_EXCLUSIVE_MODE_ONLY: Final = 0x88890012
AUDCLNT_E_RESOURCES_INVALIDATED: Final = 0x88890026

#: Messages are what a person reads, so they are in the product's language.
_HRESULT_TEXT: Final[dict[int, str]] = {
    AUDCLNT_E_DEVICE_INVALIDATED: "o dispositivo foi removido ou reconfigurado",
    AUDCLNT_E_DEVICE_IN_USE: "o dispositivo esta em uso em modo exclusivo",
    AUDCLNT_E_UNSUPPORTED_FORMAT: "o formato pedido nao e suportado pelo dispositivo",
    AUDCLNT_E_SERVICE_NOT_RUNNING: "o servico de audio do Windows nao esta em execucao",
    AUDCLNT_E_ENDPOINT_CREATE_FAILED: "o Windows nao conseguiu criar o ponto de extremidade",
    AUDCLNT_E_EXCLUSIVE_MODE_ONLY: "o dispositivo so aceita modo exclusivo",
    AUDCLNT_E_RESOURCES_INVALIDATED: "os recursos do fluxo foram invalidados",
    AUDCLNT_E_BUFFER_TOO_LARGE: "o buffer pedido e maior do que o dispositivo aceita",
    0x80070005: "acesso negado ao dispositivo (verifique a privacidade do microfone)",
    0x80004005: "falha nao especificada do subsistema de audio",
    0x80040154: "classe COM nao registrada; o endpoint existe mas nao esta utilizavel",
    0x80070490: "elemento nao encontrado",
}

# --------------------------------------------------------------------------
# WASAPI enumerations and flags
# --------------------------------------------------------------------------

# EDataFlow
E_RENDER: Final = 0
E_CAPTURE: Final = 1
E_ALL: Final = 2

# ERole -- Windows keeps one default endpoint per role, and they diverge.
E_CONSOLE: Final = 0
E_MULTIMEDIA: Final = 1
E_COMMUNICATIONS: Final = 2

# DEVICE_STATE_XXX
DEVICE_STATE_ACTIVE: Final = 0x00000001
DEVICE_STATE_DISABLED: Final = 0x00000002
DEVICE_STATE_NOTPRESENT: Final = 0x00000004
DEVICE_STATE_UNPLUGGED: Final = 0x00000008
DEVICE_STATEMASK_ALL: Final = 0x0000000F

# AUDCLNT_SHAREMODE
AUDCLNT_SHAREMODE_SHARED: Final = 0
AUDCLNT_SHAREMODE_EXCLUSIVE: Final = 1

# AUDCLNT_STREAMFLAGS_XXX
AUDCLNT_STREAMFLAGS_CROSSPROCESS: Final = 0x00010000
#: Captures the mix a *render* endpoint is playing. No virtual cable, no driver.
AUDCLNT_STREAMFLAGS_LOOPBACK: Final = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK: Final = 0x00040000
AUDCLNT_STREAMFLAGS_NOPERSIST: Final = 0x00080000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM: Final = 0x80000000

# AUDCLNT_BUFFERFLAGS -- the third of the three data points the gate demands.
AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY: Final = 0x2
AUDCLNT_BUFFERFLAGS_SILENT: Final = 0x1
AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR: Final = 0x4

CLSCTX_ALL: Final = 0x17
STGM_READ: Final = 0x00000000

COINIT_MULTITHREADED: Final = 0x0

# wave format tags
WAVE_FORMAT_PCM: Final = 0x0001
WAVE_FORMAT_IEEE_FLOAT: Final = 0x0003
WAVE_FORMAT_EXTENSIBLE: Final = 0xFFFE

VT_EMPTY: Final = 0
VT_UI4: Final = 19
VT_LPWSTR: Final = 31

INFINITE: Final = 0xFFFFFFFF
WAIT_OBJECT_0: Final = 0x00000000
WAIT_TIMEOUT: Final = 0x00000102


# --------------------------------------------------------------------------
# structures
# --------------------------------------------------------------------------


class GUID(Structure):
    """The 16-byte COM identifier, laid out exactly as ``guiddef.h`` has it."""

    _fields_ = [
        ("Data1", c_uint32),
        ("Data2", c_ushort),
        ("Data3", c_ushort),
        ("Data4", c_byte * 8),
    ]


def _guid(text: str) -> GUID:
    """Build a GUID from its canonical ``{xxxxxxxx-xxxx-...}`` spelling."""
    raw = text.strip("{}").replace("-", "")
    if len(raw) != 32:
        raise ValueError(f"GUID malformado: {text!r}")
    g = GUID()
    g.Data1 = int(raw[0:8], 16)
    g.Data2 = int(raw[8:12], 16)
    g.Data3 = int(raw[12:16], 16)
    tail = bytes.fromhex(raw[16:32])
    for i, b in enumerate(tail):
        g.Data4[i] = b - 256 if b > 127 else b
    return g


def guid_to_str(g: GUID) -> str:
    tail = bytes(b & 0xFF for b in g.Data4)
    return (
        "{%08X-%04X-%04X-%s-%s}"
        % (
            g.Data1 & 0xFFFFFFFF,
            g.Data2 & 0xFFFF,
            g.Data3 & 0xFFFF,
            tail[:2].hex().upper(),
            tail[2:].hex().upper(),
        )
    )


GUID.__str__ = guid_to_str  # type: ignore[assignment]


class PROPERTYKEY(Structure):
    _fields_ = [("fmtid", GUID), ("pid", DWORD)]


class _PropVariantValue(Union):
    _fields_ = [
        ("pwszVal", c_void_p),
        ("uhVal", c_uint64),
        ("ulVal", c_uint32),
        ("_raw", c_byte * 16),
    ]


class PROPVARIANT(Structure):
    """Only the members this module reads are named; the rest is padding."""

    _fields_ = [
        ("vt", c_ushort),
        ("wReserved1", c_ushort),
        ("wReserved2", c_ushort),
        ("wReserved3", c_ushort),
        ("value", _PropVariantValue),
    ]


class WAVEFORMATEX(Structure):
    """``mmreg.h`` packs this to 1; sizeof must be 18, not 20.

    Getting the packing wrong shifts every field after ``nSamplesPerSec`` and
    produces a format that WASAPI rejects with an unhelpful error, so
    :mod:`tests.capture.test_format` asserts both sizes.
    """

    _pack_ = 1
    _fields_ = [
        ("wFormatTag", c_ushort),
        ("nChannels", c_ushort),
        ("nSamplesPerSec", DWORD),
        ("nAvgBytesPerSec", DWORD),
        ("nBlockAlign", c_ushort),
        ("wBitsPerSample", c_ushort),
        ("cbSize", c_ushort),
    ]


class _Samples(Union):
    _pack_ = 1
    _fields_ = [
        ("wValidBitsPerSample", c_ushort),
        ("wSamplesPerBlock", c_ushort),
        ("wReserved", c_ushort),
    ]


class WAVEFORMATEXTENSIBLE(Structure):
    _pack_ = 1
    _fields_ = [
        ("Format", WAVEFORMATEX),
        ("Samples", _Samples),
        ("dwChannelMask", DWORD),
        ("SubFormat", GUID),
    ]


# --------------------------------------------------------------------------
# identifiers
# --------------------------------------------------------------------------

CLSID_MMDeviceEnumerator: Final = _guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator: Final = _guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IMMNotificationClient: Final = _guid("{7991EEC9-7E89-4D85-8390-6C703CEC60C0}")
IID_IAudioClient: Final = _guid("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
IID_IAudioCaptureClient: Final = _guid("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
IID_IAudioRenderClient: Final = _guid("{F294ACFC-3146-4483-A7BF-ADDCA7C260E2}")
IID_IUnknown: Final = _guid("{00000000-0000-0000-C000-000000000046}")

KSDATAFORMAT_SUBTYPE_PCM: Final = _guid("{00000001-0000-0010-8000-00AA00389B71}")
KSDATAFORMAT_SUBTYPE_IEEE_FLOAT: Final = _guid("{00000003-0000-0010-8000-00AA00389B71}")

PKEY_Device_FriendlyName: Final = PROPERTYKEY(
    _guid("{A45C254E-DF1C-4EFD-8020-67D146A850E0}"), 14
)
PKEY_DeviceInterface_FriendlyName: Final = PROPERTYKEY(
    _guid("{026E516E-B814-414B-83CD-856D6FEF4822}"), 2
)
PKEY_AudioEndpoint_FormFactor: Final = PROPERTYKEY(
    _guid("{1DA5D803-D492-4EDD-8C23-E0C0FFEE7F0E}"), 0
)
PKEY_AudioEndpoint_GUID: Final = PROPERTYKEY(
    _guid("{1DA5D803-D492-4EDD-8C23-E0C0FFEE7F0E}"), 4
)

#: EndpointFormFactor, used by the echo-risk heuristic further up the stack.
FORM_FACTOR_NAMES: Final[dict[int, str]] = {
    0: "RemoteNetworkDevice",
    1: "Speakers",
    2: "LineLevel",
    3: "Headphones",
    4: "Microphone",
    5: "Headset",
    6: "Handset",
    7: "UnknownDigitalPassthrough",
    8: "SPDIF",
    9: "DigitalAudioDisplayDevice",
    10: "UnknownFormFactor",
}


# --------------------------------------------------------------------------
# error handling
# --------------------------------------------------------------------------


def hresult_text(hr: int) -> str:
    code = hr & 0xFFFFFFFF
    known = _HRESULT_TEXT.get(code)
    return f"0x{code:08X}" + (f" ({known})" if known else "")


def check(hr: int, what: str) -> int:
    """Raise on a failed HRESULT, naming the call that failed.

    ``AUDCLNT_E_DEVICE_INVALIDATED`` becomes :class:`DeviceLostError` because
    the recovery path above cares about that case specifically: it is the
    difference between "retry this endpoint for 30 s" and "give up".
    """
    if hr >= 0:
        return hr
    code = hr & 0xFFFFFFFF
    if code in (AUDCLNT_E_DEVICE_INVALIDATED, AUDCLNT_E_RESOURCES_INVALIDATED):
        raise DeviceLostError(f"{what} falhou: {hresult_text(hr)}")
    raise CaptureError(f"{what} falhou: {hresult_text(hr)}")


# --------------------------------------------------------------------------
# minimal COM vtable dispatch
# --------------------------------------------------------------------------

_IUNKNOWN_SLOTS: Final = 3  # QueryInterface, AddRef, Release

_Release_proto = ctypes.WINFUNCTYPE(c_ulong, c_void_p)
_QueryInterface_proto = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_void_p, POINTER(c_void_p))


class ComPtr:
    """A raw interface pointer with its vtable methods bound as attributes.

    Each subclass declares ``_vtbl_`` in vtable order, starting after
    ``IUnknown``. Binding happens once per instance, so the capture loop calls
    a ctypes foreign function directly instead of walking the vtable on every
    packet.
    """

    _vtbl_: tuple[tuple[str, type, tuple], ...] = ()
    _protos_: tuple[tuple[str, int, type], ...] = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        protos = []
        for offset, (name, restype, argtypes) in enumerate(
            cls._vtbl_, start=_IUNKNOWN_SLOTS
        ):
            protos.append(
                (name, offset, ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes))
            )
        cls._protos_ = tuple(protos)

    def __init__(self, ptr: int | c_void_p) -> None:
        raw = ptr.value if isinstance(ptr, c_void_p) else ptr
        if not raw:
            raise CaptureError(f"{type(self).__name__}: ponteiro COM nulo")
        self.this = c_void_p(raw)
        vtbl = ctypes.cast(self.this, POINTER(POINTER(c_void_p)))[0]
        for name, offset, proto in self._protos_:
            setattr(self, name, proto(vtbl[offset]))
        self._release = _Release_proto(vtbl[2])
        self._released = False

    def release(self) -> None:
        if not self._released and self.this:
            self._released = True
            self._release(self.this)
            self.this = c_void_p(0)

    def __enter__(self):  # noqa: D105
        return self

    def __exit__(self, *exc: object) -> None:  # noqa: D105
        self.release()


class IMMDeviceCollection(ComPtr):
    _vtbl_ = (
        ("_GetCount", HRESULT, (POINTER(c_uint32),)),
        ("_Item", HRESULT, (c_uint32, POINTER(c_void_p))),
    )

    def count(self) -> int:
        n = c_uint32()
        check(self._GetCount(self.this, byref(n)), "IMMDeviceCollection::GetCount")
        return n.value

    def item(self, index: int) -> "IMMDevice":
        p = c_void_p()
        check(self._Item(self.this, index, byref(p)), "IMMDeviceCollection::Item")
        return IMMDevice(p)


class IPropertyStore(ComPtr):
    _vtbl_ = (
        ("_GetCount", HRESULT, (POINTER(DWORD),)),
        ("_GetAt", HRESULT, (DWORD, POINTER(PROPERTYKEY))),
        ("_GetValue", HRESULT, (POINTER(PROPERTYKEY), POINTER(PROPVARIANT))),
        ("_SetValue", HRESULT, (POINTER(PROPERTYKEY), POINTER(PROPVARIANT))),
        ("_Commit", HRESULT, ()),
    )

    def get_string(self, key: PROPERTYKEY, default: str = "") -> str:
        pv = PROPVARIANT()
        hr = self._GetValue(self.this, byref(key), byref(pv))
        if hr < 0:
            return default
        try:
            if pv.vt == VT_LPWSTR and pv.value.pwszVal:
                return ctypes.wstring_at(pv.value.pwszVal)
            return default
        finally:
            _ole32.PropVariantClear(byref(pv))

    def get_uint(self, key: PROPERTYKEY, default: int = -1) -> int:
        pv = PROPVARIANT()
        hr = self._GetValue(self.this, byref(key), byref(pv))
        if hr < 0:
            return default
        try:
            if pv.vt == VT_UI4:
                return int(pv.value.ulVal)
            return default
        finally:
            _ole32.PropVariantClear(byref(pv))


class IMMDevice(ComPtr):
    _vtbl_ = (
        ("_Activate", HRESULT, (POINTER(GUID), DWORD, POINTER(PROPVARIANT), POINTER(c_void_p))),
        ("_OpenPropertyStore", HRESULT, (DWORD, POINTER(c_void_p))),
        ("_GetId", HRESULT, (POINTER(c_void_p),)),
        ("_GetState", HRESULT, (POINTER(DWORD),)),
    )

    def id(self) -> str:
        """The endpoint ID string: stable across reboot and reconnection.

        This is the persistent identifier the pinned-device policy needs. The
        friendly name is not: two identical headsets share it.
        """
        p = c_void_p()
        check(self._GetId(self.this, byref(p)), "IMMDevice::GetId")
        try:
            return ctypes.wstring_at(p)
        finally:
            _ole32.CoTaskMemFree(p)

    def state(self) -> int:
        s = DWORD()
        check(self._GetState(self.this, byref(s)), "IMMDevice::GetState")
        return s.value

    def property_store(self) -> IPropertyStore:
        p = c_void_p()
        check(
            self._OpenPropertyStore(self.this, STGM_READ, byref(p)),
            "IMMDevice::OpenPropertyStore",
        )
        return IPropertyStore(p)

    def activate_audio_client(self) -> "IAudioClient":
        p = c_void_p()
        check(
            self._Activate(
                self.this, byref(IID_IAudioClient), CLSCTX_ALL, None, byref(p)
            ),
            "IMMDevice::Activate(IAudioClient)",
        )
        return IAudioClient(p)


class IMMDeviceEnumerator(ComPtr):
    _vtbl_ = (
        ("_EnumAudioEndpoints", HRESULT, (c_int, DWORD, POINTER(c_void_p))),
        ("_GetDefaultAudioEndpoint", HRESULT, (c_int, c_int, POINTER(c_void_p))),
        ("_GetDevice", HRESULT, (c_wchar_p, POINTER(c_void_p))),
        ("_RegisterEndpointNotificationCallback", HRESULT, (c_void_p,)),
        ("_UnregisterEndpointNotificationCallback", HRESULT, (c_void_p,)),
    )

    def enum_endpoints(
        self, flow: int, state_mask: int = DEVICE_STATE_ACTIVE
    ) -> IMMDeviceCollection:
        p = c_void_p()
        check(
            self._EnumAudioEndpoints(self.this, flow, state_mask, byref(p)),
            "IMMDeviceEnumerator::EnumAudioEndpoints",
        )
        return IMMDeviceCollection(p)

    def default_endpoint(self, flow: int, role: int) -> IMMDevice | None:
        """``None`` when the role has no endpoint -- a normal state, not an error."""
        p = c_void_p()
        hr = self._GetDefaultAudioEndpoint(self.this, flow, role, byref(p))
        if hr < 0:
            if (hr & 0xFFFFFFFF) == 0x80070490:  # ERROR_NOT_FOUND
                return None
            check(hr, "IMMDeviceEnumerator::GetDefaultAudioEndpoint")
        return IMMDevice(p)

    def device(self, endpoint_id: str) -> IMMDevice | None:
        p = c_void_p()
        hr = self._GetDevice(self.this, endpoint_id, byref(p))
        if hr < 0:
            return None
        return IMMDevice(p)

    def register_notifications(self, client: "NotificationClient") -> None:
        check(
            self._RegisterEndpointNotificationCallback(self.this, client.interface),
            "IMMDeviceEnumerator::RegisterEndpointNotificationCallback",
        )

    def unregister_notifications(self, client: "NotificationClient") -> None:
        self._UnregisterEndpointNotificationCallback(self.this, client.interface)


class IAudioClient(ComPtr):
    _vtbl_ = (
        (
            "_Initialize",
            HRESULT,
            (c_int, DWORD, REFERENCE_TIME, REFERENCE_TIME, c_void_p, POINTER(GUID)),
        ),
        ("_GetBufferSize", HRESULT, (POINTER(c_uint32),)),
        ("_GetStreamLatency", HRESULT, (POINTER(REFERENCE_TIME),)),
        ("_GetCurrentPadding", HRESULT, (POINTER(c_uint32),)),
        ("_IsFormatSupported", HRESULT, (c_int, c_void_p, POINTER(c_void_p))),
        ("_GetMixFormat", HRESULT, (POINTER(c_void_p),)),
        (
            "_GetDevicePeriod",
            HRESULT,
            (POINTER(REFERENCE_TIME), POINTER(REFERENCE_TIME)),
        ),
        ("_Start", HRESULT, ()),
        ("_Stop", HRESULT, ()),
        ("_Reset", HRESULT, ()),
        ("_SetEventHandle", HRESULT, (HANDLE,)),
        ("_GetService", HRESULT, (POINTER(GUID), POINTER(c_void_p))),
    )

    def mix_format(self) -> tuple[bytes, c_void_p]:
        """Return the mix format as bytes plus the pointer the caller must free.

        The bytes copy exists so parsing is a pure function over a byte string
        and can be unit-tested without a device.
        """
        p = c_void_p()
        check(self._GetMixFormat(self.this, byref(p)), "IAudioClient::GetMixFormat")
        head = ctypes.string_at(p, ctypes.sizeof(WAVEFORMATEX))
        cb_size = int.from_bytes(head[16:18], "little")
        raw = ctypes.string_at(p, ctypes.sizeof(WAVEFORMATEX) + cb_size)
        return raw, p

    def initialize(
        self,
        *,
        share_mode: int,
        stream_flags: int,
        buffer_duration_hns: int,
        periodicity_hns: int,
        format_ptr: c_void_p,
    ) -> None:
        check(
            self._Initialize(
                self.this,
                share_mode,
                stream_flags,
                buffer_duration_hns,
                periodicity_hns,
                format_ptr,
                None,
            ),
            "IAudioClient::Initialize",
        )

    def buffer_size(self) -> int:
        n = c_uint32()
        check(self._GetBufferSize(self.this, byref(n)), "IAudioClient::GetBufferSize")
        return n.value

    def current_padding(self) -> int:
        n = c_uint32()
        check(
            self._GetCurrentPadding(self.this, byref(n)),
            "IAudioClient::GetCurrentPadding",
        )
        return n.value

    def stream_latency_hns(self) -> int:
        t = REFERENCE_TIME()
        hr = self._GetStreamLatency(self.this, byref(t))
        return t.value if hr >= 0 else -1

    def device_period_hns(self) -> tuple[int, int]:
        default = REFERENCE_TIME()
        minimum = REFERENCE_TIME()
        check(
            self._GetDevicePeriod(self.this, byref(default), byref(minimum)),
            "IAudioClient::GetDevicePeriod",
        )
        return default.value, minimum.value

    def set_event_handle(self, handle: int) -> None:
        check(
            self._SetEventHandle(self.this, HANDLE(handle)),
            "IAudioClient::SetEventHandle",
        )

    def start(self) -> None:
        check(self._Start(self.this), "IAudioClient::Start")

    def stop(self) -> None:
        self._Stop(self.this)

    def reset(self) -> None:
        self._Reset(self.this)

    def capture_client(self) -> "IAudioCaptureClient":
        p = c_void_p()
        check(
            self._GetService(self.this, byref(IID_IAudioCaptureClient), byref(p)),
            "IAudioClient::GetService(IAudioCaptureClient)",
        )
        return IAudioCaptureClient(p)

    def render_client(self) -> "IAudioRenderClient":
        p = c_void_p()
        check(
            self._GetService(self.this, byref(IID_IAudioRenderClient), byref(p)),
            "IAudioClient::GetService(IAudioRenderClient)",
        )
        return IAudioRenderClient(p)


class IAudioCaptureClient(ComPtr):
    """The interface the whole backend decision rests on.

    ``GetBuffer``'s last two parameters are declared as real out-pointers here
    and passed as real out-pointers at every call site. Passing ``NULL`` there
    -- what PortAudio does -- is precisely the defect that disqualified it.
    """

    _vtbl_ = (
        (
            "_GetBuffer",
            HRESULT,
            (
                POINTER(c_void_p),  # BYTE **ppData
                POINTER(c_uint32),  # UINT32 *pNumFramesToRead
                POINTER(DWORD),  # DWORD *pdwFlags
                POINTER(c_uint64),  # UINT64 *pu64DevicePosition
                POINTER(c_uint64),  # UINT64 *pu64QPCPosition
            ),
        ),
        ("_ReleaseBuffer", HRESULT, (c_uint32,)),
        ("_GetNextPacketSize", HRESULT, (POINTER(c_uint32),)),
    )

    def next_packet_size(self) -> int:
        n = c_uint32()
        check(
            self._GetNextPacketSize(self.this, byref(n)),
            "IAudioCaptureClient::GetNextPacketSize",
        )
        return n.value


class IAudioRenderClient(ComPtr):
    """Used only by the validation gate, to feed a known tone into loopback.

    Production never renders: playing audio during a recording would be picked
    up by the loopback track as if it were a participant.
    """

    _vtbl_ = (
        ("_GetBuffer", HRESULT, (c_uint32, POINTER(c_void_p))),
        ("_ReleaseBuffer", HRESULT, (c_uint32, DWORD)),
    )

    def get_buffer(self, frames: int) -> c_void_p:
        p = c_void_p()
        check(self._GetBuffer(self.this, frames, byref(p)), "IAudioRenderClient::GetBuffer")
        return p

    def release_buffer(self, frames: int, flags: int = 0) -> None:
        check(
            self._ReleaseBuffer(self.this, frames, flags),
            "IAudioRenderClient::ReleaseBuffer",
        )


# --------------------------------------------------------------------------
# IMMNotificationClient implemented in Python
# --------------------------------------------------------------------------

_NC_QueryInterface = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_void_p, POINTER(c_void_p))
_NC_AddRef = ctypes.WINFUNCTYPE(c_ulong, c_void_p)
_NC_Release = ctypes.WINFUNCTYPE(c_ulong, c_void_p)
_NC_OnDeviceStateChanged = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_wchar_p, DWORD)
_NC_OnDeviceAdded = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_wchar_p)
_NC_OnDeviceRemoved = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_wchar_p)
_NC_OnDefaultDeviceChanged = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_int, c_int, c_wchar_p)
_NC_OnPropertyValueChanged = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_wchar_p, PROPERTYKEY)


class _NotificationVtbl(Structure):
    _fields_ = [
        ("QueryInterface", _NC_QueryInterface),
        ("AddRef", _NC_AddRef),
        ("Release", _NC_Release),
        ("OnDeviceStateChanged", _NC_OnDeviceStateChanged),
        ("OnDeviceAdded", _NC_OnDeviceAdded),
        ("OnDeviceRemoved", _NC_OnDeviceRemoved),
        ("OnDefaultDeviceChanged", _NC_OnDefaultDeviceChanged),
        ("OnPropertyValueChanged", _NC_OnPropertyValueChanged),
    ]


class _NotificationObject(Structure):
    _fields_ = [("lpVtbl", POINTER(_NotificationVtbl))]


class NotificationClient:
    """A COM object, implemented in Python, that Windows calls on device events.

    It exists because the default endpoint for a role changes *without* the
    open stream failing: plugging in a headset leaves the previous device
    working, so waiting for a stream error would keep recording the wrong
    endpoint. Callbacks run on an MMDevice worker thread, so they do nothing
    but append to a deque -- any real work belongs to whoever drains it.
    """

    def __init__(self) -> None:
        from collections import deque

        self.events: deque[tuple[str, tuple]] = deque(maxlen=256)
        self._vtbl = _NotificationVtbl(
            _NC_QueryInterface(self._query_interface),
            _NC_AddRef(lambda this: 2),
            _NC_Release(lambda this: 1),
            _NC_OnDeviceStateChanged(self._on_state),
            _NC_OnDeviceAdded(self._on_added),
            _NC_OnDeviceRemoved(self._on_removed),
            _NC_OnDefaultDeviceChanged(self._on_default),
            _NC_OnPropertyValueChanged(self._on_property),
        )
        self._obj = _NotificationObject(ctypes.pointer(self._vtbl))
        self.interface = ctypes.cast(ctypes.byref(self._obj), c_void_p)

    # -- IUnknown ---------------------------------------------------------
    def _query_interface(self, this: int, riid: int, out: object) -> int:
        try:
            wanted = ctypes.cast(riid, POINTER(GUID))[0]
            if guid_to_str(wanted) in (
                guid_to_str(IID_IUnknown),
                guid_to_str(IID_IMMNotificationClient),
            ):
                out[0] = self.interface
                return S_OK
            out[0] = None
        except Exception:  # pragma: no cover - a callback must never raise
            pass
        return -2147467262  # E_NOINTERFACE

    # -- IMMNotificationClient -------------------------------------------
    def _record(self, kind: str, payload: tuple) -> int:
        try:
            self.events.append((kind, payload))
        except Exception:  # pragma: no cover
            pass
        return S_OK

    def _on_state(self, this: int, device_id: str, state: int) -> int:
        return self._record("state", (device_id, state))

    def _on_added(self, this: int, device_id: str) -> int:
        return self._record("added", (device_id,))

    def _on_removed(self, this: int, device_id: str) -> int:
        return self._record("removed", (device_id,))

    def _on_default(self, this: int, flow: int, role: int, device_id: str) -> int:
        return self._record("default", (flow, role, device_id))

    def _on_property(self, this: int, device_id: str, key: PROPERTYKEY) -> int:
        return self._record("property", (device_id, key.pid))


# --------------------------------------------------------------------------
# process / thread services
# --------------------------------------------------------------------------

_ole32.CoInitializeEx.argtypes = [c_void_p, DWORD]
_ole32.CoInitializeEx.restype = HRESULT
_ole32.CoUninitialize.restype = None
_ole32.CoTaskMemFree.argtypes = [c_void_p]
_ole32.CoTaskMemFree.restype = None
_ole32.CoCreateInstance.argtypes = [
    POINTER(GUID),
    c_void_p,
    DWORD,
    POINTER(GUID),
    POINTER(c_void_p),
]
_ole32.CoCreateInstance.restype = HRESULT
_ole32.PropVariantClear.argtypes = [POINTER(PROPVARIANT)]
_ole32.PropVariantClear.restype = HRESULT

_kernel32.CreateEventW.argtypes = [c_void_p, c_int, c_int, c_wchar_p]
_kernel32.CreateEventW.restype = HANDLE
_kernel32.SetEvent.argtypes = [HANDLE]
_kernel32.SetEvent.restype = c_int
_kernel32.CloseHandle.argtypes = [HANDLE]
_kernel32.CloseHandle.restype = c_int
_kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
_kernel32.WaitForSingleObject.restype = DWORD
_kernel32.QueryPerformanceCounter.argtypes = [POINTER(c_int64)]
_kernel32.QueryPerformanceCounter.restype = c_int
_kernel32.QueryPerformanceFrequency.argtypes = [POINTER(c_int64)]
_kernel32.QueryPerformanceFrequency.restype = c_int

if _avrt is not None:
    _avrt.AvSetMmThreadCharacteristicsW.argtypes = [c_wchar_p, POINTER(DWORD)]
    _avrt.AvSetMmThreadCharacteristicsW.restype = HANDLE
    _avrt.AvRevertMmThreadCharacteristics.argtypes = [HANDLE]
    _avrt.AvRevertMmThreadCharacteristics.restype = c_int


def _qpc_frequency() -> int:
    freq = c_int64()
    _kernel32.QueryPerformanceFrequency(byref(freq))
    return freq.value or 10_000_000


QPC_FREQUENCY: Final = _qpc_frequency()


def qpc_now_ns() -> int:
    """Read the same high-resolution clock WASAPI stamps packets with.

    Comparing this against a packet's ``qpc_ns`` gives the delivery delay: the
    quantity that must jitter under load while the reported acquisition
    instants do not.
    """
    counter = c_int64()
    _kernel32.QueryPerformanceCounter(byref(counter))
    return counter.value * 1_000_000_000 // QPC_FREQUENCY


def co_initialize() -> bool:
    """Join the multi-threaded apartment. Returns True if this call did it."""
    hr = _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    code = hr & 0xFFFFFFFF
    if hr >= 0:
        return hr == S_OK
    if code == RPC_E_CHANGED_MODE:
        # Another library already put this thread in an STA. WASAPI objects
        # still work; only the apartment we asked for was denied.
        return False
    check(hr, "CoInitializeEx")
    return False


def co_uninitialize() -> None:
    _ole32.CoUninitialize()


def create_enumerator() -> IMMDeviceEnumerator:
    p = c_void_p()
    check(
        _ole32.CoCreateInstance(
            byref(CLSID_MMDeviceEnumerator),
            None,
            CLSCTX_ALL,
            byref(IID_IMMDeviceEnumerator),
            byref(p),
        ),
        "CoCreateInstance(MMDeviceEnumerator)",
    )
    return IMMDeviceEnumerator(p)


def create_event() -> int:
    handle = _kernel32.CreateEventW(None, 0, 0, None)
    if not handle:
        raise CaptureError(
            f"CreateEventW falhou: erro {ctypes.get_last_error()}"
        )
    return handle


def close_handle(handle: int) -> None:
    if handle:
        _kernel32.CloseHandle(HANDLE(handle))


def set_event(handle: int) -> None:
    """Wake a reader blocked on the stream event, so stopping is prompt."""
    if handle:
        _kernel32.SetEvent(HANDLE(handle))


def wait_for_event(handle: int, timeout_ms: int) -> int:
    return _kernel32.WaitForSingleObject(HANDLE(handle), timeout_ms)


def set_pro_audio_priority() -> tuple[int, str]:
    """Join the "Pro Audio" MMCSS task for the calling thread.

    Returns the task handle (0 on failure) and a human-readable status, which
    the gate report records rather than hiding.
    """
    if _avrt is None:
        return 0, "avrt.dll indisponivel"
    index = DWORD(0)
    handle = _avrt.AvSetMmThreadCharacteristicsW("Pro Audio", byref(index))
    if not handle:
        return 0, f"AvSetMmThreadCharacteristicsW falhou (erro {ctypes.get_last_error()})"
    return handle, f"Pro Audio, indice {index.value}"


def revert_pro_audio_priority(handle: int) -> None:
    if _avrt is not None and handle:
        _avrt.AvRevertMmThreadCharacteristics(HANDLE(handle))
