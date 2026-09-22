"""The capture validation gate -- tasks 2.0.1 to 2.0.3.

Run it:

    python -m voxvault.capture.gate all

It answers one question: does this backend report, per packet, a device
position, a high-resolution acquisition timestamp and the OS discontinuity
flag -- and do the reported instants stay put when the machine is busy?

The decisive measurement is in :func:`run_load`. For every packet it computes
the same quantity twice:

* from what WASAPI reports -- ``qpc_ns`` against the instant implied by
  ``device_position``;
* the way PortAudio synthesises ``inputBufferAdcTime`` -- wall clock read when
  the packet arrives, minus the estimated latency.

Under CPU load the first stays flat and the second tracks the scheduling
delay. Same packets, same run, two ways of answering: that is what makes the
rejection of PortAudio a measurement rather than an assertion.
"""

from __future__ import annotations

import argparse
import array
import ctypes
import math
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field

from ..errors import CaptureError
from ..types import CapturePacket
from . import devices, wasapi
from .anchor import Anchor
from .format import SAMPLE_FLOAT32, SAMPLE_INT16, parse_wave_format
from .stream import CaptureStream
from .stream import prewarm as stream_prewarm

# --------------------------------------------------------------------------
# a tone source, so the loopback track has something to capture
# --------------------------------------------------------------------------


class TonePlayer:
    """Renders a quiet steady tone into one endpoint. Gate-only.

    Loopback delivers nothing while the endpoint is idle, which is correct but
    makes it impossible to measure anything. Rather than depend on the
    operator playing music, the gate feeds the exact endpoint it is capturing.

    Production never does this: playing audio during a recording would land on
    the system track as if it were a participant.
    """

    def __init__(
        self,
        endpoint_id: str,
        *,
        frequency: float = 441.0,
        amplitude: float = 0.02,
        buffer_ms: int = 200,
    ) -> None:
        self.endpoint_id = endpoint_id
        self.frequency = frequency
        self.amplitude = amplitude
        self.buffer_ms = buffer_ms
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: BaseException | None = None
        self.format = None

    def start(self, timeout_s: float = 5.0) -> None:
        self._thread = threading.Thread(
            target=self._run, name="voxvault-gate-tone", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout_s):
            raise RuntimeError("gerador de tom nao iniciou")
        if self.error is not None:
            raise self.error

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(3.0)
            self._thread = None

    def __enter__(self) -> "TonePlayer":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _tone_bytes(self, fmt) -> bytes:
        """One second of tone, with a whole number of cycles so it loops clean."""
        cycles = max(1, round(self.frequency))
        frames = fmt.sample_rate
        step = 2.0 * math.pi * cycles / frames
        if fmt.sample_format == SAMPLE_FLOAT32:
            buf = array.array("f", bytes(4 * frames * fmt.channels))
            for i in range(frames):
                value = self.amplitude * math.sin(step * i)
                base = i * fmt.channels
                for c in range(fmt.channels):
                    buf[base + c] = value
            return buf.tobytes()
        if fmt.sample_format == SAMPLE_INT16:
            peak = int(self.amplitude * 32767)
            buf = array.array("h", bytes(2 * frames * fmt.channels))
            for i in range(frames):
                value = int(peak * math.sin(step * i))
                base = i * fmt.channels
                for c in range(fmt.channels):
                    buf[base + c] = value
            return buf.tobytes()
        raise RuntimeError(
            f"gerador de tom nao suporta o formato {fmt.sample_format}"
        )

    def _run(self) -> None:
        client = None
        render = None
        try:
            wasapi.co_initialize()
            enumerator = wasapi.create_enumerator()
            try:
                device = enumerator.device(self.endpoint_id)
                if device is None:
                    raise RuntimeError(f"dispositivo ausente: {self.endpoint_id}")
                try:
                    client = device.activate_audio_client()
                finally:
                    device.release()
            finally:
                enumerator.release()

            raw, ptr = client.mix_format()
            try:
                fmt = parse_wave_format(raw)
                self.format = fmt
                client.initialize(
                    share_mode=wasapi.AUDCLNT_SHAREMODE_SHARED,
                    stream_flags=0,
                    buffer_duration_hns=self.buffer_ms * 10_000,
                    periodicity_hns=0,
                    format_ptr=ptr,
                )
            finally:
                wasapi._ole32.CoTaskMemFree(ptr)

            render = client.render_client()
            total_frames = client.buffer_size()
            tone = self._tone_bytes(fmt)
            block = fmt.block_align
            tone_frames = len(tone) // block
            offset = 0

            def fill(frames: int) -> None:
                nonlocal offset
                dest = render.get_buffer(frames)
                written = 0
                while written < frames:
                    chunk = min(frames - written, tone_frames - offset)
                    start = offset * block
                    ctypes.memmove(
                        ctypes.c_void_p(dest.value + written * block),
                        tone[start : start + chunk * block],
                        chunk * block,
                    )
                    written += chunk
                    offset = (offset + chunk) % tone_frames
                render.release_buffer(frames, 0)

            fill(total_frames)
            client.start()
            self._ready.set()

            sleep = max(0.005, self.buffer_ms / 4000.0)
            while not self._stop.wait(sleep):
                available = total_frames - client.current_padding()
                if available:
                    fill(available)
            client.stop()
        except BaseException as exc:  # reported through start()
            self.error = exc
            self._ready.set()
        finally:
            if render is not None:
                render.release()
            if client is not None:
                client.release()
            wasapi.co_uninitialize()


# --------------------------------------------------------------------------
# CPU load
# --------------------------------------------------------------------------


class CpuLoad:
    """Saturate the machine so delivery is late.

    Threads are the right tool even with the GIL: this must delay the *Python*
    consumer, and GIL contention is the most direct way to do it. Extra
    processes pile real scheduler pressure on top of that.
    """

    def __init__(self, threads: int = 0, processes: int = 0) -> None:
        import os

        cpus = os.cpu_count() or 4
        self.threads = threads or max(4, cpus)
        self.processes = processes if processes >= 0 else 0
        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []
        self._procs: list = []

    def _spin(self) -> None:
        x = 1.000001
        while not self._stop.is_set():
            for _ in range(200_000):
                x = x * 1.0000001 % 997.0
            if self._stop.is_set():
                break

    def start(self) -> None:
        import subprocess

        self._stop.clear()
        for i in range(self.threads):
            t = threading.Thread(target=self._spin, name=f"gate-load-{i}", daemon=True)
            t.start()
            self._workers.append(t)
        for _ in range(self.processes):
            self._procs.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        "x=1.0\nwhile True: x=x*1.0000001%997.0",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            )

    def stop(self) -> None:
        self._stop.set()
        for t in self._workers:
            t.join(5.0)
        self._workers.clear()
        for p in self._procs:
            try:
                p.kill()
            except Exception:
                pass
        self._procs.clear()

    def __enter__(self) -> "CpuLoad":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------


@dataclass
class PhaseStats:
    """One measurement phase: idle or loaded."""

    label: str
    packets: int = 0
    frames: int = 0
    #: Drift-immune discriminator. Between consecutive packets, the reported
    #: instant must advance by exactly what the device position advanced by.
    #: A real capture gap does not inflate it -- both quantities grow together
    #: -- so what is left is the timestamp's dependence on delivery.
    step_us: list[float] = field(default_factory=list)
    #: The same step, computed the way PortAudio synthesises its timestamp:
    #: the clock read when the packet arrives, plus an estimated latency.
    portaudio_step_us: list[float] = field(default_factory=list)
    #: qpc_ns - instant implied by device_position, anchored on the first
    #: packet. Includes the slow drift between the audio clock and QPC.
    residual_us: list[float] = field(default_factory=list)
    #: arrival - qpc_ns. Negative on loopback, where the stamp is the instant
    #: the mix is presented, which is ahead of the moment it is handed over.
    delivery_us: list[float] = field(default_factory=list)
    discontinuities: int = 0
    invalid_timestamps: int = 0

    @staticmethod
    def _span(values: list[float]) -> float:
        return (max(values) - min(values)) if values else 0.0

    @staticmethod
    def _absmax(values: list[float]) -> float:
        return max((abs(v) for v in values), default=0.0)

    @property
    def step_absmax_us(self) -> float:
        return self._absmax(self.step_us)

    @property
    def portaudio_step_absmax_us(self) -> float:
        return self._absmax(self.portaudio_step_us)

    @property
    def residual_span_us(self) -> float:
        return self._span(self.residual_us)

    @property
    def delivery_span_us(self) -> float:
        return self._span(self.delivery_us)

    @property
    def delivery_max_us(self) -> float:
        return max(self.delivery_us) if self.delivery_us else 0.0

    @property
    def delivery_median_us(self) -> float:
        return statistics.median(self.delivery_us) if self.delivery_us else 0.0

    def line(self) -> str:
        return (
            f"  {self.label:<10} pacotes={self.packets:<5d} quadros={self.frames:<9d} "
            f"descont={self.discontinuities}\n"
            f"    passo WASAPI     (dQPC - dPosicao)   max|.|={self.step_absmax_us:10.1f} us  "
            f"desvio={statistics.pstdev(self.step_us) if len(self.step_us) > 1 else 0.0:8.1f} us\n"
            f"    passo PortAudio  (dEntrega - dPos.)  max|.|="
            f"{self.portaudio_step_absmax_us:10.1f} us  "
            f"desvio={statistics.pstdev(self.portaudio_step_us) if len(self.portaudio_step_us) > 1 else 0.0:8.1f} us\n"
            f"    residuo ancorado                     amplitude={self.residual_span_us:10.1f} us\n"
            f"    entrega - carimbo                    mediana={self.delivery_median_us:10.1f} us  "
            f"amplitude={self.delivery_span_us:8.1f} us"
        )


class TrackMeasurement:
    """Accumulates the per-packet comparison for one track."""

    def __init__(self, name: str, latency_ns: int) -> None:
        self.name = name
        # What PortAudio adds to the delivery clock to build
        # inputBufferAdcTime. A negative report means unknown, in which case
        # the synthesised instant is just the arrival clock.
        self.latency_ns = max(latency_ns, 0)
        self.anchor: Anchor | None = None
        self.first_packet_discontinuity = False
        self._seen_any = False
        self._prev_position: int | None = None
        self._prev_qpc: int = 0
        self._prev_arrival: int = 0

    def observe(
        self, packet: CapturePacket, arrival_ns: int, sample_rate: int, phase: PhaseStats
    ) -> None:
        if not self._seen_any:
            self._seen_any = True
            self.first_packet_discontinuity = packet.discontinuity
        if not packet.timestamp_valid:
            phase.invalid_timestamps += 1
            return
        if self.anchor is None:
            self.anchor = Anchor(packet.device_position, packet.qpc_ns, sample_rate)

        phase.packets += 1
        phase.frames += packet.frames
        if packet.discontinuity:
            phase.discontinuities += 1
        phase.residual_us.append(
            (packet.qpc_ns - self.anchor.instant_ns(packet.device_position)) / 1000.0
        )
        phase.delivery_us.append((arrival_ns - packet.qpc_ns) / 1000.0)

        if self._prev_position is not None:
            advance = packet.device_position - self._prev_position
            implied_ns = advance * 1_000_000_000 // sample_rate
            phase.step_us.append(
                ((packet.qpc_ns - self._prev_qpc) - implied_ns) / 1000.0
            )
            phase.portaudio_step_us.append(
                ((arrival_ns - self._prev_arrival) - implied_ns) / 1000.0
            )
        self._prev_position = packet.device_position
        self._prev_qpc = packet.qpc_ns
        self._prev_arrival = arrival_ns


# --------------------------------------------------------------------------
# endpoint selection
# --------------------------------------------------------------------------


@dataclass
class Selection:
    mic: devices.AudioEndpoint | None
    render: devices.AudioEndpoint | None
    note: str = ""


def select_endpoints(role: str = devices.ROLE_COMMUNICATIONS) -> Selection:
    mic = devices.default_endpoint(devices.FLOW_CAPTURE, role)
    render = devices.default_endpoint(devices.FLOW_RENDER, role)
    notes = []
    if mic is None:
        available = devices.list_endpoints(devices.FLOW_CAPTURE)
        notes.append(
            "Nenhum dispositivo de ENTRADA disponivel nesta sessao "
            f"(enumerados: {len(available)})."
        )
    if render is None:
        notes.append("Nenhum dispositivo de SAIDA disponivel para o papel.")
    return Selection(mic=mic, render=render, note=" ".join(notes))


def _guard(step, *args, **kwargs) -> bool:
    """Run one gate step, reporting a failure instead of unwinding.

    An endpoint can be enumerable and still unusable -- a Remote Desktop
    endpoint whose audio channel has dropped answers ``GetMixFormat`` with
    ``REGDB_E_CLASSNOTREG``. That is a result the report should carry, not a
    traceback that stops the remaining steps from running.
    """
    try:
        return bool(step(*args, **kwargs))
    except (CaptureError, OSError) as exc:
        print(f"\n  FALHA: {exc}")
        return False


def _print_header(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


# --------------------------------------------------------------------------
# 2.0.1 -- the three data points arrive populated
# --------------------------------------------------------------------------


def run_packets(seconds: float = 6.0, role: str = devices.ROLE_COMMUNICATIONS) -> bool:
    _print_header("2.0.1  Posicao de dispositivo, timestamp e flags por pacote")
    selection = select_endpoints(role)
    if selection.render is None:
        print("FALHA: " + selection.note)
        return False

    streams: list[CaptureStream] = []
    if selection.mic is not None:
        print(f"entrada : {selection.mic.name}\n          {selection.mic.id}")
        streams.append(
            CaptureStream(
                selection.mic.id, loopback=False, name="mic", record_arrival=True
            )
        )
    else:
        print("entrada : INDISPONIVEL -- " + selection.note)
    print(f"saida   : {selection.render.name}\n          {selection.render.id}")
    streams.append(
        CaptureStream(
            selection.render.id, loopback=True, name="loopback", record_arrival=True
        )
    )

    ok = True
    tone = TonePlayer(selection.render.id)
    try:
        for stream in streams:
            stream.start(timeout_s=5.0)
            print(
                f"\n[{stream.name}] armado em QPC {stream.armed_qpc_ns} ns | "
                f"{stream.format.describe()} | buffer {stream.buffer_frames} quadros | "
                f"periodo {stream.device_period_ns/1e6:.1f} ms | "
                f"latencia {stream.latency_ns/1e6:.1f} ms | "
                f"{'evento' if stream.event_driven else 'polling'} | "
                f"prioridade: {stream.priority_status}"
            )
        tone.start()
        print(f"\ntom de teste em {tone.frequency:.0f} Hz no dispositivo de saida\n")

        deadline = time.monotonic() + seconds
        shown: dict[str, int] = {s.name: 0 for s in streams}
        while time.monotonic() < deadline:
            time.sleep(0.05)
            for stream in streams:
                for packet, arrival in stream.read_with_arrival():
                    if shown[stream.name] < 8:
                        shown[stream.name] += 1
                        print(
                            f"  [{stream.name:<8}] devpos={packet.device_position:<12d} "
                            f"qpc_ns={packet.qpc_ns:<16d} flags="
                            f"{'D' if packet.discontinuity else '-'}"
                            f"{'S' if packet.silent else '-'}"
                            f"{'!' if not packet.timestamp_valid else '-'} "
                            f"quadros={packet.frames:<6d} bytes={len(packet.data):<7d} "
                            f"entrega={(arrival - packet.qpc_ns)/1e6:6.2f} ms"
                        )
    finally:
        tone.stop()
        for stream in streams:
            stream.stop()

    print()
    for stream in streams:
        stats = stream.stats()
        populated = stats.packets > 0 and stats.invalid_timestamps == 0
        print(
            f"  [{stream.name}] pacotes={stats.packets} quadros={stats.frames} "
            f"descontinuidades={stats.discontinuities} "
            f"timestamps invalidos={stats.invalid_timestamps} "
            f"erro={stream.error!r}"
        )
        if stream.error is not None:
            ok = False
        if not populated:
            print(f"      -> nenhum pacote utilizavel em '{stream.name}'")
            ok = False
    if selection.mic is None:
        print(
            "\n  AVISO: a metade de entrada nao pode ser exercitada nesta sessao.\n"
            "         " + selection.note
        )
    print("\n2.0.1: " + ("APROVADO" if ok else "REPROVADO"))
    return ok


# --------------------------------------------------------------------------
# 2.0.2 -- the reported instants do not move with delivery delay
# --------------------------------------------------------------------------


def run_load(
    seconds: float = 8.0,
    role: str = devices.ROLE_COMMUNICATIONS,
    threads: int = 0,
    processes: int = 2,
) -> bool:
    _print_header(
        "2.0.2  Instantes reportados sob carga de processador"
    )
    selection = select_endpoints(role)
    if selection.render is None:
        print("FALHA: " + selection.note)
        return False

    streams: list[CaptureStream] = []
    if selection.mic is not None:
        streams.append(
            CaptureStream(
                selection.mic.id, loopback=False, name="mic", record_arrival=True
            )
        )
    streams.append(
        CaptureStream(
            selection.render.id, loopback=True, name="loopback", record_arrival=True
        )
    )

    tone = TonePlayer(selection.render.id)
    measurements: dict[str, TrackMeasurement] = {}
    phases: dict[str, dict[str, PhaseStats]] = {}
    try:
        for stream in streams:
            stream.start(timeout_s=5.0)
            measurements[stream.name] = TrackMeasurement(
                stream.name, stream.latency_ns
            )
            phases[stream.name] = {
                "ocioso": PhaseStats("ocioso"),
                "sob carga": PhaseStats("sob carga"),
            }
        tone.start()
        time.sleep(0.4)  # let the tone reach the loopback ring
        for stream in streams:
            stream.read_with_arrival()

        def collect(phase_name: str, duration: float) -> None:
            end = time.monotonic() + duration
            while time.monotonic() < end:
                time.sleep(0.02)
                for stream in streams:
                    phase = phases[stream.name][phase_name]
                    measurement = measurements[stream.name]
                    rate = stream.format.sample_rate
                    for packet, arrival in stream.read_with_arrival():
                        measurement.observe(packet, arrival, rate, phase)

        collect("ocioso", seconds / 2)
        load = CpuLoad(threads=threads, processes=processes)
        load.start()
        try:
            collect("sob carga", seconds / 2)
        finally:
            load.stop()
        print(
            f"carga aplicada: {load.threads} threads + {load.processes} processos"
        )
    finally:
        tone.stop()
        for stream in streams:
            stream.stop()

    ok = True
    for stream in streams:
        idle = phases[stream.name]["ocioso"]
        loaded = phases[stream.name]["sob carga"]
        print(f"\n[{stream.name}]  {stream.format.describe()}")
        print(idle.line())
        print(loaded.line())

        if loaded.packets < 5 or idle.packets < 5:
            print("    -> pacotes insuficientes para concluir")
            ok = False
            continue

        # The reported instant must not move with delivery delay. Allowance:
        # three times the idle figure, floor of 2 ms -- two device periods,
        # and two orders of magnitude below the 200 ms fill threshold, so a
        # deviation this size can never fabricate silence.
        allowance = max(3.0 * idle.step_absmax_us, 2000.0)
        stable = loaded.step_absmax_us <= allowance
        # If the load did not actually delay delivery, the test proved nothing.
        delayed = (
            loaded.delivery_max_us > idle.delivery_max_us + 2000.0
            or loaded.delivery_span_us > idle.delivery_span_us * 2.0
            or loaded.portaudio_step_absmax_us
            > idle.portaudio_step_absmax_us + 2000.0
        )
        portaudio_moved = (
            loaded.portaudio_step_absmax_us > loaded.step_absmax_us * 3.0
        )

        print(
            f"    instantes WASAPI estaveis sob carga .............. "
            f"{'sim' if stable else 'NAO'} "
            f"({loaded.step_absmax_us:.1f} us <= {allowance:.1f} us)"
        )
        print(
            f"    a carga realmente atrasou a entrega .............. "
            f"{'sim' if delayed else 'nao'} "
            f"(entrega max {idle.delivery_max_us:.0f} -> "
            f"{loaded.delivery_max_us:.0f} us)"
        )
        print(
            f"    o instante estilo PortAudio acompanha o atraso ... "
            f"{'sim' if portaudio_moved else 'nao'} "
            f"({loaded.portaudio_step_absmax_us:.1f} us vs "
            f"{loaded.step_absmax_us:.1f} us -- fator "
            f"{loaded.portaudio_step_absmax_us / max(loaded.step_absmax_us, 1e-9):.0f}x)"
        )
        if not stable:
            ok = False
        if not delayed:
            print(
                "    AVISO: sem atraso de entrega mensuravel, o teste nao "
                "discrimina os dois backends."
            )
            ok = False

    if selection.mic is None:
        print("\n  AVISO: " + selection.note)
    print("\n2.0.2: " + ("APROVADO" if ok else "REPROVADO"))
    return ok


# --------------------------------------------------------------------------
# 2.0.3 -- the OS discontinuity flag is received
# --------------------------------------------------------------------------


@dataclass
class LossObservation:
    """One provoked capture loss, seen through both channels."""

    stall_s: float
    packets: int
    flagged: int
    jump_ms: list[int]
    flags_seen: set[str]

    @property
    def lost_ms(self) -> int:
        return max(self.jump_ms) if self.jump_ms else 0


def _provoke_loss(
    endpoint: devices.AudioEndpoint, stall_s: float, buffer_ms: int = 200
) -> LossObservation:
    """Stop draining for longer than the engine buffer, then look at the seam."""
    stream = CaptureStream(
        endpoint.id, loopback=True, name="loopback", buffer_ms=buffer_ms
    )
    stream.debug_stall_after_s = 1.5
    stream.debug_stall_s = stall_s
    tone = TonePlayer(endpoint.id)
    packets: list[CapturePacket] = []
    try:
        stream.start()
        tone.start()
        deadline = time.monotonic() + 2.0 + stall_s + 1.5
        while time.monotonic() < deadline:
            time.sleep(0.05)
            packets.extend(stream.read())
    finally:
        tone.stop()
        stream.stop()

    rate = stream.format.sample_rate
    flags_seen: set[str] = set()
    jumps: list[int] = []
    previous = None
    for packet in packets:
        if packet.discontinuity:
            flags_seen.add("DATA_DISCONTINUITY")
        if packet.silent:
            flags_seen.add("SILENT")
        if not packet.timestamp_valid:
            flags_seen.add("TIMESTAMP_ERROR")
        if previous is not None:
            missing = packet.device_position - (
                previous.device_position + previous.frames
            )
            if missing > rate // 100:  # more than 10 ms unaccounted for
                jumps.append(missing * 1000 // rate)
        previous = packet
    return LossObservation(
        stall_s=stall_s,
        packets=len(packets),
        flagged=sum(1 for p in packets if p.discontinuity),
        jump_ms=jumps,
        flags_seen=flags_seen,
    )


def run_discontinuity(
    role: str = devices.ROLE_COMMUNICATIONS, stall_s: float = 2.0
) -> bool:
    _print_header("2.0.3  Sinalizacao de descontinuidade do sistema operacional")
    selection = select_endpoints(role)
    if selection.render is None:
        print("FALHA: " + selection.note)
        return False

    print(
        "Metodo: parar de drenar por mais tempo que o buffer do motor, para que\n"
        "o driver sobrescreva quadros nunca lidos. A perda e entao observada por\n"
        "dois canais independentes: a flag do sistema operacional e o salto da\n"
        "posicao de dispositivo. Varias provocacoes, porque a flag nao e\n"
        "emitida de forma deterministica para uma mesma perda.\n"
    )

    observations = [
        _provoke_loss(selection.render, stall)
        for stall in (stall_s, max(1.0, stall_s / 2), 0.6)
    ]
    for observation in observations:
        print(
            f"  pausa {observation.stall_s:.1f} s -> pacotes={observation.packets} "
            f"sinalizados={observation.flagged} perda={observation.lost_ms} ms "
            f"flags vistas={sorted(observation.flags_seen) or ['nenhuma']}"
        )

    flag_received = any(o.flagged for o in observations)
    with_loss = [o for o in observations if o.lost_ms > 0]
    position_always = len(with_loss) == len(observations)
    unflagged = [o for o in with_loss if o.flagged == 0]

    print()
    print(
        f"    flag DATA_DISCONTINUITY recebida ................. "
        f"{'sim' if flag_received else 'NAO'} "
        f"({sum(o.flagged for o in observations)} pacotes em "
        f"{sum(1 for o in observations if o.flagged)}/{len(observations)} provocacoes)"
    )
    print(
        f"    perda visivel na posicao de dispositivo .......... "
        f"{'sim' if position_always else 'NAO'} "
        f"({len(with_loss)}/{len(observations)} provocacoes)"
    )
    if unflagged:
        print(
            f"    NOTA: {len(unflagged)} perda(s) real(is) de ate "
            f"{max(o.lost_ms for o in unflagged)} ms nao foram sinalizadas.\n"
            "          A flag e necessaria mas nao suficiente neste endpoint; a\n"
            "          regra de lacuna precisa tambem do salto de posicao, como\n"
            "          a especificacao ja exige ao ligar as duas condicoes por\n"
            "          'ou'. Depender so da flag perderia lacunas reais."
        )

    ok = flag_received and position_always
    if not ok:
        print(
            "\n  perda provocada insuficiente; aumente --stall ou reduza o buffer"
        )
    print("\n2.0.3: " + ("APROVADO" if ok else "REPROVADO"))
    return ok


# --------------------------------------------------------------------------
# supporting modes
# --------------------------------------------------------------------------


def run_list() -> bool:
    _print_header("Dispositivos de audio")
    for flow in (devices.FLOW_CAPTURE, devices.FLOW_RENDER):
        endpoints = devices.list_endpoints(flow)
        print(f"\n{flow.upper()} ({len(endpoints)}):")
        if not endpoints:
            print("  (nenhum)")
        for endpoint in endpoints:
            print("  " + endpoint.describe())
    print("\npadroes por papel:")
    for flow in (devices.FLOW_CAPTURE, devices.FLOW_RENDER):
        for role in devices.ALL_ROLES:
            endpoint = devices.default_endpoint(flow, role)
            print(f"  {flow:<8} {role:<14} {endpoint.name if endpoint else '(nenhum)'}")
    return True


def run_probe(seconds: float = 4.0, role: str = devices.ROLE_COMMUNICATIONS) -> bool:
    """Measure event-driven against polling, for both kinds of endpoint."""
    _print_header("Comparacao: dirigido por evento x polling")
    selection = select_endpoints(role)
    combos: list[tuple[str, devices.AudioEndpoint, bool, bool]] = []
    if selection.mic is not None:
        combos.append(("entrada/evento", selection.mic, False, True))
        combos.append(("entrada/polling", selection.mic, False, False))
    if selection.render is not None:
        combos.append(("loopback/evento", selection.render, True, True))
        combos.append(("loopback/polling", selection.render, True, False))
    if not combos:
        print("FALHA: nenhum dispositivo disponivel")
        return False

    for label, endpoint, loopback, event_driven in combos:
        stream = CaptureStream(
            endpoint.id,
            loopback=loopback,
            name=label,
            event_driven=event_driven,
            record_arrival=True,
        )
        tone = TonePlayer(endpoint.id) if loopback else None
        lags: list[float] = []
        try:
            stream.start(timeout_s=5.0)
            if tone is not None:
                tone.start()
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                time.sleep(0.02)
                for packet, arrival in stream.read_with_arrival():
                    lags.append((arrival - packet.qpc_ns) / 1e6)
            stats = stream.stats()
            median = statistics.median(lags) if lags else float("nan")
            worst = max(lags) if lags else float("nan")
            print(
                f"  {label:<18} pacotes={stats.packets:<5d} quadros={stats.frames:<9d} "
                f"descont={stats.discontinuities:<3d} "
                f"entrega mediana={median:6.2f} ms max={worst:6.2f} ms "
                f"erro={stream.error!r}"
            )
        except Exception as exc:
            print(f"  {label:<18} ERRO: {exc}")
        finally:
            if tone is not None:
                tone.stop()
            stream.stop()
    return True


def run_prewarm(role: str = devices.ROLE_COMMUNICATIONS) -> bool:
    """Pay the first-Initialize cost once, and report it.

    Worth its own step because the number is a design input: if it is seconds,
    the resident service has to do this at startup rather than when the user
    presses record.
    """
    _print_header("Aquecimento do motor de audio")
    selection = select_endpoints(role)
    for label, endpoint, loopback in (
        ("entrada", selection.mic, False),
        ("saida", selection.render, True),
    ):
        if endpoint is None:
            print(f"  {label:<8} (nenhum dispositivo)")
            continue
        first = stream_prewarm(endpoint.id, loopback=loopback)
        second = stream_prewarm(endpoint.id, loopback=loopback)
        print(
            f"  {label:<8} {endpoint.name}\n"
            f"           primeira abertura={first:9.1f} ms  "
            f"segunda={second:9.1f} ms"
        )
    return True


def run_all(args: argparse.Namespace) -> bool:
    _guard(run_list)
    _guard(run_prewarm, role=args.role)
    _guard(run_probe, seconds=args.seconds / 2, role=args.role)
    results = {
        "2.0.1": _guard(run_packets, seconds=args.seconds, role=args.role),
        "2.0.2": _guard(
            run_load,
            seconds=args.seconds * 2,
            role=args.role,
            threads=args.threads,
            processes=args.processes,
        ),
        "2.0.3": _guard(run_discontinuity, role=args.role, stall_s=args.stall),
    }
    _print_header("Resultado do portao de captura")
    for name, passed in results.items():
        print(f"  {name}: {'APROVADO' if passed else 'REPROVADO'}")
    every = all(results.values())
    print("\nPORTAO: " + ("APROVADO" if every else "REPROVADO"))
    return every


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="voxvault.capture.gate",
        description="Portao de validacao do backend de captura (tarefas 2.0.1-2.0.3)",
    )
    parser.add_argument(
        "mode",
        choices=[
            "all",
            "list",
            "prewarm",
            "probe",
            "packets",
            "load",
            "discontinuity",
        ],
        nargs="?",
        default="all",
    )
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--stall", type=float, default=2.0)
    parser.add_argument("--processes", type=int, default=2)
    parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="threads de carga (0 = um por CPU). Poucas threads atrasam a "
        "entrega sem provocar estouro do buffer.",
    )
    parser.add_argument(
        "--role",
        choices=list(devices.ALL_ROLES),
        default=devices.ROLE_COMMUNICATIONS,
    )
    args = parser.parse_args(argv)

    wasapi.co_initialize()
    if args.mode == "list":
        ok = _guard(run_list)
    elif args.mode == "prewarm":
        ok = _guard(run_prewarm, role=args.role)
    elif args.mode == "probe":
        ok = _guard(run_probe, seconds=args.seconds, role=args.role)
    elif args.mode == "packets":
        ok = _guard(run_packets, seconds=args.seconds, role=args.role)
    elif args.mode == "load":
        ok = _guard(
            run_load,
            seconds=args.seconds,
            role=args.role,
            threads=args.threads,
            processes=args.processes,
        )
    elif args.mode == "discontinuity":
        ok = _guard(run_discontinuity, role=args.role, stall_s=args.stall)
    else:
        ok = run_all(args)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
