//! The library, assembled from two sources with a clear division of labour.
//!
//! **State comes from the core.** What meetings exist, whether each one has a
//! transcript, and what its current attempt is doing all come from
//! `voxvault list --json`. That command reports availability and attempt as
//! separate attributes, which is what lets this module distinguish a failed
//! transcription from a queued one -- a distinction no single status string
//! could carry, and one the interface was previously unable to make.
//!
//! **Per-meeting content comes from the meeting's own directory.** The
//! timeline, the notes and the audio tracks are read from `transcricao.json`
//! and `metadados.json`, which the core writes on purpose and versions
//! explicitly, and regenerates on every note write.
//!
//! What this module never does is open the database. The store's revision
//! rules, timeline merge and full-text index belong to the core, and a second
//! implementation of them here would be a copy free to drift.

use std::path::Path;

use serde::Serialize;

const METADATA_NAME: &str = "metadados.json";
const STRUCTURED_NAME: &str = "transcricao.json";
const READABLE_NAME: &str = "transcricao.md";

/// What the list shows at a glance.
///
/// Derived from the core's own two independent attributes -- is a transcript
/// available, and what is the current attempt doing -- and never from one
/// collapsed status string. The distinction is the reason a failed
/// transcription is not shown as a queued one, and the reason a meeting being
/// reprocessed is still shown as readable.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Situacao {
    Gravando,
    NaFila,
    Transcrevendo,
    Pronta,
    Falhou,
    /// No transcript, no attempt, nothing running: finalization stopped early.
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
    /// Kept alongside `situacao` rather than folded into it: a reprocessing
    /// meeting is both readable and busy.
    pub transcricao_disponivel: bool,
    pub completude: String,
    /// The revision the store would serve. Compared against the one the export
    /// on disk was written from, to notice a stale export.
    pub revisao_ativa: String,
    pub estado_da_tentativa: String,
    pub motivo_da_falha: Option<String>,
    pub origem: String,
    pub tem_audio: bool,
    pub tem_notas: bool,
    pub avisos: Vec<String>,
    pub diretorio: String,
    pub motor: String,
}

fn situacao_de(
    estado_da_gravacao: &str,
    disponivel: bool,
    tentativa: &str,
) -> Situacao {
    match tentativa {
        "falhou" => Situacao::Falhou,
        "na_fila" => Situacao::NaFila,
        "em_execucao" => Situacao::Transcrevendo,
        _ if disponivel => Situacao::Pronta,
        _ if estado_da_gravacao == "gravando" || estado_da_gravacao == "pausada" => {
            Situacao::Gravando
        }
        _ => Situacao::Incompleta,
    }
}

/// Build the list from the core's answer, adding only what the core does not
/// yet report: whether the meeting carries notes, and the warnings its capture
/// recorded. Both come from files the core itself wrote.
pub fn listar_do_nucleo(payload: &serde_json::Value) -> Vec<Resumo> {
    let Some(itens) = payload.get("reunioes").and_then(|v| v.as_array()) else {
        return Vec::new();
    };
    itens
        .iter()
        .map(|item| {
            let diretorio = as_str(item, "diretorio");
            let caminho = Path::new(&diretorio);
            let metadata = read_json(&caminho.join(METADATA_NAME));
            let structured = read_json(&caminho.join(STRUCTURED_NAME));
            let disponivel = item
                .get("transcricao_disponivel")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            Resumo {
                uid: as_str(item, "id"),
                titulo: as_str(item, "titulo"),
                inicio: as_str(item, "inicio"),
                fim: as_str(item, "fim"),
                duracao_ms: as_i64(item, "duracao_ms"),
                situacao: situacao_de(
                    &as_str(item, "estado_da_gravacao"),
                    disponivel,
                    &as_str(item, "estado_da_tentativa"),
                ),
                transcricao_disponivel: disponivel,
                completude: as_str(item, "completude"),
                revisao_ativa: as_str(item, "revisao_ativa"),
                estado_da_tentativa: as_str(item, "estado_da_tentativa"),
                motivo_da_falha: item
                    .get("motivo_da_falha")
                    .and_then(|v| v.as_str())
                    .map(str::to_string),
                origem: as_str(item, "origem"),
                tem_audio: item
                    .get("tem_audio")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false),
                tem_notas: !notes_from(structured.as_ref()).is_empty(),
                avisos: metadata
                    .as_ref()
                    .and_then(|m| m.get("avisos"))
                    .and_then(|v| v.as_array())
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(|i| i.as_str().map(str::to_string))
                            .collect()
                    })
                    .unwrap_or_default(),
                motor: as_str(item, "motor"),
                diretorio,
            }
        })
        .collect()
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

/// A note as the structured export carries it.
///
/// Notes travel in their own key, never mixed into `segmentos`. That
/// separation is the point of the feature -- a summary a model produced can be
/// wrong, and "this was said" must stay distinguishable from "this was
/// interpreted" -- so the app keeps them apart here too.
#[derive(Debug, Clone, Serialize)]
pub struct Nota {
    pub uid: String,
    pub tipo: String,
    pub conteudo: String,
    pub autoria_tipo: String,
    pub autoria_cliente: String,
    pub criada_em: String,
    pub alterada_em: String,
}

/// The per-meeting content, without the summary.
///
/// The summary is not repeated here because the interface already holds the
/// core's answer for it from the list. Re-deriving it would put a second,
/// weaker copy of the meeting's state one screen away from the authoritative
/// one, and the two would eventually disagree.
#[derive(Debug, Clone, Serialize)]
pub struct Detalhe {
    pub segmentos: Vec<Segmento>,
    pub notas: Vec<Nota>,
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

/// Notes out of a structured export.
///
/// Version 2 of the format always carries the `notas` key, empty list included,
/// so an absent key means the export predates it -- which is why absence is
/// reported as "unknown" by returning nothing, and an empty list as "none".
fn notes_from(structured: Option<&serde_json::Value>) -> Vec<Nota> {
    let Some(items) = structured
        .and_then(|value| value.get("notas"))
        .and_then(|value| value.as_array())
    else {
        return Vec::new();
    };
    items
        .iter()
        .map(|item| {
            let autoria = item.get("autoria");
            Nota {
                uid: as_str(item, "uid"),
                tipo: as_str(item, "tipo"),
                conteudo: as_str(item, "conteudo"),
                autoria_tipo: autoria.map(|a| as_str(a, "tipo")).unwrap_or_default(),
                autoria_cliente: autoria.map(|a| as_str(a, "cliente")).unwrap_or_default(),
                criada_em: as_str(item, "criada_em"),
                alterada_em: as_str(item, "alterada_em"),
            }
        })
        .collect()
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


/// Which revision the export on disk was written from, if any.
///
/// The structured export records it precisely so a reader can tell whether the
/// file still corresponds to what the store would serve.
pub fn revisao_exportada(diretorio: &str) -> Option<String> {
    let valor = read_json(&Path::new(diretorio).join(STRUCTURED_NAME))?;
    let uid = as_str(valor.get("revisao")?, "uid");
    (!uid.is_empty()).then_some(uid)
}

/// Everything a meeting's own directory says about it.
///
/// Takes the directory the core reported for that meeting rather than
/// recomputing it from the data directory: the core is the one that knows where
/// a meeting lives, and a path rebuilt here would be a second convention.
pub fn detalhar(diretorio: &str) -> Option<Detalhe> {
    let directory = Path::new(diretorio);
    if !directory.is_dir() {
        return None;
    }
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
        segmentos,
        notas: notes_from(structured.as_ref()),
        trilhas: tracks_on_disk(directory),
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
        caminho_legivel: legivel
            .is_file()
            .then(|| legivel.to_string_lossy().into_owned()),
        caminho_estruturado: estruturado
            .is_file()
            .then(|| estruturado.to_string_lossy().into_owned()),
    })
}

#[cfg(test)]
mod testes {
    use super::*;

    /// One entry exactly as `voxvault list --json` emits it.
    fn payload(tentativa: &str, disponivel: bool, motivo: serde_json::Value) -> serde_json::Value {
        serde_json::json!({
            "reunioes": [{
                "id": "ba28bcd99c3348feb407ed637a5b5f57",
                "titulo": "Titulo novo",
                "inicio": "2026-09-22T03:48:09.214000+00:00",
                "fim": "2026-09-22T03:48:19.370000+00:00",
                "duracao_ms": 9142,
                "origem": "gravada",
                "estado_da_gravacao": "gravada",
                "diretorio": "Z:/nao-existe/ba28bcd9",
                "transcricao_disponivel": disponivel,
                "completude": "completa",
                "revisao_ativa": "74d8631422fa4821ae878930daab7fd8",
                "motor": "faster-whisper/large-v3/cuda/float16/4.8.2",
                "estado_da_tentativa": tentativa,
                "motivo_da_falha": motivo,
                "tem_audio": true
            }]
        })
    }

    #[test]
    fn transcricao_falhada_nao_vira_fila() {
        // The distinction the whole list depends on: a failure must not be
        // rendered as "still waiting", which is what a single status string
        // would have forced.
        let resumos = listar_do_nucleo(&payload("falhou", false, serde_json::json!("sem VRAM")));
        assert_eq!(resumos[0].situacao, Situacao::Falhou);
        assert_eq!(resumos[0].motivo_da_falha.as_deref(), Some("sem VRAM"));

        let resumos = listar_do_nucleo(&payload("na_fila", false, serde_json::Value::Null));
        assert_eq!(resumos[0].situacao, Situacao::NaFila);
        assert!(resumos[0].motivo_da_falha.is_none());
    }

    #[test]
    fn reprocessamento_continua_legivel() {
        // Availability and attempt are independent: a meeting being
        // re-transcribed is busy AND readable, and the interface has to be able
        // to say both.
        let resumos = listar_do_nucleo(&payload("em_execucao", true, serde_json::Value::Null));
        assert_eq!(resumos[0].situacao, Situacao::Transcrevendo);
        assert!(resumos[0].transcricao_disponivel);
    }

    #[test]
    fn pronta_quando_ha_revisao_e_nenhuma_tentativa() {
        let resumos = listar_do_nucleo(&payload("nenhuma", true, serde_json::Value::Null));
        assert_eq!(resumos[0].situacao, Situacao::Pronta);
        assert_eq!(resumos[0].uid, "ba28bcd99c3348feb407ed637a5b5f57");
        assert_eq!(resumos[0].duracao_ms, 9142);
        assert!(resumos[0].tem_audio);
    }

    #[test]
    fn sem_revisao_e_sem_tentativa_e_incompleta() {
        let resumos = listar_do_nucleo(&payload("nenhuma", false, serde_json::Value::Null));
        assert_eq!(resumos[0].situacao, Situacao::Incompleta);
    }

    #[test]
    fn notas_saem_da_exportacao_estruturada() {
        // Version 2 of the structured export always carries `notas`, empty list
        // included, and never mixes them into `segmentos`.
        let estruturado = serde_json::json!({
            "notas": [{
                "uid": "n1",
                "tipo": "resumo",
                "conteudo": "Decidiram adiar o deploy.",
                "autoria": {"tipo": "agente", "cliente": "claude-desktop"},
                "criada_em": "2026-09-22T03:50:00+00:00",
                "alterada_em": "2026-09-22T03:50:00+00:00"
            }]
        });
        let notas = notes_from(Some(&estruturado));
        assert_eq!(notas.len(), 1);
        assert_eq!(notas[0].autoria_tipo, "agente");
        assert_eq!(notas[0].autoria_cliente, "claude-desktop");
        assert!(notes_from(None).is_empty());
    }
}
