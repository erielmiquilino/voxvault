"""Environment diagnosis.

Reports every prerequisite separately, because a missing one should disable
only the capabilities that depend on it. A missing media decoder must not stop
you from recording a meeting that is starting right now.

Nothing here imports the inference runtime unless it has to, and the report is
meant to be read by a person: each failing item says what to do about it.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, load_config, user_config_path
from .types import DiagnosticItem

#: 16 kHz mono 16-bit PCM, two tracks, while recording.
BYTES_PER_SECOND_PER_TRACK = 16_000 * 2
RAW_MB_PER_HOUR = BYTES_PER_SECOND_PER_TRACK * 3600 * 2 / (1024 * 1024)
#: After finalization, both tracks are kept as FLAC -- lossless, so that a
#: better model can re-transcribe the meeting later without generation loss.
#: Speech at 16 kHz compresses to roughly 55% of the raw PCM.
FLAC_RATIO = 0.55
COMPRESSED_MB_PER_HOUR = RAW_MB_PER_HOUR * FLAC_RATIO

MIN_FREE_GB_WARN = 5


@dataclass(slots=True)
class Report:
    items: list[DiagnosticItem] = field(default_factory=list)
    config_sources: dict[str, str] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return any(item.status == "falha" for item in self.items)

    @property
    def warned(self) -> bool:
        return any(item.status == "aviso" for item in self.items)

    def add(self, item: DiagnosticItem) -> None:
        self.items.append(item)


def _check_python() -> DiagnosticItem:
    major, minor = sys.version_info[:2]
    version = f"{major}.{minor}.{sys.version_info[2]}"
    if (major, minor) == (3, 12):
        return DiagnosticItem(
            "python", "Interpretador Python", "ok",
            f"{version} em {sys.executable}",
        )
    return DiagnosticItem(
        "python", "Interpretador Python", "falha",
        f"{version}; esperado 3.12 (intersecao de wheels: soxr pula o 3.13)",
        remedy="Recrie o ambiente com: uv venv --python 3.12",
    )


def _check_data_dir(config: Config) -> DiagnosticItem:
    path = config.data_dir
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return DiagnosticItem(
            "data_dir", "Diretorio de dados", "falha",
            f"{path} nao pode ser criado: {exc}",
            remedy="Escolha outro diretorio ou ajuste as permissoes.",
        )

    probe = path / ".voxvault-write-test"
    try:
        probe.write_bytes(b"x")
        probe.unlink()
    except OSError as exc:
        return DiagnosticItem(
            "data_dir", "Diretorio de dados", "falha",
            f"{path} existe mas nao e gravavel: {exc}",
            remedy="Ajuste as permissoes do diretorio.",
        )

    free_bytes = shutil.disk_usage(path).free
    free_gb = free_bytes / (1024 ** 3)
    detail = (
        f"{path} gravavel, {free_gb:.1f} GB livres. "
        f"Uma hora de reuniao ocupa cerca de {RAW_MB_PER_HOUR:.0f} MB durante a "
        f"gravacao e cerca de {COMPRESSED_MB_PER_HOUR:.0f} MB depois de comprimida."
    )
    if free_gb < MIN_FREE_GB_WARN:
        return DiagnosticItem(
            "data_dir", "Diretorio de dados", "aviso", detail,
            remedy=f"Libere espaco: abaixo de {MIN_FREE_GB_WARN} GB o risco de "
                   f"interromper uma gravacao longa e real.",
        )
    return DiagnosticItem("data_dir", "Diretorio de dados", "ok", detail)


def _check_decoder() -> DiagnosticItem:
    from .engine.media import find_ffmpeg  # noqa: PLC0415

    exe = find_ffmpeg()
    if not exe:
        return DiagnosticItem(
            "ffmpeg", "Decodificador de midia", "falha",
            "ffmpeg nao encontrado. Gravacao segue disponivel; "
            "importacao e transcricao nao.",
            remedy="winget install Gyan.FFmpeg  (e reabra o terminal)",
        )
    detail = exe
    if not shutil.which("ffmpeg"):
        detail += "  (encontrado fora do PATH; reabra o terminal para que o "
        detail += "PATH passe a inclui-lo)"
    return DiagnosticItem("ffmpeg", "Decodificador de midia", "ok", detail)


def _check_inference(config: Config) -> DiagnosticItem:
    from .engine.capability import probe_inference  # noqa: PLC0415

    capability = probe_inference(config)
    detail = capability.detail
    if capability.warning:
        detail = f"{detail} {capability.warning}".strip()
    return DiagnosticItem(
        "inferencia", "Ambiente de inferencia", capability.status,
        detail, remedy=capability.remedy,
    )


def _check_libraries() -> DiagnosticItem:
    """Load the audio libraries for real, in this launch environment.

    Checking that a package is installed proves nothing: what matters is that
    it imports here, with this interpreter and this PATH.
    """
    missing: list[str] = []
    for module in ("numpy", "soxr", "soundfile"):
        try:
            __import__(module)
        except Exception as exc:
            missing.append(f"{module} ({exc})")
    if missing:
        return DiagnosticItem(
            "bibliotecas", "Bibliotecas de audio", "falha",
            "; ".join(missing),
            remedy='uv pip install -e ".[dev]"',
        )
    import numpy
    import soundfile
    import soxr

    return DiagnosticItem(
        "bibliotecas", "Bibliotecas de audio", "ok",
        f"numpy {numpy.__version__}, soxr {soxr.__version__}, "
        f"soundfile {soundfile.__version__}",
    )


def _check_capture() -> DiagnosticItem:
    """Enumerate real audio endpoints, when the capture backend is present."""
    try:
        from .capture.devices import list_devices  # noqa: PLC0415
    except Exception as exc:
        return DiagnosticItem(
            "captura", "Captura de audio", "aviso",
            f"Backend de captura ainda indisponivel: {exc}",
            remedy="Conclua o portao de captura da Fase 1.",
        )
    try:
        devices = list_devices()
    except Exception as exc:
        return DiagnosticItem(
            "captura", "Captura de audio", "falha",
            f"Falha ao enumerar dispositivos: {exc}",
            remedy="Verifique o painel de som do Windows.",
        )
    inputs = [d for d in devices if getattr(d, "is_input", False)]
    outputs = [d for d in devices if not getattr(d, "is_input", False)]
    if not inputs or not outputs:
        return DiagnosticItem(
            "captura", "Captura de audio", "falha",
            f"{len(inputs)} entrada(s) e {len(outputs)} saida(s) encontradas; "
            f"as duas trilhas exigem pelo menos uma de cada.",
            remedy="Conecte um microfone e um dispositivo de saida.",
        )
    return DiagnosticItem(
        "captura", "Captura de audio", "ok",
        f"{len(inputs)} entrada(s) e {len(outputs)} saida(s) disponiveis",
    )


def run_diagnostics(config: Config | None = None) -> Report:
    cfg = config or load_config()
    report = Report(config_sources=dict(cfg.sources))
    report.add(_check_python())
    report.add(_check_libraries())
    report.add(_check_data_dir(cfg))
    report.add(_check_decoder())
    report.add(_check_inference(cfg))
    report.add(_check_capture())
    return report


_MARK = {"ok": "[ ok   ]", "aviso": "[ aviso]", "falha": "[ FALHA]"}


def format_report(report: Report, config: Config, *, verbose: bool = False) -> str:
    lines: list[str] = ["", "VoxVault - diagnostico de ambiente", ""]
    for item in report.items:
        lines.append(f"{_MARK.get(item.status, '[      ]')}  {item.label}")
        if item.detail:
            lines.append(f"          {item.detail}")
        if item.remedy and item.status != "ok":
            lines.append(f"          -> {item.remedy}")
    lines.append("")

    lines.append("Configuracao efetiva (valor <- origem)")
    lines.append(f"          arquivo do usuario: {user_config_path()}")
    interesting = ["data_dir", "model", "device", "compute_type", "language",
                   "device_role", "allow_cpu_fallback"]
    for name in interesting:
        value = getattr(config, name)
        lines.append(f"          {name} = {value}  <- {config.source_of(name)}")
    if verbose:
        for name, origin in sorted(report.config_sources.items()):
            if name not in interesting:
                lines.append(f"          {name} = {getattr(config, name)}  <- {origin}")
    lines.append("")

    if report.failed:
        lines.append("Resultado: ha falhas. Os itens marcados bloqueiam as "
                     "capacidades que dependem deles.")
    elif report.warned:
        lines.append("Resultado: utilizavel, com avisos.")
    else:
        lines.append("Resultado: tudo ok.")
    lines.append("")
    return "\n".join(lines)
