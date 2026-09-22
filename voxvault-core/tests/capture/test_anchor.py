"""The alignment arithmetic, exercised with packets we build ourselves.

Each test here corresponds to a scenario in the audio-capture specification.
None of them touch a device, which is the point: the property that decides
whether the product works -- that a late packet is not mistaken for a lost one
-- is a property of this arithmetic, and it can be asserted anywhere.
"""

from __future__ import annotations

import pytest

from voxvault.capture.anchor import (
    NS_PER_SECOND,
    Anchor,
    TrackPlacer,
    decide_gap,
    delivery_lag_ns,
    residual_ns,
)

from .conftest import RATE, coherent_stream, packet

NS = NS_PER_SECOND


# --------------------------------------------------------------------------
# the anchor itself
# --------------------------------------------------------------------------


def test_anchor_maps_position_to_instant_and_back():
    anchor = Anchor(device_position=1_000, qpc_ns=5 * NS, sample_rate=RATE)
    assert anchor.offset_ns(1_000 + RATE) == NS
    assert anchor.instant_ns(1_000 + RATE) == 6 * NS
    assert anchor.position_of(6 * NS) == 1_000 + RATE


def test_residual_is_zero_while_the_timestamp_follows_the_audio():
    """A correct backend: the instant is a function of the device position."""
    packets = list(coherent_stream(count=200))
    anchor = Anchor(packets[0].device_position, packets[0].qpc_ns, RATE)
    assert all(abs(residual_ns(anchor, p)) < 1_000 for p in packets)


def test_residual_tracks_delivery_delay_when_the_timestamp_is_the_delivery_clock():
    """The PortAudio failure mode, reproduced so the discriminator is visible.

    Building the timestamp from the clock read on delivery makes the residual
    grow with scheduling delay -- the exact error the alignment exists to
    remove, dressed up as the correction for it.
    """
    delays_ns = [0, 3 * NS // 1000, 40 * NS // 1000, 250 * NS // 1000]
    packets = []
    for index, delay in enumerate(delays_ns):
        position = index * 480
        acquisition = 10 * NS + position * NS // RATE
        packets.append(
            packet(
                device_position=position,
                frames=480,
                qpc_ns=acquisition + delay,  # a delivery clock, not an acquisition one
            )
        )
    anchor = Anchor(packets[0].device_position, packets[0].qpc_ns, RATE)
    residuals = [residual_ns(anchor, p) for p in packets]
    assert residuals[-1] >= 249 * NS // 1000
    assert max(residuals) > 200 * NS // 1000


def test_delivery_lag_is_reported_separately_from_the_instant():
    p = packet(device_position=0, frames=480, qpc_ns=10 * NS)
    assert delivery_lag_ns(p, arrival_qpc_ns=10 * NS + 7_000_000) == 7_000_000


# --------------------------------------------------------------------------
# decide_gap -- the three cases
# --------------------------------------------------------------------------


def test_jitter_below_the_threshold_inserts_nothing():
    """Specification: no fill below 200 ms of divergence."""
    below = 199 * RATE // 1000
    decision = decide_gap(
        expected_position=100_000,
        packet_position=100_000 + below,
        packet_frames=480,
        sample_rate=RATE,
        threshold_ms=200,
    )
    assert decision.silence_frames == 0
    assert decision.gap is False
    assert decision.write_frames == 480


def test_divergence_above_the_threshold_is_a_real_gap():
    above = 201 * RATE // 1000
    decision = decide_gap(
        expected_position=100_000,
        packet_position=100_000 + above,
        packet_frames=480,
        sample_rate=RATE,
        threshold_ms=200,
    )
    assert decision.silence_frames == above
    assert decision.gap is True


def test_the_os_discontinuity_flag_means_a_gap_even_below_the_threshold():
    """The threshold is about jitter. A signalled loss is not jitter."""
    small = 50 * RATE // 1000
    decision = decide_gap(
        expected_position=100_000,
        packet_position=100_000 + small,
        packet_frames=480,
        sample_rate=RATE,
        threshold_ms=200,
        discontinuity=True,
    )
    assert decision.gap is True
    assert decision.silence_frames == small
    assert "descontinuidade" in decision.reason


def test_overlapping_packets_write_only_the_excess():
    decision = decide_gap(
        expected_position=100_000,
        packet_position=99_800,  # 200 frames already written
        packet_frames=480,
        sample_rate=RATE,
        threshold_ms=200,
    )
    assert decision.trim_frames == 200
    assert decision.write_frames == 280
    assert decision.silence_frames == 0


def test_a_fully_overlapping_packet_writes_nothing_and_never_rewinds():
    decision = decide_gap(
        expected_position=100_000,
        packet_position=99_000,
        packet_frames=480,
        sample_rate=RATE,
        threshold_ms=200,
    )
    assert decision.trim_frames == 480
    assert decision.write_frames == 0
    assert decision.silence_frames == 0


def test_exact_continuation_produces_no_decision_at_all():
    decision = decide_gap(
        expected_position=100_000,
        packet_position=100_000,
        packet_frames=480,
        sample_rate=RATE,
        threshold_ms=200,
    )
    assert (decision.silence_frames, decision.trim_frames, decision.gap) == (0, 0, False)


# --------------------------------------------------------------------------
# TrackPlacer -- the specification's scenarios end to end
# --------------------------------------------------------------------------


def _placer(session_qpc_ns: int = 10 * NS) -> TrackPlacer:
    return TrackPlacer(
        session_qpc_ns=session_qpc_ns, sample_rate=RATE, threshold_ms=200
    )


def test_delivery_delay_without_loss_inserts_no_silence():
    """The test that separates a correct implementation from a naive one.

    Packets arrive late and in bursts, but the device position never runs
    ahead of the frames handed over, so nothing may be inserted.
    """
    placer = _placer()
    packets = list(coherent_stream(count=500, start_qpc_ns=10 * NS))
    for p in packets:
        placement = placer.place(p)
        assert placement.silence_frames == 0, "silencio fabricado por atraso de entrega"
        assert placement.gap is False
    assert placer.written_frames == 500 * 480
    assert placer.gaps == []


def test_idle_loopback_at_session_start_is_filled_with_silence():
    """Thirty seconds of nothing played, then audio -- the normal loopback case."""
    placer = _placer(session_qpc_ns=10 * NS)
    first = packet(
        device_position=30 * RATE,
        frames=480,
        qpc_ns=40 * NS,  # thirty seconds after the session was armed
    )
    placement = placer.place(first)
    assert placement.gap is True
    assert placement.silence_frames == pytest.approx(30 * RATE, abs=2)
    assert placer.written_frames == pytest.approx(30 * RATE + 480, abs=2)
    assert len(placer.gaps) == 1
    assert placer.gaps[0][0] == 0  # the gap starts at the session start


def test_a_track_that_starts_immediately_gets_no_prefix_silence():
    placer = _placer(session_qpc_ns=10 * NS)
    first = packet(
        device_position=0, frames=480, qpc_ns=10 * NS + 20_000_000  # 20 ms later
    )
    placement = placer.place(first)
    assert placement.silence_frames == 0
    assert placer.written_frames == 480


def test_subsequent_audio_stays_aligned_after_the_idle_prefix():
    """The scenario's last clause: speech after silence lands at the same instant."""
    placer = _placer(session_qpc_ns=0)
    packets = list(
        coherent_stream(count=100, start_position=30 * RATE, start_qpc_ns=30 * NS)
    )
    for p in packets:
        placer.place(p)
    # Written position must equal the elapsed time from the session start.
    expected = 30 * RATE + 100 * 480
    assert placer.written_frames == pytest.approx(expected, abs=2)


def test_a_two_minute_idle_stretch_is_filled_and_recorded():
    placer = _placer(session_qpc_ns=0)
    for p in coherent_stream(count=10, start_position=0, start_qpc_ns=0):
        placer.place(p)
    before = placer.written_frames
    resumed_position = before + 120 * RATE
    placer.place(
        packet(
            device_position=resumed_position,
            frames=480,
            qpc_ns=resumed_position * NS // RATE,
        )
    )
    assert len(placer.gaps) == 1
    start_frame, frames, _reason = placer.gaps[0]
    assert start_frame == before
    assert frames == 120 * RATE
    assert placer.written_frames == before + 120 * RATE + 480


def test_an_invalid_timestamp_falls_back_to_the_continuous_frame_count():
    placer = _placer(session_qpc_ns=0)
    for p in coherent_stream(count=5, start_qpc_ns=0):
        placer.place(p)
    written = placer.written_frames

    placement = placer.place(
        packet(device_position=0, frames=480, qpc_ns=0, timestamp_valid=False)
    )
    assert placement.derived is True
    assert placement.silence_frames == 0
    assert placer.written_frames == written + 480
    assert placer.derived_packets == 1


def test_the_anchor_is_re_established_on_the_next_valid_packet():
    placer = _placer(session_qpc_ns=0)
    for p in coherent_stream(count=5, start_qpc_ns=0):
        placer.place(p)
    placer.place(
        packet(device_position=0, frames=480, qpc_ns=0, timestamp_valid=False)
    )
    # A valid packet arrives whose position bears no relation to the old
    # anchor. Re-anchoring must absorb that, not fabricate a gap.
    placement = placer.place(
        packet(device_position=9_000_000, frames=480, qpc_ns=123 * NS)
    )
    assert placement.reanchored is True
    assert placement.silence_frames == 0
    assert placer.reanchors == 1
    assert placer.derived_packets == 0


def test_overlapping_packets_never_move_the_write_position_backwards():
    placer = _placer(session_qpc_ns=0)
    for p in coherent_stream(count=10, start_qpc_ns=0):
        placer.place(p)
    written = placer.written_frames
    placer.place(
        packet(
            device_position=written - 200,
            frames=480,
            qpc_ns=(written - 200) * NS // RATE,
        )
    )
    assert placer.written_frames == written + 280
    assert placer.duplicated_frames_dropped == 200


def test_the_written_position_tracks_elapsed_time_across_mixed_events():
    """Gaps, jitter and overlap together; the end position is still the clock."""
    placer = _placer(session_qpc_ns=0)
    position = 0
    for _ in range(50):
        placer.place(
            packet(
                device_position=position, frames=480, qpc_ns=position * NS // RATE
            )
        )
        position += 480
    position += 3 * RATE  # a three-second hole
    for _ in range(50):
        placer.place(
            packet(
                device_position=position, frames=480, qpc_ns=position * NS // RATE
            )
        )
        position += 480
    elapsed_frames = position
    assert placer.written_frames == elapsed_frames
    assert sum(g[1] for g in placer.gaps) == 3 * RATE


def test_a_zero_or_negative_sample_rate_is_refused():
    with pytest.raises(ValueError):
        TrackPlacer(session_qpc_ns=0, sample_rate=0)
