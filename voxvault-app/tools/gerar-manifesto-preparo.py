"""Write preparo.json: what the first-use preparation downloads, and how much.

The preparation screen shows these numbers before anything is downloaded, so
they are computed at build time from the files that decide them, not typed in:

- the packages come from ``voxvault-core/uv.lock``, resolved for the one
  platform the installer targets -- Windows x64, CPython 3.12 -- by evaluating
  every marker in ``uv export``, and each weighs what its chosen wheel weighs
  in the lock;
- the GPU components are the difference between the environment with the
  ``cuda`` extra and the one without;
- the VRAM thresholds are the core's own ``MODEL_VRAM_MB`` and
  ``VRAM_MARGIN_MB``, read from ``engine/capability.py``, so the app and the
  core never disagree about which model a GPU can hold.

Run by tools/preparar-recursos.ps1 with the pinned uv:

    uv run --no-project --python 3.12 --with packaging python gerar-manifesto-preparo.py <saida>
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from packaging.markers import Marker
from packaging.utils import canonicalize_name

RAIZ = Path(__file__).resolve().parents[2]
NUCLEO = RAIZ / "voxvault-core"
LOCK = NUCLEO / "uv.lock"
CAPACIDADE = NUCLEO / "src" / "voxvault" / "engine" / "capability.py"

#: The platform the installer targets, as markers see it.
AMBIENTE = {
    "implementation_name": "cpython",
    "implementation_version": "3.12.12",
    "os_name": "nt",
    "platform_machine": "AMD64",
    "platform_python_implementation": "CPython",
    "platform_release": "10",
    "platform_system": "Windows",
    "platform_version": "10.0.26100",
    "python_full_version": "3.12.12",
    "python_version": "3.12",
    "sys_platform": "win32",
    "extra": "",
}

#: Downloaded by uv in the ``interpretador`` step: python-build-standalone,
#: CPython 3.12 install_only_stripped for x86_64-pc-windows-msvc, the build
#: the pinned uv (0.12.18) picks -- 3.12.14, from releases.astral.sh. Measured
#: on 2026-09-23: the archive's Content-Length and the installed folder.
INTERPRETADOR = {"versao": "3.12.14", "bytes": 21_980_728, "bytes_em_disco": 63_321_361}

#: A wheel is a zip. What it takes once installed, measured on 2026-09-23 on
#: environments made from this lock with the pinned uv: engine+mcp, 100.5 MB
#: of wheels, became 288.3 MB; the three CUDA wheels, 1376 MB, became
#: 2104 MB -- binaries that were already compressed grow far less.
EXPANSAO = {"dependencias": 2.87, "gpu": 1.53}

#: The faster-whisper conversions the engine loads, with what a complete
#: download weighs in the model cache -- measured folders, 2026-09-23.
MODELOS = {
    "large-v3": {"repositorio": "Systran/faster-whisper-large-v3", "bytes": 3_090_836_727},
    "large-v3-turbo": {
        "repositorio": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "bytes": 1_621_667_008,
    },
    "medium": {"repositorio": "Systran/faster-whisper-medium", "bytes": 1_530_572_644},
}

#: Free space asked for beyond the downloads: a fifth more for the
#: environment, a gigabyte beside the model for the first recordings.
FOLGA = {"ambiente": 0.20, "dados_bytes": 1_000_000_000}


def _limites_de_vram() -> dict[str, int]:
    """``MODEL_VRAM_MB`` and ``VRAM_MARGIN_MB``, read without importing the core."""
    arvore = ast.parse(CAPACIDADE.read_text(encoding="utf-8"))
    valores: dict[str, object] = {}
    for no in ast.walk(arvore):
        alvo = None
        if isinstance(no, ast.Assign) and len(no.targets) == 1:
            alvo, valor = no.targets[0], no.value
        elif isinstance(no, ast.AnnAssign) and no.value is not None:
            alvo, valor = no.target, no.value
        if isinstance(alvo, ast.Name) and alvo.id in {"MODEL_VRAM_MB", "VRAM_MARGIN_MB"}:
            valores[alvo.id] = ast.literal_eval(valor)
    memoria, margem = valores["MODEL_VRAM_MB"], valores["VRAM_MARGIN_MB"]
    return {
        "large-v3": int(memoria["large-v3"]) + int(margem),
        "large-v3-turbo": int(memoria["large-v3-turbo"]) + int(margem),
    }


def _exportar(uv: str, extras: list[str]) -> list[tuple[str, str]]:
    """Name and version of every package the lock installs here, for extras."""
    comando = [
        uv, "export", "--project", str(NUCLEO), "--frozen", "--no-dev",
        "--no-hashes", "--no-emit-project", "--format", "requirements-txt",
    ]
    for extra in extras:
        comando += ["--extra", extra]
    saida = subprocess.run(
        comando, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout
    pacotes = []
    for linha in saida.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith(("#", "-")):
            continue
        requisito, _, marcador = linha.partition(";")
        nome, _, versao = requisito.strip().partition("==")
        if marcador.strip() and not Marker(marcador.strip()).evaluate(AMBIENTE):
            continue
        pacotes.append((canonicalize_name(nome), versao.strip()))
    return pacotes


_TAG = re.compile(r"-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$")


def _roda_para_windows(rodas: list[dict]) -> dict | None:
    """The wheel uv would pick here: the best tag that installs on win_amd64 cp312."""
    melhor, nota = None, -1
    for roda in rodas:
        achado = _TAG.search(roda["url"].split("/")[-1])
        if not achado:
            continue
        py, abi, plataforma = achado["py"], achado["abi"], achado["plat"]
        if plataforma not in {"win_amd64", "any"}:
            continue
        pys = set(py.split("."))
        if not (pys & {"cp312", "py3", "py312"} or (abi == "abi3" and any(
            p.startswith("cp3") and int(p[3:] or 0) <= 12 for p in pys
        ))):
            continue
        valor = (2 if plataforma == "win_amd64" else 0) + (1 if "cp312" in pys else 0)
        if valor > nota:
            melhor, nota = roda, valor
    return melhor


def _pesar(pacotes: list[tuple[str, str]], lock: dict) -> int:
    por_nome = {
        (canonicalize_name(p["name"]), p["version"]): p for p in lock["package"]
    }
    total = 0
    for chave in pacotes:
        pacote = por_nome.get(chave)
        if pacote is None:
            raise SystemExit(f"{chave[0]} {chave[1]} nao esta no uv.lock")
        roda = _roda_para_windows(pacote.get("wheels", []))
        if roda is not None:
            total += int(roda["size"])
        elif "sdist" in pacote:
            total += int(pacote["sdist"].get("size", 0))
    return total


def gerar(saida: Path, uv: str) -> dict:
    lock = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    base = _exportar(uv, ["engine", "mcp"])
    completo = _exportar(uv, ["engine", "mcp", "cuda"])
    so_gpu = sorted(set(completo) - set(base))
    bytes_base = _pesar(base, lock)
    bytes_gpu = _pesar(so_gpu, lock)
    manifesto = {
        "gerado_de": "voxvault-core/uv.lock",
        # Over the content, not the line endings: a Windows checkout turns the
        # lock's LF into CRLF, and the same lock must not look like another one
        # -- a stamp that disagrees prepares the environment again.
        "sha256_lock": hashlib.sha256(LOCK.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "interpretador": INTERPRETADOR,
        "dependencias": {
            "pacotes": len(base),
            "bytes": bytes_base,
            "bytes_em_disco": int(bytes_base * EXPANSAO["dependencias"]),
        },
        "gpu": {
            "pacotes": len(so_gpu),
            "bytes": bytes_gpu,
            "bytes_em_disco": int(bytes_gpu * EXPANSAO["gpu"]),
        },
        "modelos": MODELOS,
        "vram_minima_mb": _limites_de_vram(),
        "folga": FOLGA,
    }
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifesto


def main() -> int:
    if len(sys.argv) != 2:
        print("uso: gerar-manifesto-preparo.py <saida>", file=sys.stderr)
        return 1
    uv = os.environ.get("VOXVAULT_UV", "uv")
    manifesto = gerar(Path(sys.argv[1]), uv)
    print(json.dumps({k: manifesto[k] for k in ("dependencias", "gpu", "vram_minima_mb")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
