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
use std::time::Duration;

use serde::Serialize;

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

/// `voxvault --version`-equivalent probe, used by the preparation gate.
///
/// `--help` is the cheapest command the core has and touches no GPU runtime, so
/// it answers "is this environment usable at all" without paying for a model.
pub fn probe(timeout: Duration) -> CoreResult<()> {
    let mut child = build(&["--help"])?
        .spawn()
        .map_err(|err| CoreError::Execucao {
            detalhe: err.to_string(),
        })?;
    let deadline = std::time::Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) if status.success() => return Ok(()),
            Ok(Some(status)) => {
                return Err(CoreError::Falha {
                    codigo: status.code().unwrap_or(-1),
                    detalhe: "O executável do núcleo existe mas não responde a \
                              `--help`. O ambiente está incompleto."
                        .to_string(),
                })
            }
            Ok(None) => {
                if std::time::Instant::now() >= deadline {
                    let _ = child.kill();
                    return Err(CoreError::Execucao {
                        detalhe: format!(
                            "O núcleo não respondeu em {} s.",
                            timeout.as_secs()
                        ),
                    });
                }
                std::thread::sleep(Duration::from_millis(40));
            }
            Err(err) => {
                return Err(CoreError::Execucao {
                    detalhe: err.to_string(),
                })
            }
        }
    }
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
