//! `voxvault` in any terminal the user opens -- and for any agent working in
//! one.
//!
//! Not the environment's Scripts folder on the PATH: besides the two entry
//! points it holds a python.exe and some thirty tools of the dependencies
//! (hf, httpx, uvicorn, tqdm...) that would shadow the user's own. The two are
//! copied to a folder of their own instead -- uv's launchers carry the
//! interpreter's absolute path and run from anywhere -- and only that folder
//! goes on the PATH: this user's, with no elevation, at the end, so it shadows
//! nothing. Refreshed at every successful preparation, which is also what
//! brings it to an installation that predates it. The uninstaller takes it
//! back out through [`ARGUMENTO_REMOVER`].

use std::path::{Path, PathBuf};

use crate::registro;

/// What the uninstaller runs this executable with.
pub const ARGUMENTO_REMOVER: &str = "--remover-do-path";
const PONTOS_DE_ENTRADA: [&str; 2] = ["voxvault.exe", "voxvault-mcp.exe"];
/// The user's environment, where Windows keeps this user's PATH.
const AMBIENTE: &str = "Environment";

/// `%USERPROFILE%\.voxvault\bin`.
pub fn pasta() -> PathBuf {
    crate::paths::user_config_dir().join("bin")
}

/// Refresh the copies from the prepared environment's `Scripts` and put the
/// folder on the PATH if it is not there yet.
pub fn integrar(scripts: &Path) -> Result<(), String> {
    let destino = pasta();
    std::fs::create_dir_all(&destino).map_err(|e| format!("{}: {e}", destino.display()))?;
    for nome in PONTOS_DE_ENTRADA {
        let origem = scripts.join(nome);
        if origem.is_file() {
            copiar(&origem, &destino.join(nome))?;
        }
    }
    let texto = destino.display().to_string();
    let (atual, tipo) = registro::ler(AMBIENTE, Some("Path"))?
        .unwrap_or((String::new(), registro::TEXTO_EXPANSIVEL));
    if let Some(novo) = com_a_pasta(&atual, &texto) {
        registro::escrever(AMBIENTE, Some("Path"), &novo, tipo)?;
        registro::avisar_mudanca_de_ambiente();
    }
    Ok(())
}

/// Take the folder out of the PATH, wherever it is in it.
pub fn remover() -> Result<(), String> {
    let texto = pasta().display().to_string();
    if let Some((atual, tipo)) = registro::ler(AMBIENTE, Some("Path"))? {
        if let Some(novo) = sem_a_pasta(&atual, &texto) {
            registro::escrever(AMBIENTE, Some("Path"), &novo, tipo)?;
            registro::avisar_mudanca_de_ambiente();
        }
    }
    Ok(())
}

/// A copy a running `voxvault.exe` cannot block: a launcher in use cannot be
/// overwritten or deleted, but it can be moved aside -- and the one moved
/// aside goes at the next preparation, when nothing runs it any more.
fn copiar(origem: &Path, destino: &Path) -> Result<(), String> {
    let antigo = destino.with_extension("exe.antigo");
    let _ = std::fs::remove_file(&antigo);
    if destino.exists() && std::fs::remove_file(destino).is_err() {
        let _ = std::fs::rename(destino, &antigo);
    }
    std::fs::copy(origem, destino)
        .map(|_| ())
        .map_err(|e| format!("{}: {e}", destino.display()))
}

/// Folders compare as Windows compares them: without case, and a trailing
/// separator makes no difference.
fn mesma(entrada: &str, pasta: &str) -> bool {
    let limpar = |s: &str| s.trim().trim_end_matches(['\\', '/']).to_lowercase();
    !entrada.trim().is_empty() && limpar(entrada) == limpar(pasta)
}

/// The PATH with the folder added at the end, or `None` when it is already in.
pub fn com_a_pasta(path: &str, pasta: &str) -> Option<String> {
    if path.split(';').any(|entrada| mesma(entrada, pasta)) {
        return None;
    }
    let base = path.trim_end_matches(';');
    Some(if base.is_empty() {
        pasta.to_string()
    } else {
        format!("{base};{pasta}")
    })
}

/// The PATH without the folder, every other entry kept as it was and in its
/// place; `None` when the folder was not in it.
pub fn sem_a_pasta(path: &str, pasta: &str) -> Option<String> {
    let entradas: Vec<&str> = path.split(';').collect();
    let restantes: Vec<&str> = entradas
        .iter()
        .copied()
        .filter(|entrada| !mesma(entrada, pasta))
        .collect();
    (restantes.len() != entradas.len()).then(|| restantes.join(";"))
}

#[cfg(test)]
mod testes {
    use super::*;

    const BIN: &str = r"C:\Users\eriel\.voxvault\bin";

    #[test]
    fn a_pasta_entra_no_fim_e_uma_vez_so() {
        let path = r"%USERPROFILE%\AppData\Local\Microsoft\WindowsApps;C:\Tools";
        let novo = com_a_pasta(path, BIN).unwrap();
        assert_eq!(novo, format!("{path};{BIN}"), "nada antes dela muda de lugar");
        assert_eq!(com_a_pasta(&novo, BIN), None, "entrou duas vezes");
    }

    #[test]
    fn ja_presente_de_outro_jeito_conta_como_presente() {
        assert_eq!(com_a_pasta(r"C:\USERS\ERIEL\.VOXVAULT\BIN\;C:\Tools", BIN), None);
    }

    #[test]
    fn um_path_vazio_ou_terminado_em_separador_fica_limpo() {
        assert_eq!(com_a_pasta("", BIN).as_deref(), Some(BIN));
        assert_eq!(com_a_pasta(r"C:\Tools;", BIN), Some(format!(r"C:\Tools;{BIN}")));
    }

    #[test]
    fn remover_tira_so_a_pasta_e_deixa_o_resto_como_estava() {
        let path = format!(r"C:\Tools;{BIN};%USERPROFILE%\bin;;C:\Outra;{BIN}\");
        assert_eq!(
            sem_a_pasta(&path, BIN).as_deref(),
            Some(r"C:\Tools;%USERPROFILE%\bin;;C:\Outra"),
        );
        assert_eq!(sem_a_pasta(r"C:\Tools;C:\Outra", BIN), None, "mexeu num PATH sem ela");
    }
}
