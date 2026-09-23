"""Build a data directory of fictitious meetings, for screenshots and tests.

Nothing here comes from a real recording: the titles, the lines and the notes
are invented, and the audio is synthesized -- a soft tone per speaker with
gaps where nobody talks -- so the player has real FLAC to open and nothing a
person said ends up in a picture or in the repository.

Run with the core's own interpreter, which is where ``voxvault`` is importable:

    voxvault-core/.venv/Scripts/python.exe voxvault-app/tools/dados-de-demonstracao.py <pasta>

Then point the application at it with ``VOXVAULT_DATA_DIR=<pasta>``.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import soundfile as sf

from voxvault.layout import meeting_dir, track_path
from voxvault.session.finalize import Metadata, Step, write_metadata
from voxvault.store import NoteAuthor, Origin, TranscriptStore, regenerate_exports
from voxvault.types import EngineInfo, MeetingState, Segment, Track

RATE = 16_000
ENGINE = EngineInfo(
    name="faster-whisper", model="large-v3", compute_type="float16",
    device="cuda", version="1.2.1",
)

#: (title, minutes, days ago, hour, lines, notes). Each line is (track, text);
#: the mic track is "você", the system track is everyone else.
MEETINGS = [
    (
        "Planejamento do trimestre",
        6, 1, 10,
        [
            (Track.MIC, "Bom dia, pessoal. Vamos fechar as prioridades do trimestre hoje."),
            (Track.SYSTEM, "Bom dia. Eu trouxe a lista do time de produto, são cinco itens."),
            (Track.SYSTEM, "O primeiro é a migração do faturamento para o novo serviço."),
            (Track.MIC, "Esse é o que mais preocupa. Qual é o prazo realista?"),
            (Track.SYSTEM, "Com duas pessoas dedicadas, oito semanas."),
            (Track.MIC, "Então fica como prioridade um, e revisamos o prazo na metade."),
            (Track.SYSTEM, "Combinado. O segundo item é o painel de indicadores."),
            (Track.MIC, "Esse pode esperar o mês que vem, depende da migração."),
        ],
        [
            ("resumo", NoteAuthor.agent("Claude Code"),
             "Prioridades do trimestre definidas. A migração do faturamento é a "
             "prioridade um, com prazo estimado de oito semanas e revisão na "
             "metade. O painel de indicadores fica para o mês seguinte."),
            ("decisoes", NoteAuthor.user(),
             "1. Migração do faturamento é a prioridade do trimestre.\n"
             "2. Painel de indicadores depois da migração."),
        ],
    ),
    (
        "Revisão de arquitetura do serviço de pagamentos",
        9, 2, 14,
        [
            (Track.SYSTEM, "A proposta é separar a conciliação do fluxo de captura."),
            (Track.MIC, "Qual o ganho concreto, além de isolar as falhas?"),
            (Track.SYSTEM, "A conciliação para de disputar o banco com a captura no fim do mês."),
            (Track.MIC, "Faz sentido. E a fila entre os dois, quem mantém?"),
            (Track.SYSTEM, "O time de plataforma já opera uma, a gente reaproveita."),
            (Track.MIC, "Então quero um teste de carga antes de decidir."),
        ],
        [
            ("pendencias", NoteAuthor.user(),
             "- Rodar teste de carga com a conciliação separada.\n"
             "- Confirmar com plataforma a capacidade da fila."),
        ],
    ),
    (
        "Retrospectiva da sprint 42",
        5, 4, 16,
        [
            (Track.MIC, "O que funcionou bem nessa sprint?"),
            (Track.SYSTEM, "A revisão de código em par reduziu bastante o retrabalho."),
            (Track.SYSTEM, "E o ambiente de homologação ficou estável a semana toda."),
            (Track.MIC, "E o que a gente muda para a próxima?"),
            (Track.SYSTEM, "Estimativas. Subestimamos as tarefas de integração de novo."),
        ],
        [],
    ),
    (
        "Conversa semanal com a liderança",
        4, 6, 11,
        [
            (Track.SYSTEM, "Como está a contratação para o time de dados?"),
            (Track.MIC, "Duas entrevistas finais esta semana, devemos fechar uma vaga."),
            (Track.SYSTEM, "Ótimo. E o orçamento de infraestrutura para o ano que vem?"),
            (Track.MIC, "Mando a proposta até sexta, com o comparativo de custos."),
        ],
        [
            ("livre", NoteAuthor.user(), "Enviar a proposta de orçamento até sexta."),
        ],
    ),
]

IMPORTED = (
    "Entrevista com cliente (arquivo importado)",
    3, 9, 15,
    [
        "Hoje o maior problema é conciliar os relatórios no fim do mês.",
        "A equipe leva dois dias para cruzar as planilhas.",
        "Se isso fosse automático, a gente fecharia o mês na mesma semana.",
    ],
)


def _tone(seconds: float, frequency: float, spans: list[tuple[float, float]]) -> np.ndarray:
    """A soft tone only inside ``spans``: speech-shaped enough for a player."""
    t = np.arange(int(seconds * RATE)) / RATE
    signal = np.zeros_like(t)
    for start, end in spans:
        inside = (t >= start) & (t < end)
        envelope = np.sin(np.pi * (t[inside] - start) / max(end - start, 1e-3))
        signal[inside] = 0.12 * envelope * np.sin(2 * math.pi * frequency * t[inside])
    return (signal * 32767).astype(np.int16)


def _write_track(path: Path, seconds: float, frequency: float, spans) -> None:
    sf.write(path, _tone(seconds, frequency, spans), RATE, subtype="PCM_16", format="FLAC")


def _segments(lines, total_ms: int) -> dict[str, list[Segment]]:
    """Spread the lines over the meeting, one after the other."""
    step = total_ms // (len(lines) + 1)
    found: dict[str, list[Segment]] = {}
    for index, (track, text) in enumerate(lines):
        start = step * index + 800
        end = start + min(step - 400, 1800 + 55 * len(text))
        found.setdefault(str(track), []).append(Segment(start, end, text))
    return found


def _recorded(store, data_dir: Path, now: datetime, spec) -> str:
    title, minutes, days_ago, hour, lines, notes = spec
    started = (now - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    total_ms = minutes * 60_000
    uid = "demo" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:24]
    directory = meeting_dir(data_dir, uid)
    directory.mkdir(parents=True)
    segments = _segments(lines, total_ms)
    for track, frequency in ((Track.MIC, 220.0), (Track.SYSTEM, 330.0)):
        spans = [(s.start_ms / 1000, s.end_ms / 1000) for s in segments.get(str(track), [])]
        _write_track(track_path(directory, str(track), final=True), total_ms / 1000,
                     frequency, spans)
    store.create_meeting(uid=uid, title=title, started_at=started, directory=directory)
    store.finish_meeting(uid, ended_at=started + timedelta(milliseconds=total_ms),
                         duration_ms=total_ms, state=MeetingState.RECORDED)
    write_metadata(directory, Metadata(
        uid=uid, title=title, started_at=started.isoformat(),
        ended_at=(started + timedelta(milliseconds=total_ms)).isoformat(),
        duration_ms=total_ms, step=Step.QUEUED.value,
        tracks={"mic": {"duracao_ms": total_ms}, "system": {"duracao_ms": total_ms}},
        devices={"mic": "Microfone (demonstração)", "system": "Alto-falantes (demonstração)"},
    ))
    revision = store.begin_revision(uid, engine=ENGINE, language="pt")
    for track, found in segments.items():
        store.add_segments(revision.id, track, found)
    store.publish_revision(revision.id, tracks_ok=[Track.MIC, Track.SYSTEM])
    for kind, author, content in notes:
        store.create_note(uid, kind=kind, content=content, author=author)
    regenerate_exports(store, uid)
    return uid


def _imported(store, data_dir: Path, now: datetime) -> str:
    title, minutes, days_ago, hour, lines = IMPORTED
    started = (now - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    total_ms = minutes * 60_000
    uid = f"demoimportada{days_ago:02d}"
    directory = meeting_dir(data_dir, uid)
    directory.mkdir(parents=True)
    step = total_ms // (len(lines) + 1)
    found = [Segment(step * i + 800, step * i + 800 + 3500, text) for i, text in enumerate(lines)]
    _write_track(directory / "source.flac", total_ms / 1000, 262.0,
                 [(s.start_ms / 1000, s.end_ms / 1000) for s in found])
    store.create_meeting(
        uid=uid, title=title, started_at=started, directory=directory,
        origin=Origin.IMPORTED, state=MeetingState.RECORDED,
        source_path=r"C:\Users\demonstracao\Documents\entrevista.m4a",
        duration_ms=total_ms,
    )
    write_metadata(directory, Metadata(
        uid=uid, title=title, started_at=started.isoformat(),
        ended_at=(started + timedelta(milliseconds=total_ms)).isoformat(),
        duration_ms=total_ms, step=Step.QUEUED.value,
        tracks={"importada": {"duracao_ms": total_ms}},
        devices={"origem": r"C:\Users\demonstracao\Documents\entrevista.m4a"},
    ))
    revision = store.begin_revision(uid, engine=ENGINE, language="pt")
    store.add_segments(revision.id, "importada", found)
    store.publish_revision(revision.id, tracks_ok=["importada"])
    regenerate_exports(store, uid)
    return uid


def build(data_dir: Path) -> list[str]:
    if data_dir.exists() and any(data_dir.iterdir()):
        raise SystemExit(f"A pasta {data_dir} nao esta vazia; escolha uma pasta nova.")
    data_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    with TranscriptStore(data_dir / "voxvault.db") as store:
        uids = [_recorded(store, data_dir, now, spec) for spec in MEETINGS]
        uids.append(_imported(store, data_dir, now))
    return uids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pasta", type=Path)
    parser.add_argument("--recriar", action="store_true",
                        help="Apaga a pasta antes, se ela existir.")
    args = parser.parse_args(argv)
    if args.recriar and args.pasta.exists():
        shutil.rmtree(args.pasta)
    for uid in build(args.pasta):
        print(uid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
