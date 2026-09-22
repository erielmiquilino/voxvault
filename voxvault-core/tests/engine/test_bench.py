"""Benchmark harness logic that does not need a GPU.

Covers specs/benchmark-harness/spec.md. The parts that need real inference are
exercised by running `voxvault bench` against real audio; what is tested here
is the reasoning the report depends on -- above all that alignment is by time
window and never by segment index.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from voxvault.bench import (
    BenchReport,
    RunResult,
    _last_json_object,
    _slug,
    _windows,
    format_report,
)
from voxvault.types import Segment


def _run(label: str, segments: list[tuple[int, int, str]], **kwargs) -> RunResult:
    return RunResult(
        label=label,
        model=label,
        ok=True,
        engine_id=f"faster-whisper/{label}/cuda/float16",
        device="cuda",
        segments=[Segment(a, b, t) for a, b, t in segments],
        **kwargs,
    )


def test_windows_group_by_time_not_by_segment_index() -> None:
    """Scenario: Configuracoes que segmentam o audio de forma diferente.

    One model emits a single long segment where another emits three short
    ones. Pairing by index would compare unrelated speech.
    """
    chatty = _run("chatty", [
        (0, 3000, "um"), (3000, 6000, "dois"), (6000, 9000, "tres"),
        (20000, 23000, "quatro"),
    ])
    terse = _run("terse", [(0, 9000, "um dois tres"), (20000, 23000, "quatro")])

    windows = _windows([chatty, terse], 15)

    assert [start for start, _ in windows] == [0, 15000]
    first = dict(windows)[0]
    assert first["chatty"] == "um dois tres"
    assert first["terse"] == "um dois tres"
    second = dict(windows)[15000]
    assert second["chatty"] == second["terse"] == "quatro"


def test_no_text_is_dropped_for_lacking_a_counterpart() -> None:
    """Scenario: nenhum texto e descartado por nao ter par na outra coluna."""
    complete = _run("completo", [(0, 2000, "inicio"), (40000, 42000, "final")])
    truncated = _run("truncado", [(0, 2000, "inicio")])

    windows = dict(_windows([complete, truncated], 15))

    assert 30000 in windows, "a janela sem par ainda tem de aparecer"
    assert windows[30000]["completo"] == "final"
    assert "truncado" not in windows[30000]


def test_realtime_factor_excludes_load_time() -> None:
    """Load is a fixed cost; only decoding grows with meeting length."""
    run = _run("m", [(0, 1000, "x")], load_seconds=30.0, decode_seconds=10.0)
    run.audio_seconds = 60.0
    assert run.realtime_factor == 6.0
    assert run.total_seconds == 40.0


def test_realtime_factor_is_zero_rather_than_dividing_by_zero() -> None:
    run = _run("m", [(0, 1000, "x")], load_seconds=1.0, decode_seconds=0.0)
    run.audio_seconds = 60.0
    assert run.realtime_factor == 0.0


def _report(runs: list[RunResult], tmp_path: Path) -> BenchReport:
    return BenchReport(
        audio_path=Path(r"D:\audio.wav"),
        language="pt",
        vocabulary="Kubernetes",
        started_at=datetime(2026, 9, 21, 23, 0, 0),
        output_dir=tmp_path,
        runs=runs,
    )


def test_report_opens_with_the_summary_table(tmp_path: Path) -> None:
    """Scenario: o relatorio abre com a tabela de metricas, antes do corpo."""
    a = _run("large-v3", [(0, 2000, "ola")], load_seconds=13.5, decode_seconds=2.9)
    a.audio_seconds = 61.8
    a.peak_gpu_mb = 4221

    text = format_report(_report([a], tmp_path))

    assert text.index("## Desempenho") < text.index("## Comparação lado a lado")
    assert "4221 MB" in text
    assert "21.3x" in text


def test_failed_run_is_reported_with_its_reason(tmp_path: Path) -> None:
    """Scenario: Falha de uma execucao nao derruba as demais."""
    ok = _run("large-v3", [(0, 2000, "ola")], load_seconds=1.0, decode_seconds=1.0)
    ok.audio_seconds = 2.0
    bad = RunResult(label="quebrado", model="quebrado", ok=False, error="modelo invalido")

    report = _report([ok, bad], tmp_path)
    text = format_report(report)

    assert report.any_failed is True
    assert "## Falhas" in text
    assert "modelo invalido" in text
    # The successful run still appears in full.
    assert "ola" in text


def test_cpu_run_reports_gpu_peak_as_not_applicable(tmp_path: Path) -> None:
    """Scenario: Metricas de execucao em CPU."""
    run = _run("cpu", [(0, 2000, "ola")], load_seconds=1.0, decode_seconds=8.0)
    run.device = "cpu"
    run.audio_seconds = 2.0
    run.peak_gpu_mb = None

    text = format_report(_report([run], tmp_path))
    assert "| n/a |" in text


def test_report_without_any_success_says_so(tmp_path: Path) -> None:
    bad = RunResult(label="x", model="x", ok=False, error="explodiu")
    text = format_report(_report([bad], tmp_path))
    assert "Nenhuma configuração produziu transcrição." in text


def test_pipe_in_text_does_not_break_the_markdown_table(tmp_path: Path) -> None:
    run = _run("m", [(0, 2000, "a | b")], load_seconds=1.0, decode_seconds=1.0)
    run.audio_seconds = 2.0
    text = format_report(_report([run], tmp_path))
    assert r"a \| b" in text


def test_last_json_object_ignores_library_chatter() -> None:
    """Libraries print to stdout uninvited; the last JSON object wins."""
    noisy = 'Downloading...\nnot json\n{"ok": false, "error": "x"}\n{"ok": true}\n'
    assert _last_json_object(noisy) == {"ok": True}


def test_last_json_object_returns_none_when_there_is_none() -> None:
    assert _last_json_object("erro fatal\ntraceback\n") is None


def test_slug_keeps_paths_safe() -> None:
    assert _slug("large-v3") == "large-v3"
    assert _slug("modelo/estranho:1") == "modelo-estranho-1"
    assert _slug("///") == "execucao"
