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
/// folder on the PATH if it is not there yet. A copy that cannot be refreshed
/// does not keep the folder off the PATH: it is reported, and the next
/// preparation tries again.
pub fn integrar(scripts: &Path) -> Result<(), String> {
    let destino = pasta();
    std::fs::create_dir_all(&destino).map_err(|e| format!("{}: {e}", destino.display()))?;
    let falhas: Vec<String> = PONTOS_DE_ENTRADA
        .iter()
        .map(|nome| (scripts.join(nome), destino.join(nome)))
        .filter(|(origem, _)| origem.is_file())
        .filter_map(|(origem, copia)| copiar(&origem, &copia).err())
        .collect();
    let texto = destino.display().to_string();
    let (atual, tipo) = registro::ler(AMBIENTE, Some("Path"))?
        .unwrap_or((String::new(), registro::TEXTO_EXPANSIVEL));
    if let Some(novo) = com_a_pasta(&atual, &texto) {
        registro::escrever(AMBIENTE, Some("Path"), &novo, tipo)?;
        registro::avisar_mudanca_de_ambiente();
    }
    if falhas.is_empty() {
        Ok(())
    } else {
        Err(falhas.join("; "))
    }
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

/// One copy, left alone when it already has the same bytes. uv writes the
/// same launcher at every preparation -- the interpreter's path and the entry
/// point are all it carries -- and a launcher that is running, like an MCP
/// server an agent keeps open, holds its own file open: until it exits, the
/// file can be neither replaced nor moved aside.
fn copiar(origem: &Path, destino: &Path) -> Result<(), String> {
    let novo = std::fs::read(origem).map_err(|e| format!("{}: {e}", origem.display()))?;
    if std::fs::read(destino).is_ok_and(|atual| atual == novo) {
        return Ok(());
    }
    std::fs::write(destino, &novo).map_err(|e| format!("{}: {e}", destino.display()))
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

    /// Open the way uv's launcher holds itself while it runs: readable by
    /// others, but not writable, deletable or renamable.
    fn em_uso(caminho: &Path) -> std::fs::File {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_SHARE_READ: u32 = 1;
        std::fs::OpenOptions::new()
            .read(true)
            .share_mode(FILE_SHARE_READ)
            .open(caminho)
            .unwrap()
    }

    #[test]
    fn uma_copia_igual_em_uso_fica_e_uma_diferente_e_renovada() {
        let pasta = std::env::temp_dir().join(format!("voxvault-caminho-{}", std::process::id()));
        std::fs::create_dir_all(&pasta).unwrap();
        let (origem, copia) = (pasta.join("origem.exe"), pasta.join("copia.exe"));
        std::fs::write(&origem, b"lancador").unwrap();
        std::fs::write(&copia, b"lancador").unwrap();

        let aberta = em_uso(&copia);
        assert_eq!(copiar(&origem, &copia), Ok(()), "a copia igual, em uso, deu erro");
        std::fs::write(&origem, b"lancador novo").unwrap();
        assert!(copiar(&origem, &copia).is_err(), "substituiu um arquivo em uso?");
        assert_eq!(std::fs::read(&copia).unwrap(), b"lancador", "a copia em uso foi estragada");

        drop(aberta);
        assert_eq!(copiar(&origem, &copia), Ok(()));
        assert_eq!(std::fs::read(&copia).unwrap(), b"lancador novo");
        let _ = std::fs::remove_dir_all(&pasta);
    }
}
