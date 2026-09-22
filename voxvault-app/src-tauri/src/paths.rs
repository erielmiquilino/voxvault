//! Where the app finds the core, its configuration and its data.
//!
//! Nothing here derives a path from the current working directory. The core
//! makes the same promise in `config.py`, for the same reason: an MCP server
//! launched by an agent client, the command line and this app must all resolve
//! the same store, and a working-directory-relative path is exactly how two
//! surfaces end up pointed at two different databases.

use std::env;
use std::path::{Path, PathBuf};

/// Environment variable that lets a developer point the app at another checkout
/// of the core without rebuilding.
const ENV_CORE_ROOT: &str = "VOXVAULT_CORE_ROOT";
/// The core honours `VOXVAULT_DATA_DIR`; the app must notice it because a data
/// directory imposed by the environment outranks the configuration file and the
/// settings screen must say so instead of pretending it can change it.
const ENV_DATA_DIR: &str = "VOXVAULT_DATA_DIR";

const DEFAULT_DATA_DIR: &str = r"D:\VoxVault";

/// Per-user configuration directory, matching `config.user_config_path()`.
pub fn user_config_dir() -> PathBuf {
    match env::var_os("APPDATA") {
        Some(appdata) => PathBuf::from(appdata).join("VoxVault"),
        None => home_dir().join(".config").join("voxvault"),
    }
}

pub fn user_config_file() -> PathBuf {
    user_config_dir().join("config.json")
}

/// The user-scope rendezvous point where the resident service publishes the
/// address and the per-start secret.
///
/// It sits beside the configuration file on purpose: every surface can find it
/// without having been the one that started the service, which is the whole
/// requirement -- the app connects to a service a command line may have started.
pub fn rendezvous_file() -> PathBuf {
    user_config_dir().join("servico.json")
}

fn home_dir() -> PathBuf {
    env::var_os("USERPROFILE")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Root of the Python core checkout.
///
/// Resolution order: explicit environment variable, then the sibling directory
/// of the app inside the repository. The sibling walk exists because the app is
/// built and run from this machine's checkout; if distribution ever changes,
/// this is one of the two places that has to be revisited.
pub fn core_root() -> Option<PathBuf> {
    if let Some(raw) = env::var_os(ENV_CORE_ROOT) {
        let path = PathBuf::from(raw);
        if path.is_dir() {
            return Some(path);
        }
    }
    let exe = env::current_exe().ok()?;
    let mut cursor: Option<&Path> = Some(exe.as_path());
    while let Some(dir) = cursor {
        let candidate = dir.join("voxvault-core");
        if candidate.join("pyproject.toml").is_file() {
            return Some(candidate);
        }
        cursor = dir.parent();
    }
    None
}

/// The core's command line executable inside its managed virtual environment.
pub fn core_executable() -> Option<PathBuf> {
    let candidate = core_root()?
        .join(".venv")
        .join("Scripts")
        .join("voxvault.exe");
    candidate.is_file().then_some(candidate)
}

/// Whether the core's environment looks prepared. Deliberately a file check
/// and not a successful run: a run costs process startup, and this answer is
/// needed before the window opens.
pub fn core_environment_ready() -> bool {
    core_executable().is_some()
}

/// Effective data directory together with the source that imposed it.
///
/// The settings screen needs the source, not only the value: a directory coming
/// from the environment cannot be changed from inside the app, and offering the
/// change anyway would write a setting that silently never takes effect.
pub fn effective_data_dir() -> (PathBuf, DataDirSource) {
    if let Some(raw) = env::var_os(ENV_DATA_DIR) {
        let text = raw.to_string_lossy().trim().to_string();
        if !text.is_empty() {
            return (PathBuf::from(text), DataDirSource::Environment);
        }
    }
    if let Ok(text) = std::fs::read_to_string(user_config_file()) {
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(dir) = value.get("data_dir").and_then(|v| v.as_str()) {
                let dir = dir.trim();
                if !dir.is_empty() {
                    return (PathBuf::from(dir), DataDirSource::ConfigFile);
                }
            }
        }
    }
    (PathBuf::from(DEFAULT_DATA_DIR), DataDirSource::BuiltInDefault)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DataDirSource {
    Environment,
    ConfigFile,
    BuiltInDefault,
}

impl DataDirSource {
    /// pt-BR wording matching `config.py`'s own vocabulary for provenance.
    pub fn label(self) -> &'static str {
        match self {
            DataDirSource::Environment => "variável de ambiente VOXVAULT_DATA_DIR",
            DataDirSource::ConfigFile => "arquivo de configuração",
            DataDirSource::BuiltInDefault => "padrão embutido",
        }
    }

    /// Whether the settings screen may offer to change it. A value imposed by
    /// the environment outranks the configuration file, so writing the file
    /// would leave the user operating on the old storage with no signal.
    pub fn changeable(self) -> bool {
        !matches!(self, DataDirSource::Environment)
    }
}

pub fn recordings_dir(data_dir: &Path) -> PathBuf {
    data_dir.join("recordings")
}
