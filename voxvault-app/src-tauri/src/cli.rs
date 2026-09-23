//! Invoking the core's command line.
//!
//! Every call here costs a Python interpreter start, which the core measures at
//! roughly 220 ms for the cheapest command. That single fact sets the rule this
//! module exists to enforce: the app never polls the command line. Calls happen
//! on an explicit user action, or on a slow refresh that is suspended entirely
//! while a recording is running. Continuous state -- recording, levels, queue
//! progress -- belongs to the resident service's push channel, never here.
//!
//! The second rule is that this module only consumes JSON the core produces on
//! purpose. It never scrapes human-readable output: that would give the app a
//! second, silently drifting copy of the core's vocabulary, which the project's
//! own design forbids. Where a command prints prose rather than JSON -- `import`,
//! `rename`, `remove-audio`, `mcp` -- the app forwards that prose to the user
//! verbatim instead of trying to interpret it.

use std::path::PathBuf;
use std::process::{Command, Output, Stdio};

use serde::{Deserialize, Serialize};

use crate::paths;

/// Keeps a console window from flashing on every invocation. Also keeps a
/// `conhost.exe` out of the measured process tree.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "tipo", rename_all = "snake_case")]
pub enum CoreError {
    /// The managed environment has not been prepared yet.
    NaoPreparado { detalhe: String },
    /// The process could not be started at all.
    Execucao { detalhe: String },
    /// The command ran and reported a failure.
    Falha { codigo: i32, detalhe: String },
    /// The command succeeded but did not produce the JSON shape expected.
    Resposta { detalhe: String },
}

impl CoreError {
    pub fn mensagem(&self) -> String {
        match self {
            CoreError::NaoPreparado { detalhe } => detalhe.clone(),
            CoreError::Execucao { detalhe } => {
                format!("Não foi possível executar o núcleo: {detalhe}")
            }
            CoreError::Falha { detalhe, .. } => detalhe.clone(),
            CoreError::Resposta { detalhe } => detalhe.clone(),
        }
    }
}

pub type CoreResult<T> = Result<T, CoreError>;

fn executable() -> CoreResult<PathBuf> {
    paths::core_executable().ok_or_else(|| CoreError::NaoPreparado {
        detalhe: "O ambiente de execução do núcleo ainda não foi preparado."
            .to_string(),
    })
}

fn build(args: &[&str]) -> CoreResult<Command> {
    let exe = executable()?;
    let mut command = Command::new(exe);
    command
        .args(args)
        // A Python configured for something else on this machine must not
        // leak into the core's own interpreter.
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    Ok(command)
}

fn finish(output: Output) -> CoreResult<String> {
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    if output.status.success() {
        return Ok(stdout);
    }
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let detalhe = if stderr.is_empty() {
        stdout.trim().to_string()
    } else {
        stderr
    };
    Err(CoreError::Falha {
        codigo: output.status.code().unwrap_or(-1),
        detalhe: if detalhe.is_empty() {
            "O núcleo terminou com erro e não explicou a causa.".to_string()
        } else {
            detalhe
        },
    })
}

/// Run a command whose exit status carries a result, not only success.
///
/// `voxvault delete` exits with 2 when some meeting was refused and still
/// prints the full JSON report, so 2 is an answer to read and not a failure.
fn run_accepting(args: &[&str], accepted: &[i32]) -> CoreResult<String> {
    let output = build(args)?
        .output()
        .map_err(|err| CoreError::Execucao {
            detalhe: err.to_string(),
        })?;
    match output.status.code() {
        Some(code) if accepted.contains(&code) => {
            Ok(String::from_utf8_lossy(&output.stdout).into_owned())
        }
        _ => finish(output),
    }
}

/// Run a command and return its raw stdout.
pub fn run(args: &[&str]) -> CoreResult<String> {
    let output = build(args)?
        .output()
        .map_err(|err| CoreError::Execucao {
            detalhe: err.to_string(),
        })?;
    finish(output)
}

/// Run a command whose output is JSON, and parse it.
pub fn run_json(args: &[&str]) -> CoreResult<serde_json::Value> {
    let text = run(args)?;
    serde_json::from_str(&text).map_err(|err| CoreError::Resposta {
        detalhe: format!(
            "O núcleo respondeu algo que não é JSON válido ({err}). \
             Rode `voxvault {}` num terminal para ver a saída crua.",
            args.join(" ")
        ),
    })
}

// -- the commands the app actually uses -----------------------------------

/// `voxvault show <uid> --json`.
pub fn show(uid: &str) -> CoreResult<serde_json::Value> {
    run_json(&["show", uid, "--json"])
}

/// `voxvault list --json` -- the authority on what exists and on what state
/// each meeting is in.
///
/// It reports availability and attempt separately (`transcricao_disponivel`
/// against `estado_da_tentativa`), which is what lets the interface show a
/// meeting being reprocessed as still readable, and a failed one as failed
/// rather than as perpetually queued.
pub fn list(limit: u32) -> CoreResult<serde_json::Value> {
    let limite = limit.to_string();
    run_json(&["list", "--json", "-n", &limite])
}

/// `voxvault search --json --scope ...`.
pub fn search(termo: &str, escopo: &str, limite: u32) -> CoreResult<serde_json::Value> {
    let n = limite.to_string();
    run_json(&["search", "--json", "--scope", escopo, termo, "-n", &n])
}

pub fn doctor() -> CoreResult<serde_json::Value> {
    // Exit code 1 means "a prerequisite failed", which is a result and not an
    // error: the report it prints is exactly what the settings screen shows.
    match run_json(&["doctor", "--json"]) {
        Ok(valor) => Ok(valor),
        Err(CoreError::Falha { detalhe, .. }) => {
            serde_json::from_str(&detalhe).map_err(|_| CoreError::Falha {
                codigo: 1,
                detalhe,
            })
        }
        Err(outro) => Err(outro),
    }
}

pub fn devices() -> CoreResult<serde_json::Value> {
    run_json(&["devices", "--json"])
}

pub fn config_read() -> CoreResult<serde_json::Value> {
    run_json(&["config", "--json"])
}

pub fn rename(uid: &str, titulo: &str) -> CoreResult<String> {
    run(&["rename", uid, "--title", titulo])
}

/// `voxvault remove-audio <uid>`.
///
/// Without `--yes` the core only describes what it would do, which is what the
/// interface shows inside the confirmation before anything is destroyed.
pub fn remove_audio(uid: &str, confirmado: bool) -> CoreResult<String> {
    if confirmado {
        run(&["remove-audio", uid, "--yes"])
    } else {
        run(&["remove-audio", uid])
    }
}

/// One file a deletion removes, relative to the meeting's directory.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ArquivoDaExclusao {
    pub caminho: String,
    pub bytes: u64,
}

/// One meeting of a `voxvault delete --json` report.
///
/// `motivo` is the sentence to show a person and `causa` the same refusal as
/// one word to branch on; both are empty when nothing refuses. `resultado`
/// only exists once the deletion was confirmed.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ItemDaExclusao {
    pub uid: String,
    pub titulo: String,
    pub inicio: Option<String>,
    pub duracao_ms: i64,
    pub revisoes: u32,
    pub notas: u32,
    pub diretorio: String,
    pub arquivos: Vec<ArquivoDaExclusao>,
    pub bytes: u64,
    pub motivo: String,
    pub causa: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resultado: Option<String>,
}

/// What the deleted, or to-be-deleted, meetings add up to.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TotalDaExclusao {
    pub reunioes: u32,
    pub duracao_ms: i64,
    pub revisoes: u32,
    pub notas: u32,
    pub bytes: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Exclusao {
    pub itens: Vec<ItemDaExclusao>,
    pub total: TotalDaExclusao,
}

/// `voxvault delete <uid>... --json [--yes]`.
///
/// Without confirmation the core only describes what it would remove, and
/// that description -- sums included -- is what the confirmation shows, so the
/// number the person agrees to is the number the core then deletes.
pub fn delete(uids: &[String], confirmar: bool) -> CoreResult<Exclusao> {
    let mut args: Vec<&str> = vec!["delete"];
    args.extend(uids.iter().map(String::as_str));
    args.push("--json");
    if confirmar {
        args.push("--yes");
    }
    let text = run_accepting(&args, &[0, 2])?;
    parse_exclusao(&text)
}

fn parse_exclusao(text: &str) -> CoreResult<Exclusao> {
    serde_json::from_str(text).map_err(|err| CoreError::Resposta {
        detalhe: format!(
            "O núcleo respondeu à exclusão com algo fora do formato esperado \
             ({err}). Rode `voxvault delete <reunião> --json` num terminal para \
             ver a saída crua."
        ),
    })
}

/// `voxvault import <file>`; the core prints a confirmation, not JSON, so the
/// result is only "it worked or it did not".
pub fn import(file: &str, title: &str) -> CoreResult<String> {
    let mut args: Vec<&str> = vec!["import", file];
    if !title.is_empty() {
        args.push("--title");
        args.push(title);
    }
    run(&args)
}

/// `voxvault reprocess <uid>`.
pub fn reprocess(uid: &str) -> CoreResult<String> {
    run(&["reprocess", uid])
}

/// `voxvault export <uid>` -- prints the regenerated paths, one per line.
pub fn export(uid: &str) -> CoreResult<Vec<String>> {
    let text = run(&["export", uid])?;
    Ok(text
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(str::to_string)
        .collect())
}

// Notes: the write paths exist on the command line and each of them ends by
// regenerating the meeting's exports, which is what makes a note written here
// readable from `transcricao.json` immediately afterwards. Reading therefore
// goes through the export, not through a second listing command.

pub fn notes_add(uid: &str, tipo: &str, conteudo: &str) -> CoreResult<String> {
    run(&["notes", "add", uid, "--type", tipo, "--content", conteudo])
}

pub fn notes_update(id: &str, conteudo: &str) -> CoreResult<String> {
    run(&["notes", "update", id, "--content", conteudo])
}

pub fn notes_remove(id: &str) -> CoreResult<String> {
    run(&["notes", "remove", id])
}

/// `voxvault mcp` -- the core's own report on whether the MCP server is
/// registered in each agent client, plus the snippet to add.
///
/// Presented to the user verbatim. Building the snippet in the app would mean
/// maintaining a second copy of how the server is launched, and a snippet that
/// drifts from the core's is worse than none: it fails at the client, not here.
pub fn mcp_status() -> CoreResult<String> {
    run(&["mcp"])
}

/// `voxvault mcp --apply` -- writes the registration, after copying the client's
/// current configuration file.
pub fn mcp_apply() -> CoreResult<String> {
    run(&["mcp", "--apply"])
}

/// `voxvault config CAMPO=VALOR ...` -- writes the shared configuration file,
/// which is what makes a setting visible to the other surfaces and not only to
/// this window.
pub fn config_set(assignments: &[String]) -> CoreResult<String> {
    let mut args: Vec<&str> = vec!["config"];
    args.extend(assignments.iter().map(String::as_str));
    run(&args)
}

#[cfg(test)]
mod testes {
    use super::*;

    /// Captured from the core, not written by hand: `voxvault delete 7f3a
    /// c0ffee --yes --json` over two meetings, the second one being
    /// transcribed at that moment.
    const SAIDA_REAL: &str = include_str!("../tests/fixtures/exclusao-com-recusa.json");

    #[test]
    fn uma_saida_real_com_uma_recusa_vira_tipos() {
        let exclusao = parse_exclusao(SAIDA_REAL).expect("o formato do núcleo mudou");

        assert_eq!(exclusao.itens.len(), 2);
        let (excluida, recusada) = (&exclusao.itens[0], &exclusao.itens[1]);

        assert_eq!(excluida.resultado.as_deref(), Some("excluida"));
        assert_eq!(excluida.titulo, "Planejamento do trimestre");
        assert_eq!(excluida.causa, "");
        assert_eq!(excluida.arquivos.len(), 2);
        assert_eq!(
            excluida.bytes,
            excluida.arquivos.iter().map(|a| a.bytes).sum::<u64>()
        );

        assert_eq!(recusada.resultado.as_deref(), Some("recusada"));
        assert_eq!(recusada.causa, "transcrevendo");
        assert!(recusada.motivo.contains("em execucao"));

        // The total counts only what was deleted.
        assert_eq!(exclusao.total.reunioes, 1);
        assert_eq!(exclusao.total.bytes, excluida.bytes);
        assert_eq!(exclusao.total.duracao_ms, excluida.duracao_ms);
    }

    #[test]
    fn uma_previa_nao_traz_resultado() {
        let previa = r#"{"itens":[{"uid":"abc","titulo":"","inicio":null,
            "duracao_ms":0,"revisoes":0,"notas":0,"diretorio":"","arquivos":[],
            "bytes":0,"motivo":"Nenhuma reuniao comeca com 'abc'.",
            "causa":"identificador"}],
            "total":{"reunioes":0,"duracao_ms":0,"revisoes":0,"notas":0,"bytes":0}}"#;
        let exclusao = parse_exclusao(previa).unwrap();
        assert_eq!(exclusao.itens[0].resultado, None);
        assert_eq!(exclusao.itens[0].inicio, None);
        assert_eq!(exclusao.itens[0].causa, "identificador");
    }
}
