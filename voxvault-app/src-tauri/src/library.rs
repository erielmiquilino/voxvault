//! Reading the library from the artifacts the core itself writes.
//!
//! Each meeting directory is self-describing by design -- `finalize.py` says so
//! in as many words: a directory copied to another machine still describes its
//! own recording. This module consumes exactly those files, `metadados.json`
//! and `transcricao.json`, both of which the core produces on purpose and
//! versions explicitly.
//!
//! What it deliberately does not do:
//!
//! * It does not open the database. The store's revision rules, timeline merge
//!   and full-text index belong to the core, and a second implementation of
//!   them in Rust would be a copy free to drift.
//! * It does not read the human-readable `transcricao.md`, nor the console
//!   output of any command. Where the core has no machine-readable surface yet,
//!   the app says so instead of guessing.
//!
//! The cost of this choice is stated rather than hidden: the per-meeting
//! *attempt* state -- queued, running, failed, and the failure reason -- lives
//! only in the database, so what this module can report is derived from the
//! finalization step and from whether an export exists. A `voxvault list --json`
//! would replace that inference with the core's own answer.

use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::paths;

const METADATA_NAME: &str = "metadados.json";
const STRUCTURED_NAME: &str = "transcricao.json";
const READABLE_NAME: &str = "transcricao.md";

/// Finalization steps, from `session/finalize.py`. Ordered: each implies the
/// ones before it.
const STEP_QUEUED: &str = "enfileirada";
const STEP_METADATA: &str = "metadados_gravados";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Situacao {
    /// Audio is still being written, or the app found the directory mid-write.
    Gravando,
    /// Finalized but not yet exported: it is in, or waiting for, the queue.
    NaFila,
    /// An export exists for the active revision.
    Pronta,
    /// Finalization stopped before the metadata was written.
    Incompleta,
}

#[derive(Debug, Clone, Serialize)]
pub struct Resumo {
    pub uid: String,
    pub titulo: String,
    /// ISO-8601, exactly as the core wrote it. Formatting is the interface's
    /// job and locale-dependent; re-encoding it here would only lose the
    /// original.
    pub inicio: String,
    pub fim: String,
    pub duracao_ms: i64,
    pub situacao: Situacao,
    pub origem: String,
    pub tem_audio: bool,
    pub tem_notas: bool,
    pub avisos: Vec<String>,
    pub diretorio: String,
    pub motor: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Segmento {
    pub id: i64,
    pub trilha: String,
    pub falante: String,
    pub inicio_ms: i64,
    pub fim_ms: i64,
    pub texto: String,
    pub sobreposto: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct Trilha {
    pub nome: String,
    pub caminho: String,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct Detalhe {
    pub resumo: Resumo,
    pub segmentos: Vec<Segmento>,
    pub trilhas: Vec<Trilha>,
    pub dispositivos: serde_json::Value,
    pub pausas: serde_json::Value,
    pub alinhamento: serde_json::Value,
    pub revisao: serde_json::Value,
    /// Empty when there is no export yet; the interface explains the situation
    /// from `situacao` rather than from an empty timeline.
    pub caminho_legivel: Option<String>,
    pub caminho_estruturado: Option<String>,
}

fn read_json(path: &Path) -> Option<serde_json::Value> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

fn as_str(value: &serde_json::Value, key: &str) -> String {
    value
        .get(key)
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string()
}

fn as_i64(value: &serde_json::Value, key: &str) -> i64 {
    value.get(key).and_then(|v| v.as_i64()).unwrap_or(0)
}

/// Audio still on disk for this meeting, in the layout `layout.py` owns.
///
/// The finalized lossless file wins over the recording-time WAV when both are
/// present: a leftover WAV means a finalization that had not yet removed it,
/// and the FLAC is the one that was verified.
fn tracks_on_disk(directory: &Path) -> Vec<Trilha> {
    let mut found = Vec::new();
    for base in ["mic", "system"] {
        for suffix in [".flac", ".wav"] {
            let candidate = directory.join(format!("{base}{suffix}"));
            if let Ok(meta) = std::fs::metadata(&candidate) {
                if meta.is_file() && meta.len() > 0 {
                    found.push(Trilha {
                        nome: base.to_string(),
                        caminho: candidate.to_string_lossy().into_owned(),
                        bytes: meta.len(),
                    });
                    break;
                }
            }
        }
    }
    if found.is_empty() {
        if let Ok(entries) = std::fs::read_dir(directory) {
            for entry in entries.flatten() {
                let path = entry.path();
                let is_source = path
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .map(|stem| stem == "source")
                    .unwrap_or(false);
                if is_source {
                    if let Ok(meta) = entry.metadata() {
                        if meta.len() > 0 {
                            found.push(Trilha {
                                nome: "importada".to_string(),
                                caminho: path.to_string_lossy().into_owned(),
                                bytes: meta.len(),
                            });
                        }
                    }
                }
            }
        }
    }
    found
}

fn summarise(directory: &Path) -> Option<Resumo> {
    let metadata = read_json(&directory.join(METADATA_NAME));
    let structured = read_json(&directory.join(STRUCTURED_NAME));
    if metadata.is_none() && structured.is_none() {
        return None;
    }

    let uid = directory
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default();
    let trilhas = tracks_on_disk(directory);

    // The structured export is the more authoritative of the two when present:
    // it is written from the published revision, while the metadata describes
    // the capture.
    let reuniao = structured
        .as_ref()
        .and_then(|value| value.get("reuniao"))
        .cloned();
    let revisao = structured
        .as_ref()
        .and_then(|value| value.get("revisao"))
        .cloned();

    let passo = metadata
        .as_ref()
        .map(|value| as_str(value, "passo_finalizacao"))
        .unwrap_or_default();

    let situacao = if structured.is_some() {
        Situacao::Pronta
    } else if passo == STEP_QUEUED || passo == STEP_METADATA {
        Situacao::NaFila
    } else if directory.join("mic.wav").exists() || directory.join("system.wav").exists() {
        Situacao::Gravando
    } else {
        Situacao::Incompleta
    };

    let titulo = reuniao
        .as_ref()
        .map(|r| as_str(r, "titulo"))
        .filter(|t| !t.is_empty())
        .or_else(|| {
            metadata
                .as_ref()
                .map(|m| as_str(m, "titulo"))
                .filter(|t| !t.is_empty())
        })
        .unwrap_or_else(|| uid.clone());

    let inicio = reuniao
        .as_ref()
        .map(|r| as_str(r, "inicio"))
        .filter(|s| !s.is_empty())
        .or_else(|| {
            metadata
                .as_ref()
                .map(|m| as_str(m, "inicio"))
                .filter(|s| !s.is_empty())
        })
        .unwrap_or_default();

    let duracao_ms = reuniao
        .as_ref()
        .map(|r| as_i64(r, "duracao_ms"))
        .filter(|d| *d > 0)
        .or_else(|| metadata.as_ref().map(|m| as_i64(m, "duracao_ms")))
        .unwrap_or(0);

    let origem = reuniao
        .as_ref()
        .map(|r| as_str(r, "origem"))
        .filter(|o| !o.is_empty())
        .unwrap_or_else(|| {
            if trilhas.iter().any(|t| t.nome == "importada") {
                "importada".to_string()
            } else {
                "gravada".to_string()
            }
        });

    Some(Resumo {
        uid,
        titulo,
        inicio,
        fim: metadata
            .as_ref()
            .map(|m| as_str(m, "fim"))
            .unwrap_or_default(),
        duracao_ms,
        situacao,
        origem,
        tem_audio: !trilhas.is_empty(),
        // The notes model lives in the core's `meeting-notes` capability, which
        // has no storage on disk yet. Reported as absent rather than guessed.
        tem_notas: false,
        avisos: metadata
            .as_ref()
            .and_then(|m| m.get("avisos"))
            .and_then(|v| v.as_array())
            .map(|items| {
                items
                    .iter()
                    .filter_map(|item| item.as_str().map(str::to_string))
                    .collect()
            })
            .unwrap_or_default(),
        diretorio: directory.to_string_lossy().into_owned(),
        motor: revisao
            .as_ref()
            .and_then(|r| r.get("motor"))
            .map(|motor| {
                let modelo = as_str(motor, "modelo");
                let dispositivo = as_str(motor, "dispositivo");
                if modelo.is_empty() {
                    String::new()
                } else if dispositivo.is_empty() {
                    modelo
                } else {
                    format!("{modelo} ({dispositivo})")
                }
            })
            .unwrap_or_default(),
    })
}

/// Every meeting in the data directory, most recent first.
pub fn listar(data_dir: &Path) -> Vec<Resumo> {
    let root = paths::recordings_dir(data_dir);
    let Ok(entries) = std::fs::read_dir(&root) else {
        return Vec::new();
    };
    let mut meetings: Vec<Resumo> = entries
        .flatten()
        .filter(|entry| entry.path().is_dir())
        .filter_map(|entry| summarise(&entry.path()))
        .collect();
    // The uid carries the start instant, so it orders correctly even for a
    // meeting whose metadata was never written.
    meetings.sort_by(|a, b| {
        b.inicio
            .cmp(&a.inicio)
            .then_with(|| b.uid.cmp(&a.uid))
    });
    meetings
}

pub fn meeting_dir(data_dir: &Path, uid: &str) -> PathBuf {
    paths::recordings_dir(data_dir).join(uid)
}

pub fn detalhar(data_dir: &Path, uid: &str) -> Option<Detalhe> {
    let directory = meeting_dir(data_dir, uid);
    let resumo = summarise(&directory)?;
    let metadata = read_json(&directory.join(METADATA_NAME)).unwrap_or(serde_json::Value::Null);
    let structured = read_json(&directory.join(STRUCTURED_NAME));

    let segmentos = structured
        .as_ref()
        .and_then(|value| value.get("segmentos"))
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .map(|item| Segmento {
                    id: as_i64(item, "id"),
                    trilha: as_str(item, "trilha"),
                    falante: as_str(item, "falante"),
                    inicio_ms: as_i64(item, "inicio_ms"),
                    fim_ms: as_i64(item, "fim_ms"),
                    texto: as_str(item, "texto"),
                    sobreposto: item
                        .get("sobreposto")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false),
                })
                .collect()
        })
        .unwrap_or_default();

    let legivel = directory.join(READABLE_NAME);
    let estruturado = directory.join(STRUCTURED_NAME);

    Some(Detalhe {
        trilhas: tracks_on_disk(&directory),
        dispositivos: metadata
            .get("dispositivos")
            .cloned()
            .unwrap_or(serde_json::Value::Null),
        pausas: metadata
            .get("pausas")
            .cloned()
            .unwrap_or(serde_json::Value::Null),
        alinhamento: metadata
            .get("alinhamento")
            .cloned()
            .unwrap_or(serde_json::Value::Null),
        revisao: structured
            .as_ref()
            .and_then(|value| value.get("revisao"))
            .cloned()
            .unwrap_or(serde_json::Value::Null),
        caminho_legivel: legivel.is_file().then(|| legivel.to_string_lossy().into_owned()),
        caminho_estruturado: estruturado
            .is_file()
            .then(|| estruturado.to_string_lossy().into_owned()),
        segmentos,
        resumo,
    })
}
