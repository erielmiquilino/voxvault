"""The COM binding layer.

These need Windows but not an audio device: GUID handling, the vtable
signatures, HRESULT classification and the QPC clock are all checkable without
opening anything.
"""

from __future__ import annotations

import ctypes
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="WASAPI so existe no Windows"
)

from voxvault.capture import wasapi  # noqa: E402
from voxvault.errors import CaptureError, DeviceLostError  # noqa: E402


def test_guid_round_trips_through_its_canonical_spelling():
    text = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
    assert wasapi.guid_to_str(wasapi._guid(text)) == text


def test_guid_high_bytes_survive_the_signed_byte_array():
    """``Data4`` is a signed byte array in ctypes; 0xC0 must not become -64."""
    text = "{00000000-0000-0000-C000-000000000046}"
    assert wasapi.guid_to_str(wasapi._guid(text)) == text


def test_a_malformed_guid_is_refused():
    with pytest.raises(ValueError):
        wasapi._guid("{not-a-guid}")


def test_structure_sizes_match_the_windows_headers():
    assert ctypes.sizeof(wasapi.GUID) == 16
    assert ctypes.sizeof(wasapi.PROPERTYKEY) == 20
    assert ctypes.sizeof(wasapi.WAVEFORMATEX) == 18
    assert ctypes.sizeof(wasapi.WAVEFORMATEXTENSIBLE) == 40
    assert ctypes.sizeof(wasapi.PROPVARIANT) == 24


def test_get_buffer_declares_both_out_pointers():
    """The whole backend decision rests on these two parameters existing.

    PortAudio passes NULL for both. Asserting the declared signature keeps a
    future edit from quietly dropping them back to NULL.
    """
    signature = dict(
        (name, argtypes)
        for name, _restype, argtypes in wasapi.IAudioCaptureClient._vtbl_
    )
    argtypes = signature["_GetBuffer"]
    assert len(argtypes) == 5
    assert argtypes[3] == ctypes.POINTER(ctypes.c_uint64)  # pu64DevicePosition
    assert argtypes[4] == ctypes.POINTER(ctypes.c_uint64)  # pu64QPCPosition


def test_vtable_offsets_start_after_iunknown():
    protos = {name: offset for name, offset, _ in wasapi.IAudioClient._protos_}
    assert protos["_Initialize"] == 3
    assert protos["_GetMixFormat"] == 8
    assert protos["_Start"] == 10
    assert protos["_SetEventHandle"] == 13
    assert protos["_GetService"] == 14


def test_a_lost_device_is_classified_apart_from_other_failures():
    """The recovery path differs: retry this endpoint, or give up."""
    with pytest.raises(DeviceLostError):
        wasapi.check(
            ctypes.c_int32(wasapi.AUDCLNT_E_DEVICE_INVALIDATED).value, "teste"
        )
    with pytest.raises(CaptureError) as info:
        wasapi.check(ctypes.c_int32(0x80070005).value, "teste")
    assert not isinstance(info.value, DeviceLostError)
    assert "microfone" in str(info.value)


def test_a_success_hresult_passes_through():
    assert wasapi.check(wasapi.S_OK, "teste") == 0
    assert wasapi.check(wasapi.AUDCLNT_S_BUFFER_EMPTY, "teste") > 0


def test_qpc_reads_the_same_clock_the_packets_are_stamped_with():
    first = wasapi.qpc_now_ns()
    second = wasapi.qpc_now_ns()
    assert second >= first
    assert wasapi.QPC_FREQUENCY > 0
    assert first > 0


def test_the_buffer_flags_are_the_documented_bit_values():
    assert wasapi.AUDCLNT_BUFFERFLAGS_SILENT == 0x1
    assert wasapi.AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY == 0x2
    assert wasapi.AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR == 0x4
    assert wasapi.AUDCLNT_STREAMFLAGS_LOOPBACK == 0x00020000
    assert wasapi.AUDCLNT_STREAMFLAGS_EVENTCALLBACK == 0x00040000


def test_the_enumerator_can_be_created_without_any_device():
    wasapi.co_initialize()
    enumerator = wasapi.create_enumerator()
    try:
        assert enumerator.this.value
    finally:
        enumerator.release()
    assert enumerator.this.value is None


def test_a_notification_client_builds_a_usable_com_object():
    """Default-endpoint changes do not fail the open stream, so we must be told."""
    wasapi.co_initialize()
    client = wasapi.NotificationClient()
    enumerator = wasapi.create_enumerator()
    try:
        enumerator.register_notifications(client)
        enumerator.unregister_notifications(client)
    finally:
        enumerator.release()
    assert client.interface.value


def test_pro_audio_priority_reports_what_happened_either_way():
    handle, status = wasapi.set_pro_audio_priority()
    try:
        assert isinstance(status, str) and status
    finally:
        wasapi.revert_pro_audio_priority(handle)
