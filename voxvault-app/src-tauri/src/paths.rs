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

/// Where the data directory lived by default before there was an installer.
/// A store already there stays where it is: nothing is ever moved, and nothing
/// is written to make it so.
pub const PASTA_ANTERIOR: &str = r"D:\VoxVault";

/// Per-user state directory, matching `config.user_state_dir()`.
///
/// Not under `%APPDATA%`, and the reason is the whole point: a process started
/// from inside a packaged Windows app -- the terminal of the Claude desktop app,
/// for one -- has what it writes under AppData redirected into that package's
/// private folder. A service started that way published its address where this
/// app could not read it, while still holding the machine-wide claim, so the app
/// could neither find it nor start another. The profile root is not redirected.
pub fn user_config_dir() -> PathBuf {
    if cfg!(windows) {
        home_dir().join(".voxvault")
    } else {
        home_dir().join(".config").join("voxvault")
    }
}

pub fn user_config_file() -> PathBuf {
    user_config_dir().join("config.json")
}

/// Where the configuration lived before it moved out of AppData. Read only as
/// a fallback: the core copies it over the first time it reads it.
fn legacy_user_config_file() -> Option<PathBuf> {
    env::var_os("APPDATA").map(|appdata| PathBuf::from(appdata).join("VoxVault").join("config.json"))
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

/// What the installer carries beside the executable: `<instalação>\recursos`,
/// with the core's sources in `nucleo`, `uv\uv.exe` and `preparo.json`.
///
/// Tauri's own resource directory on Windows is the executable's folder, so
/// this needs no handle to the app. A development build finds the same layout
/// where `tools/preparar-recursos.ps1` writes it, inside `src-tauri`.
pub fn recursos_dir() -> Option<PathBuf> {
    let instalado = env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|pasta| pasta.join("recursos")));
    if let Some(pasta) = instalado.filter(|p| p.join("preparo.json").is_file()) {
        return Some(pasta);
    }
    #[cfg(debug_assertions)]
    {
        let desenvolvimento = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("recursos");
        if desenvolvimento.join("preparo.json").is_file() {
            return Some(desenvolvimento);
        }
    }
    None
}

/// The places a core can come from, gathered so the choice between them is a
/// pure function the tests can drive branch by branch.
#[derive(Debug, Default)]
pub struct OrigensDoNucleo {
    /// `VOXVAULT_CORE_ROOT`, for pointing a build at another checkout.
    pub variavel: Option<PathBuf>,
    /// The checkout this build came from -- only ever filled in a debug build.
    pub checkout: Option<PathBuf>,
    /// The sources the installer put in `recursos\nucleo`.
    pub instalado: Option<PathBuf>,
}

fn e_um_nucleo(pasta: &Path) -> bool {
    pasta.join("pyproject.toml").is_file()
}

/// Which core this build runs: the environment variable, then -- in a debug
/// build only -- the checkout, then the installed sources.
///
/// The checkout is consulted before the installed copy because a debug build
/// has both: the build copies the bundle's resources beside the executable, and
/// that copy has no development environment of its own. A release build never
/// sees the checkout at all, so it cannot depend on where it was compiled.
pub fn resolver_nucleo(origens: OrigensDoNucleo) -> Option<PathBuf> {
    if let Some(pasta) = origens.variavel.filter(|p| p.is_dir()) {
        return Some(pasta);
    }
    if let Some(pasta) = origens.checkout.filter(|p| e_um_nucleo(p)) {
        return Some(pasta);
    }
    origens.instalado.filter(|p| e_um_nucleo(p))
}

/// Root of the Python core: see [`resolver_nucleo`].
pub fn core_root() -> Option<PathBuf> {
    resolver_nucleo(OrigensDoNucleo {
        variavel: env::var_os(ENV_CORE_ROOT).map(PathBuf::from),
        checkout: checkout_da_compilacao(),
        instalado: recursos_dir().map(|pasta| pasta.join("nucleo")),
    })
}

/// The repository this build was compiled from, found by walking up from the
/// executable and then from the crate. Compiled into debug builds only: a
/// published binary must not carry, let alone depend on, the path of whoever
/// built it.
fn checkout_da_compilacao() -> Option<PathBuf> {
    #[cfg(debug_assertions)]
    {
        if let Some(found) = env::current_exe().ok().and_then(|exe| walk_up_for_core(&exe)) {
            return Some(found);
        }
        walk_up_for_core(Path::new(env!("CARGO_MANIFEST_DIR")))
    }
    #[cfg(not(debug_assertions))]
    {
        None
    }
}

#[cfg(debug_assertions)]
fn walk_up_for_core(start: &Path) -> Option<PathBuf> {
    let mut cursor: Option<&Path> = Some(start);
    while let Some(dir) = cursor {
        let candidate = dir.join("voxvault-core");
        if candidate.join("pyproject.toml").is_file() {
            return Some(candidate);
        }
        cursor = dir.parent();
    }
    None
}

/// Everything the preparation creates for this user: the environment, the
/// interpreter, uv's cache and the stamp. Outside the installation folder,
/// which every update replaces, and outside AppData, which a process started
/// from a packaged app sees redirected.
pub fn runtime_dir() -> PathBuf {
    user_config_dir().join("runtime")
}

pub fn ambiente_dir() -> PathBuf {
    runtime_dir().join("ambiente")
}

/// The executable to run: the prepared environment's, when there is one; in a
/// debug build, the checkout's own development environment otherwise.
pub fn resolver_executavel(
    ambiente: &Path,
    nucleo: Option<&Path>,
    depuracao: bool,
) -> Option<PathBuf> {
    let preparado = ambiente.join("Scripts").join("voxvault.exe");
    if preparado.is_file() {
        return Some(preparado);
    }
    if !depuracao {
        return None;
    }
    let desenvolvimento = nucleo?.join(".venv").join("Scripts").join("voxvault.exe");
    desenvolvimento.is_file().then_some(desenvolvimento)
}

/// The core's command line executable.
pub fn core_executable() -> Option<PathBuf> {
    resolver_executavel(&ambiente_dir(), core_root().as_deref(), cfg!(debug_assertions))
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
    let current = user_config_file();
    let file = if current.is_file() {
        Some(current)
    } else {
        legacy_user_config_file().filter(|legacy| legacy.is_file())
    };
    if let Some(text) = file.and_then(|path| std::fs::read_to_string(path).ok()) {
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(dir) = value.get("data_dir").and_then(|v| v.as_str()) {
                let dir = dir.trim();
                if !dir.is_empty() {
                    return (PathBuf::from(dir), DataDirSource::ConfigFile);
                }
            }
        }
    }
    pasta_padrao(Path::new(PASTA_ANTERIOR), &home_dir())
}

/// The data directory when nothing configures one, mirroring the core's
/// `default_data_dir()`: `VoxVault` in the user's profile, unless a store
/// already exists at the previous default -- then that one, so meetings do not
/// vanish from the library because a newer build prefers another place.
pub fn pasta_padrao(anterior: &Path, perfil: &Path) -> (PathBuf, DataDirSource) {
    if anterior.join("voxvault.db").is_file() {
        (anterior.to_path_buf(), DataDirSource::PadraoAnterior)
    } else {
        (perfil.join("VoxVault"), DataDirSource::BuiltInDefault)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DataDirSource {
    Environment,
    ConfigFile,
    BuiltInDefault,
    PadraoAnterior,
}

impl DataDirSource {
    /// pt-BR wording matching `config.py`'s own vocabulary for provenance.
    pub fn label(self) -> &'static str {
        match self {
            DataDirSource::Environment => "variável de ambiente VOXVAULT_DATA_DIR",
            DataDirSource::ConfigFile => "arquivo de configuração",
            DataDirSource::BuiltInDefault => "padrão embutido",
            DataDirSource::PadraoAnterior => "padrão anterior (dados existentes)",
        }
    }

    /// Whether the settings screen may offer to change it. A value imposed by
    /// the environment outranks the configuration file, so writing the file
    /// would leave the user operating on the old storage with no signal.
    pub fn changeable(self) -> bool {
        !matches!(self, DataDirSource::Environment)
    }
}

/// `NO_PROXY` with loopback added, for every core process this app starts.
///
/// The core talks to its own service over 127.0.0.1, and Python's HTTP client
/// hands even that to a proxy configured for the machine -- which refuses it,
/// so the command reports a service that is not there. The core no longer
/// does that, but an older core still in place during an update does, and the
/// app is what asks it to stop.
pub fn sem_proxy_no_loopback() -> String {
    let atual = env::var("NO_PROXY")
        .or_else(|_| env::var("no_proxy"))
        .unwrap_or_default();
    let mut itens: Vec<String> = atual
        .split(',')
        .map(|item| item.trim().to_string())
        .filter(|item| !item.is_empty())
        .collect();
    for local in ["127.0.0.1", "localhost", "::1"] {
        if !itens.iter().any(|item| item.eq_ignore_ascii_case(local)) {
            itens.push(local.to_string());
        }
    }
    itens.join(",")
}

pub fn recordings_dir(data_dir: &Path) -> PathBuf {
    data_dir.join("recordings")
}

#[cfg(test)]
mod testes {
    use super::*;

    fn pasta(nome: &str) -> PathBuf {
        let pasta = env::temp_dir().join(format!(
            "voxvault-paths-{nome}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&pasta).unwrap();
        pasta
    }

    fn nucleo_em(pasta: &Path) -> PathBuf {
        std::fs::create_dir_all(pasta).unwrap();
        std::fs::write(pasta.join("pyproject.toml"), "[project]\n").unwrap();
        pasta.to_path_buf()
    }

    fn executavel_em(pasta: &Path) -> PathBuf {
        let scripts = pasta.join("Scripts");
        std::fs::create_dir_all(&scripts).unwrap();
        let exe = scripts.join("voxvault.exe");
        std::fs::write(&exe, b"").unwrap();
        exe
    }

    #[test]
    fn o_loopback_nunca_passa_pelo_proxy() {
        let valor = sem_proxy_no_loopback();
        for local in ["127.0.0.1", "localhost", "::1"] {
            assert!(valor.split(',').any(|item| item == local), "{valor}");
        }
    }

    #[test]
    fn a_variavel_de_ambiente_prevalece() {
        let raiz = pasta("variavel");
        let outro = raiz.join("outro-checkout");
        std::fs::create_dir_all(&outro).unwrap();
        let achado = resolver_nucleo(OrigensDoNucleo {
            variavel: Some(outro.clone()),
            checkout: Some(nucleo_em(&raiz.join("checkout"))),
            instalado: Some(nucleo_em(&raiz.join("recursos").join("nucleo"))),
        });
        assert_eq!(achado, Some(outro));
    }

    #[test]
    fn uma_variavel_que_nao_existe_nao_esconde_as_outras() {
        let raiz = pasta("variavel-invalida");
        let instalado = nucleo_em(&raiz.join("recursos").join("nucleo"));
        let achado = resolver_nucleo(OrigensDoNucleo {
            variavel: Some(raiz.join("nao-existe")),
            checkout: None,
            instalado: Some(instalado.clone()),
        });
        assert_eq!(achado, Some(instalado));
    }

    #[test]
    fn sem_checkout_o_build_publicado_usa_o_nucleo_instalado() {
        let raiz = pasta("instalado");
        let instalado = nucleo_em(&raiz.join("recursos").join("nucleo"));
        let achado = resolver_nucleo(OrigensDoNucleo {
            instalado: Some(instalado.clone()),
            ..OrigensDoNucleo::default()
        });
        assert_eq!(achado, Some(instalado));
    }

    #[test]
    fn na_depuracao_o_checkout_vem_antes_da_copia_dos_recursos() {
        let raiz = pasta("depuracao");
        let checkout = nucleo_em(&raiz.join("voxvault-core"));
        let achado = resolver_nucleo(OrigensDoNucleo {
            variavel: None,
            checkout: Some(checkout.clone()),
            instalado: Some(nucleo_em(&raiz.join("recursos").join("nucleo"))),
        });
        assert_eq!(achado, Some(checkout));
    }

    #[test]
    fn nada_encontrado_e_nenhum_nucleo() {
        let raiz = pasta("nada");
        let achado = resolver_nucleo(OrigensDoNucleo {
            variavel: None,
            checkout: Some(raiz.join("sem-pyproject")),
            instalado: Some(raiz.join("recursos").join("nucleo")),
        });
        assert_eq!(achado, None);
    }

    #[test]
    fn o_ambiente_preparado_prevalece_sobre_o_de_desenvolvimento() {
        let raiz = pasta("executavel");
        let nucleo = nucleo_em(&raiz.join("voxvault-core"));
        executavel_em(&nucleo.join(".venv"));
        let preparado = executavel_em(&raiz.join("runtime").join("ambiente"));

        let achado = resolver_executavel(&raiz.join("runtime").join("ambiente"), Some(&nucleo), true);

        assert_eq!(achado, Some(preparado));
    }

    #[test]
    fn so_a_depuracao_recorre_ao_ambiente_do_checkout() {
        let raiz = pasta("executavel-dev");
        let nucleo = nucleo_em(&raiz.join("voxvault-core"));
        let desenvolvimento = executavel_em(&nucleo.join(".venv"));
        let sem_preparo = raiz.join("runtime").join("ambiente");

        assert_eq!(resolver_executavel(&sem_preparo, Some(&nucleo), true), Some(desenvolvimento));
        assert_eq!(resolver_executavel(&sem_preparo, Some(&nucleo), false), None);
    }

    #[test]
    fn dados_na_pasta_anterior_continuam_la() {
        let raiz = pasta("anterior");
        let anterior = raiz.join("D-VoxVault");
        std::fs::create_dir_all(&anterior).unwrap();
        std::fs::write(anterior.join("voxvault.db"), b"").unwrap();

        let (caminho, fonte) = pasta_padrao(&anterior, &raiz.join("perfil"));

        assert_eq!(caminho, anterior);
        assert_eq!(fonte, DataDirSource::PadraoAnterior);
        assert_eq!(fonte.label(), "padrão anterior (dados existentes)");
        assert!(fonte.changeable());
    }

    #[test]
    fn sem_dados_anteriores_o_padrao_fica_no_perfil() {
        let raiz = pasta("perfil");
        let anterior = raiz.join("D-VoxVault");
        // A folder without a store is not a store: the default is the profile.
        std::fs::create_dir_all(&anterior).unwrap();

        let (caminho, fonte) = pasta_padrao(&anterior, &raiz.join("perfil"));

        assert_eq!(caminho, raiz.join("perfil").join("VoxVault"));
        assert_eq!(fonte, DataDirSource::BuiltInDefault);
    }
}
