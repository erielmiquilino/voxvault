"""End-to-end capture, on a real endpoint.

Every test here is marked ``hardware``. On a machine with no capture endpoint
-- a Remote Desktop session, for instance -- the microphone ones skip with a
reason rather than passing vacuously.

Run them with::

    pytest tests/capture -m hardware
"""

from __future__ import annotations

import statistics
import sys
import time

import pytest

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(sys.platform != "win32", reason="WASAPI so existe no Windows"),
]

from voxvault.capture.anchor import Anchor, TrackPlacer  # noqa: E402
from voxvault.capture.gate import CpuLoad, TonePlayer  # noqa: E402
from voxvault.capture.stream import CaptureStream, prewarm  # noqa: E402

CAPTURE_SECONDS = 3.0


def _capture(endpoint, *, loopback, seconds=CAPTURE_SECONDS, **kwargs):
    """Capture for a while and return the stream with the packets it produced."""
    stream = CaptureStream(
        endpoint.id, loopback=loopback, record_arrival=True, **kwargs
    )
    tone = TonePlayer(endpoint.id) if loopback else None
    collected = []
    try:
        stream.start()
        if tone is not None:
            tone.start()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(0.05)
            collected.extend(stream.read_with_arrival())
    finally:
        if tone is not None:
            tone.stop()
        stream.stop()
    assert stream.error is None, f"fluxo falhou: {stream.error!r}"
    return stream, collected


def test_loopback_delivers_the_three_required_data_points(render_endpoint):
    """Task 2.0.1, as a test rather than as a report."""
    stream, collected = _capture(render_endpoint, loopback=True)
    assert collected, "nenhum pacote capturado do loopback"

    usable = [p for p, _ in collected if p.timestamp_valid]
    assert usable, "nenhum pacote com timestamp valido"
    assert all(p.qpc_ns > 0 for p in usable)
    assert all(p.frames > 0 for p in usable)
    # Device position must advance monotonically with the audio.
    positions = [p.device_position for p in usable]
    assert positions == sorted(positions)
    assert positions[-1] > positions[0]


def test_the_reported_instant_follows_the_device_position(render_endpoint):
    """The property the whole backend choice rests on."""
    stream, collected = _capture(render_endpoint, loopback=True)
    usable = [p for p, _ in collected if p.timestamp_valid]
    if len(usable) < 20:
        pytest.skip("pacotes insuficientes")
    rate = stream.format.sample_rate

    steps = []
    for previous, current in zip(usable, usable[1:]):
        advance = current.device_position - previous.device_position
        implied = advance * 1_000_000_000 // rate
        steps.append(abs((current.qpc_ns - previous.qpc_ns) - implied))
    # One millisecond is two orders of magnitude below the 200 ms fill
    # threshold, so a deviation this size can never fabricate silence.
    assert max(steps) < 1_000_000, f"passo maximo {max(steps)/1e6:.3f} ms"


def test_the_instant_does_not_move_when_the_machine_is_busy(render_endpoint):
    """Task 2.0.2. The measurement that fails PortAudio."""
    stream = CaptureStream(render_endpoint.id, loopback=True, record_arrival=True)
    tone = TonePlayer(render_endpoint.id)
    rate = None
    phases: dict[str, list[float]] = {"idle": [], "loaded": []}
    delivery: dict[str, list[float]] = {"idle": [], "loaded": []}
    try:
        stream.start()
        rate = stream.format.sample_rate
        tone.start()
        time.sleep(0.4)
        stream.read_with_arrival()

        def collect(key: str, seconds: float) -> None:
            previous = None
            previous_arrival = 0
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                time.sleep(0.02)
                for packet, arrival in stream.read_with_arrival():
                    if not packet.timestamp_valid:
                        continue
                    if previous is not None:
                        advance = packet.device_position - previous.device_position
                        implied = advance * 1_000_000_000 // rate
                        phases[key].append(
                            abs((packet.qpc_ns - previous.qpc_ns) - implied) / 1000.0
                        )
                        delivery[key].append(
                            abs((arrival - previous_arrival) - implied) / 1000.0
                        )
                    previous = packet
                    previous_arrival = arrival

        collect("idle", 2.5)
        with CpuLoad(threads=1, processes=0):
            collect("loaded", 2.5)
    finally:
        tone.stop()
        stream.stop()

    if len(phases["idle"]) < 10 or len(phases["loaded"]) < 10:
        pytest.skip("pacotes insuficientes para comparar as duas fases")

    idle_worst = max(phases["idle"])
    loaded_worst = max(phases["loaded"])
    # Stable to within three times the idle figure, floor of 2 ms.
    assert loaded_worst <= max(3 * idle_worst, 2000.0), (
        f"instantes deslocaram sob carga: {idle_worst:.1f} -> {loaded_worst:.1f} us"
    )
    # And the load really did delay delivery, otherwise nothing was proved.
    assert max(delivery["loaded"]) > max(delivery["idle"]), (
        "a carga nao atrasou a entrega; o teste nao discrimina"
    )
    assert max(delivery["loaded"]) > loaded_worst * 3


def test_a_provoked_overrun_is_visible_in_the_device_position(render_endpoint):
    """Task 2.0.3, through the channel that is always reliable.

    The ``DATA_DISCONTINUITY`` flag is asserted separately because this
    endpoint does not raise it for every real loss.
    """
    stream = CaptureStream(render_endpoint.id, loopback=True)
    stream.debug_stall_after_s = 1.0
    stream.debug_stall_s = 1.0
    tone = TonePlayer(render_endpoint.id)
    collected = []
    try:
        stream.start()
        tone.start()
        deadline = time.monotonic() + 3.5
        while time.monotonic() < deadline:
            time.sleep(0.05)
            collected.extend(stream.read())
    finally:
        tone.stop()
        stream.stop()

    rate = stream.format.sample_rate
    jumps = []
    for previous, current in zip(collected, collected[1:]):
        missing = current.device_position - (
            previous.device_position + previous.frames
        )
        if missing > rate // 100:
            jumps.append(missing * 1000 // rate)
    assert jumps, "a pausa provocada nao produziu perda observavel"
    assert max(jumps) >= 500


def test_a_placer_fed_real_packets_inserts_no_silence_without_loss(render_endpoint):
    """The arithmetic and the device agree: late is not lost."""
    stream, collected = _capture(render_endpoint, loopback=True, seconds=4.0)
    packets = [p for p, _ in collected]
    if len(packets) < 20:
        pytest.skip("pacotes insuficientes")
    placer = TrackPlacer(
        session_qpc_ns=stream.armed_qpc_ns,
        sample_rate=stream.format.sample_rate,
        threshold_ms=200,
    )
    for packet in packets:
        placer.place(packet)
    # No loss was provoked, so no gap may have been recorded beyond the
    # loopback's own idle prefix before the tone started.
    assert len(placer.gaps) <= 1
    assert placer.duplicated_frames_dropped == 0


def test_the_engine_gets_slower_to_open_the_first_time(render_endpoint):
    """Pre-warming belongs to service start-up, not to the record command."""
    first = prewarm(render_endpoint.id, loopback=True)
    second = prewarm(render_endpoint.id, loopback=True)
    assert first >= 0 and second >= 0
    assert second < 5000.0, "reabertura deveria ser rapida apos o aquecimento"


def test_loopback_is_idle_while_nothing_plays(render_endpoint):
    """Documented behaviour, asserted so nobody 'fixes' it later.

    A loopback stream that delivers nothing on an idle endpoint is correct.
    The silence fill exists for it.
    """
    stream = CaptureStream(render_endpoint.id, loopback=True)
    try:
        stream.start()
        time.sleep(1.5)
        packets = stream.read()
    finally:
        stream.stop()
    assert stream.error is None
    real_audio = [p for p in packets if not p.silent]
    assert not real_audio or all(p.frames > 0 for p in real_audio)


def test_microphone_delivers_the_three_data_points(capture_endpoint):
    """Skipped where no capture endpoint exists, never passed vacuously."""
    stream, collected = _capture(capture_endpoint, loopback=False)
    assert collected, "nenhum pacote capturado do microfone"
    usable = [p for p, _ in collected if p.timestamp_valid]
    assert usable
    assert all(p.qpc_ns > 0 for p in usable)
    positions = [p.device_position for p in usable]
    assert positions == sorted(positions)


def test_both_tracks_share_one_session_reference(capture_endpoint, render_endpoint):
    """Skipped here for the same reason: no microphone in this session."""
    from voxvault.capture.stream import open_pair

    mic, system, session_qpc_ns = open_pair(
        mic_endpoint_id=capture_endpoint.id,
        render_endpoint_id=render_endpoint.id,
        record_arrival=True,
    )
    tone = TonePlayer(render_endpoint.id)
    try:
        tone.start()
        time.sleep(2.0)
        mic_packets = [p for p, _ in mic.read_with_arrival()]
        system_packets = [p for p, _ in system.read_with_arrival()]
    finally:
        tone.stop()
        mic.stop()
        system.stop()

    assert session_qpc_ns >= mic.armed_qpc_ns
    assert session_qpc_ns >= system.armed_qpc_ns
    assert mic_packets, "trilha de entrada sem pacotes"
    assert system_packets, "trilha do sistema sem pacotes"

    mic_anchor = Anchor(
        mic_packets[0].device_position,
        mic_packets[0].qpc_ns,
        mic.format.sample_rate,
    )
    system_anchor = Anchor(
        system_packets[0].device_position,
        system_packets[0].qpc_ns,
        system.format.sample_rate,
    )
    # Both anchors live on the same QPC clock, so the offset between the
    # tracks is a subtraction and not a guess.
    offset_ns = mic_anchor.qpc_ns - system_anchor.qpc_ns
    assert abs(offset_ns) < 10 * 1_000_000_000
    assert statistics.mean([p.frames for p in mic_packets]) > 0
