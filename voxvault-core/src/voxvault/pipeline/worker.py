"""The subprocess that actually runs inference.

Transcription lives in its own process for one reason that the specification
makes non-negotiable: when a recording starts while a transcription is running,
the first audio sample must be captured within 2000 ms **including GPU memory
release**. Unloading a CTranslate2 model in-process does not reliably return
CUDA memory on any schedule you can promise; killing a process does, every
time, and the operating system is the one making the guarantee.

The model is loaded once and serves jobs until the process is told to stop or
is killed, so the load cost is paid per worker rather than per meeting.

Protocol: one JSON object per line on stdin, one JSON object per line on
stdout. Line-oriented so the parent can read a reply without waiting for the
process to exit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

READY = "pronto"


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    import os

    # Never online from here: models come from disk, and nothing is reported.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    # Accented Portuguese travels this pipe. Without forcing UTF-8 the parent's
    # reader dies on the first cedilla and the job looks like a silent failure.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    handshake = sys.stdin.readline()
    if not handshake:
        return 0
    setup = json.loads(handshake)

    from ..config import load_config
    from ..engine import build_engine

    try:
        config = load_config({
            "data_dir": setup["data_dir"],
            "model": setup.get("model") or None,
            "allow_cpu_fallback": setup.get("allow_cpu_fallback", False),
        })
        engine = build_engine(config, model=setup.get("model") or None)
        engine.warm_up()
        info = engine.info()
    except Exception as exc:
        _emit({"ok": False, "stage": "carga", "error": f"{type(exc).__name__}: {exc}"})
        return 1

    _emit({
        "ok": True,
        "stage": READY,
        "engine": {
            "name": info.name,
            "model": info.model,
            "device": info.device,
            "compute_type": info.compute_type,
            "version": info.version,
        },
    })

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
        except json.JSONDecodeError as exc:
            _emit({"ok": False, "error": f"pedido ilegivel: {exc}"})
            continue

        if job.get("job") == "stop":
            return 0

        try:
            result = engine.transcribe(
                Path(job["audio_path"]),
                language=job.get("language") or "pt",
                vocabulary=job.get("vocabulary") or "",
            )
            _emit({
                "ok": True,
                "track": job.get("track", ""),
                "duration_ms": result.duration_ms,
                "segments": [
                    {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
                    for s in result.segments
                ],
            })
        except Exception as exc:
            _emit({
                "ok": False,
                "track": job.get("track", ""),
                "error": f"{type(exc).__name__}: {exc}",
            })

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
