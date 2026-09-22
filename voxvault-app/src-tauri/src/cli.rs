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
//! purpose. Where a command has no `--json` yet, it returns [`CoreError::NoJsonFlag`]
//! naming the flag, and the interface says so. Scraping the human-readable
//! output would give the app a second, silently drifting copy of the core's
//! vocabulary, which is the failure the project's own design forbids.

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
    /// The command exists but has no machine-readable output yet.
    SemJson {
        comando: String,
        sinalizador: String,
        detalhe: String,
    },
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
            CoreError::SemJson { detalhe, .. } => detalhe.clone(),
        }
    }

    /// Declares that a command the app needs has no machine-readable output.
    ///
    /// Stated as data rather than as a thrown string so the interface can show
    /// the exact flag the core still has to grow, instead of an apology.
    pub fn sem_json(comando: &str, sinalizador: &str) -> Self {
        CoreError::SemJson {
            comando: comando.to_string(),
            sinalizador: sinalizador.to_string(),
            detalhe: format!(
                "O comando `voxvault {comando}` ainda não tem saída legível por \
                 máquina. Esta tela passa a funcionar assim que o núcleo publicar \
                 `{sinalizador}`. Até lá o aplicativo não interpreta o texto humano \
                 do comando, para não carregar uma cópia divergente do vocabulário \
                 do núcleo."
            ),
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

/// `voxvault show <uid> --json` -- the one read path the core already exposes
/// in machine-readable form.
pub fn show(uid: &str) -> CoreResult<serde_json::Value> {
    run_json(&["show", uid, "--json"])
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

/// `voxvault config CAMPO=VALOR ...` -- writes the shared configuration file,
/// which is what makes a setting visible to the other surfaces and not only to
/// this window.
pub fn config_set(assignments: &[String]) -> CoreResult<String> {
    let mut args: Vec<&str> = vec!["config"];
    args.extend(assignments.iter().map(String::as_str));
    run(&args)
}
