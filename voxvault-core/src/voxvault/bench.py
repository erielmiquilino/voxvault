"""Side-by-side comparison of transcription configurations.

The instrument behind the phase-0 decision gate: run several models over the
very same audio and lay the results out so a person can read them and judge
whether local transcription is good enough.

Two design points are not incidental.

Each configuration runs in its **own subprocess**. The specification forbids
holding two models in GPU memory at once, and `del model` plus a garbage
collection does not reliably return CUDA memory in CTranslate2. Process exit
does, always. It also means one configuration crashing cannot take the rest of
the run down with it.

Results are aligned by **time window**, never by segment index. Different
models cut the audio into different numbers of segments, so pairing the third
segment of one with the third of another would compare unrelated speech.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config
from .types import Segment

#: Width of a comparison row in the report body.
WINDOW_SECONDS = 15


@dataclass(slots=True)
class RunResult:
    label: str
    model: str
    ok: bool = False
    error: str = ""
    engine_id: str = ""
    load_seconds: float = 0.0
    decode_seconds: float = 0.0
    audio_seconds: float = 0.0
    peak_gpu_mb: int | None = None
    device: str = ""
    segments: list[Segment] = field(default_factory=list)
    raw_path: Path | None = None

    @property
    def total_seconds(self) -> float:
        return self.load_seconds + self.decode_seconds

    @property
    def realtime_factor(self) -> float:
        """How many seconds of audio per second of decoding.

        Load time is excluded on purpose: it is a fixed cost paid once by the
        resident service, not a cost per hour of meeting. It is reported
        separately so the two are never conflated.
        """
        if self.decode_seconds <= 0:
            return 0.0
        return self.audio_seconds / self.decode_seconds


@dataclass(slots=True)
class BenchReport:
    audio_path: Path
    language: str
    vocabulary: str
    started_at: datetime
    output_dir: Path
    runs: list[RunResult] = field(default_factory=list)

    @property
    def any_failed(self) -> bool:
        return any(not run.ok for run in self.runs)


# -- GPU sampling ------------------------------------------------------


def _total_gpu_used_mb() -> int | None:
    import shutil

    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        first = out.stdout.strip().splitlines()[0]
        return int(float(first.strip()))
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


class _GpuSampler(threading.Thread):
    """Track how much GPU memory a configuration takes while it runs.

    Per-process accounting would be ideal, but `nvidia-smi
    --query-compute-apps` reports `[N/A]` for used memory on GeForce cards
    under Windows' WDDM driver model -- verified on this machine. So the
    measurement is a device-wide delta against a baseline taken just before
    the child starts.

    That delta is attributable precisely because the benchmark never holds two
    models at once: runs are serialized in separate processes, so during one
    run the only new allocation of any size is that run's. Other desktop
    activity can still perturb it, which is why the report says what is being
    measured instead of presenting the number as exact.
    """

    def __init__(self, interval: float = 0.2) -> None:
        super().__init__(daemon=True)
        self.interval = interval
        self.baseline_mb = _total_gpu_used_mb()
        self.peak_total_mb = self.baseline_mb or 0
        # Not named `_stop`: threading.Thread already owns that attribute
        # internally, and shadowing it breaks join().
        self._halt = threading.Event()

    @property
    def delta_mb(self) -> int | None:
        if self.baseline_mb is None:
            return None
        delta = self.peak_total_mb - self.baseline_mb
        return delta if delta > 0 else None

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        if self.baseline_mb is None:
            return
        while not self._halt.is_set():
            current = _total_gpu_used_mb()
            if current is not None:
                self.peak_total_mb = max(self.peak_total_mb, current)
            self._halt.wait(self.interval)


# -- running -----------------------------------------------------------

def run_benchmark(
    config: Config,
    audio_path: Path,
    models: list[str],
    *,
    language: str = "pt",
    vocabulary: str = "",
    labels: list[str] | None = None,
) -> BenchReport:
    """Run every configuration over identical input and collect the results."""
    started = datetime.now()
    stamp = started.strftime("%Y%m%d-%H%M%S")
    # Never overwrite a previous benchmark: the run identifier carries both
    # the instant and the input file name.
    output_dir = config.data_dir / "bench" / f"{stamp}-{audio_path.stem}"
    output_dir.mkdir(parents=True, exist_ok=True)

    report = BenchReport(
        audio_path=audio_path,
        language=language,
        vocabulary=vocabulary,
        started_at=started,
        output_dir=output_dir,
    )

    for index, model in enumerate(models):
        label = labels[index] if labels and index < len(labels) else model
        report.runs.append(
            _run_one(config, audio_path, model, label, language, vocabulary, output_dir)
        )

    (output_dir / "relatorio.md").write_text(
        format_report(report), encoding="utf-8"
    )
    return report


def _run_one(
    config: Config,
    audio_path: Path,
    model: str,
    label: str,
    language: str,
    vocabulary: str,
    output_dir: Path,
) -> RunResult:
    result = RunResult(label=label, model=model)

    request = {
        "audio_path": str(audio_path),
        "model": model,
        "language": language,
        "vocabulary": vocabulary,
        "data_dir": str(config.data_dir),
        "allow_cpu_fallback": config.allow_cpu_fallback,
    }

    # The child emits accented Portuguese. Without this its stdout defaults to
    # the console code page, the parent's UTF-8 reader thread dies on the first
    # cedilla, and communicate() hands back None instead of the result.
    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

    child = subprocess.Popen(
        [sys.executable, "-m", "voxvault.bench", "--worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=child_env,
    )
    sampler = _GpuSampler()
    sampler.start()
    try:
        stdout, stderr = child.communicate(json.dumps(request), timeout=3600)
    except subprocess.TimeoutExpired:
        child.kill()
        stdout, stderr = child.communicate()
        result.error = "a execucao excedeu o tempo limite de uma hora"
        return result
    finally:
        sampler.stop()
        sampler.join(timeout=2)

    result.peak_gpu_mb = sampler.delta_mb

    payload = _last_json_object(stdout)
    if payload is None:
        tail = (stderr or "").strip().splitlines()
        result.error = tail[-1] if tail else "o processo nao devolveu resultado"
        return result

    if not payload.get("ok"):
        # A failing configuration must not stop the others; record why.
        result.error = payload.get("error", "falha sem motivo informado")
        return result

    result.ok = True
    result.engine_id = payload["engine_id"]
    result.device = payload.get("device", "")
    result.load_seconds = payload["load_seconds"]
    result.decode_seconds = payload["decode_seconds"]
    result.audio_seconds = payload["audio_seconds"]
    result.segments = [
        Segment(s["start_ms"], s["end_ms"], s["text"]) for s in payload["segments"]
    ]
    if result.device != "cuda":
        result.peak_gpu_mb = None  # not applicable, rather than zero

    raw_path = output_dir / f"{_slug(label)}.json"
    raw_path.write_text(
        json.dumps(
            {
                "rotulo": label,
                "motor": result.engine_id,
                "dispositivo": result.device,
                "arquivo": str(audio_path),
                "idioma": language,
                "vocabulario": vocabulary,
                "segundos_carregamento": round(result.load_seconds, 3),
                "segundos_decodificacao": round(result.decode_seconds, 3),
                "segundos_audio": round(result.audio_seconds, 3),
                "fator_tempo_real": round(result.realtime_factor, 2),
                "pico_memoria_gpu_mb": result.peak_gpu_mb,
                "segmentos": [
                    {"inicio_ms": s.start_ms, "fim_ms": s.end_ms, "texto": s.text}
                    for s in result.segments
                ],
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    result.raw_path = raw_path
    return result


def _last_json_object(text: str) -> dict | None:
    """Pick the result line out of a child's stdout.

    Libraries in the child print warnings to stdout without asking, so the
    protocol is "the last line that parses as a JSON object wins".
    """
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _slug(text: str) -> str:
    keep = [c if c.isalnum() or c in "-_" else "-" for c in text]
    return "".join(keep).strip("-").lower() or "execucao"


# -- report ------------------------------------------------------------

def _stamp(ms: int) -> str:
    seconds, _ = divmod(int(ms), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _windows(runs: list[RunResult], width_s: int) -> list[tuple[int, dict[str, str]]]:
    """Group every run's text by the time window it falls in.

    Grouping by window rather than by segment index is what makes the columns
    comparable at all: no text is dropped for lacking a counterpart, and a
    model that emits one long segment lines up with one that emits four short
    ones.
    """
    width_ms = width_s * 1000
    buckets: dict[int, dict[str, list[str]]] = {}
    for run in runs:
        if not run.ok:
            continue
        for segment in run.segments:
            index = segment.start_ms // width_ms
            buckets.setdefault(index, {}).setdefault(run.label, []).append(segment.text)
    return [
        (index * width_ms, {label: " ".join(parts) for label, parts in by_label.items()})
        for index, by_label in sorted(buckets.items())
    ]


def format_report(report: BenchReport) -> str:
    successful = [r for r in report.runs if r.ok]
    lines: list[str] = [
        "# Comparação de transcrição",
        "",
        f"- **Áudio:** `{report.audio_path}`",
        f"- **Idioma:** {report.language}",
        f"- **Vocabulário:** {report.vocabulary or '(nenhum)'}",
        f"- **Execução:** {report.started_at:%d/%m/%Y %H:%M:%S}",
        f"- **Resultados brutos:** `{report.output_dir}`",
        "",
        "Todas as configurações receberam exatamente a mesma entrada: mesmo "
        "arquivo, mesmo idioma e mesmo vocabulário, sem pré-processamento "
        "diferente entre elas.",
        "",
        "## Desempenho",
        "",
        "| Configuração | Dispositivo | Carregamento | Decodificação | Fator tempo real | Pico GPU | Segmentos |",
        "|---|---|---|---|---|---|---|",
    ]

    for run in report.runs:
        if not run.ok:
            lines.append(
                f"| {run.label} | — | — | — | — | — | **falhou** |"
            )
            continue
        peak = f"{run.peak_gpu_mb} MB" if run.peak_gpu_mb is not None else "n/a"
        lines.append(
            f"| {run.label} | {run.device} | {run.load_seconds:.1f} s | "
            f"{run.decode_seconds:.1f} s | {run.realtime_factor:.1f}x | "
            f"{peak} | {len(run.segments)} |"
        )
    lines.append("")
    lines.append(
        "O tempo de carregamento é um custo fixo, pago uma vez pelo serviço "
        "residente; o fator de tempo real usa apenas a decodificação, que é o "
        "custo que cresce com a duração da reunião."
    )
    lines.append("")
    lines.append(
        "O pico de GPU é o acréscimo de memória do dispositivo durante a "
        "execução, medido contra a linha de base imediatamente anterior. É "
        "atribuível porque as execuções são serializadas em processos "
        "separados e nunca há dois modelos carregados ao mesmo tempo. Não é "
        "medição por processo: o driver das placas GeForce não a expõe no "
        "Windows."
    )
    lines.append("")

    failures = [r for r in report.runs if not r.ok]
    if failures:
        lines.extend(["## Falhas", ""])
        for run in failures:
            lines.append(f"- **{run.label}** (`{run.model}`): {run.error}")
        lines.append("")

    if not successful:
        lines.append("Nenhuma configuração produziu transcrição.")
        return "\n".join(lines) + "\n"

    labels = [r.label for r in successful]
    lines.extend(["## Comparação lado a lado", ""])
    lines.append(
        f"Alinhado por janela de {WINDOW_SECONDS} segundos do áudio, não por "
        f"índice de segmento: configurações diferentes cortam o áudio de "
        f"formas diferentes."
    )
    lines.append("")
    lines.append("| Instante | " + " | ".join(labels) + " |")
    lines.append("|---" * (len(labels) + 1) + "|")
    for start_ms, texts in _windows(successful, WINDOW_SECONDS):
        cells = [texts.get(label, "—").replace("|", "\\|") for label in labels]
        lines.append(f"| `{_stamp(start_ms)}` | " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines) + "\n"


# -- worker ------------------------------------------------------------

def _worker() -> int:
    """Transcribe one configuration and print the result as JSON.

    Runs as its own process so that GPU memory is returned by process exit,
    and so that a crash here is one failed row in the report rather than a
    dead benchmark.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    request = json.loads(sys.stdin.read())
    from .config import load_config

    config = load_config({
        "data_dir": request["data_dir"],
        "allow_cpu_fallback": request.get("allow_cpu_fallback", False),
    })

    try:
        from .engine import build_engine
        from .engine.media import probe_duration_ms

        engine = build_engine(config, model=request["model"])

        load_started = time.monotonic()
        info = engine.info()
        # Force the model into memory here so loading is timed on its own,
        # instead of being charged to the first decode.
        warm_up = getattr(engine, "warm_up", None)
        if callable(warm_up):
            warm_up()
        load_seconds = time.monotonic() - load_started

        audio_path = Path(request["audio_path"])
        decode_started = time.monotonic()
        result = engine.transcribe(
            audio_path,
            language=request["language"],
            vocabulary=request["vocabulary"],
        )
        decode_seconds = time.monotonic() - decode_started

        audio_ms = result.duration_ms or probe_duration_ms(audio_path) or 0
        payload = {
            "ok": True,
            "engine_id": result.engine.identifier(),
            "device": info.device,
            "load_seconds": load_seconds,
            "decode_seconds": decode_seconds,
            "audio_seconds": audio_ms / 1000,
            "segments": [
                {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
                for s in result.segments
            ],
        }
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    sys.stdout.write("\n" + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    if "--worker" in sys.argv:
        raise SystemExit(_worker())
    sys.stderr.write("Use: voxvault bench <arquivo> --models a,b\n")
    raise SystemExit(2)
