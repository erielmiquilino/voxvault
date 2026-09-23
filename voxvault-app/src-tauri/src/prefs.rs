//! The application's own preferences, in `%USERPROFILE%\.voxvault\aplicativo.json`.
//!
//! Beside the core's `config.json`, for the same reason -- outside the AppData
//! virtualization of packaged Windows apps -- but a file of its own, because the
//! core refuses fields it does not know, on purpose, and these are nobody's
//! business but the tray's: the shortcut, which notifications to show, and
//! whether the one-time "still running in the tray" notice was already shown.
//!
//! Whether the app starts with Windows is deliberately **not** here. The
//! registry is the truth about that, and a copy in this file could only ever
//! disagree with it.
//!
//! A file that cannot be read counts as the defaults, is reported to the
//! settings screen, and is rewritten whole on the next change. A preference
//! must never be the reason the app does not start.

use std::io;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::paths;

pub const ATALHO_PADRAO: &str = "Alt+Shift+R";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Notificacoes {
    pub gravacao_iniciada: bool,
    pub gravacao_encerrada: bool,
    pub transcricao_concluida: bool,
    pub transcricao_falha: bool,
    pub avisos_de_captura: bool,
    pub reuniao_detectada: bool,
}

impl Default for Notificacoes {
    fn default() -> Self {
        Self {
            gravacao_iniciada: true,
            gravacao_encerrada: true,
            transcricao_concluida: true,
            transcricao_falha: true,
            avisos_de_captura: true,
            reuniao_detectada: true,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Preferencias {
    pub atalho: String,
    pub notificacoes: Notificacoes,
    pub aviso_da_bandeja_mostrado: bool,
}

impl Default for Preferencias {
    fn default() -> Self {
        Self {
            atalho: ATALHO_PADRAO.to_string(),
            notificacoes: Notificacoes::default(),
            aviso_da_bandeja_mostrado: false,
        }
    }
}

/// What reading the file produced: the preferences in force, and why the file
/// was set aside when it could not be used.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Leitura {
    pub preferencias: Preferencias,
    pub ilegivel: Option<String>,
}

pub fn caminho() -> PathBuf {
    paths::user_config_dir().join("aplicativo.json")
}

pub fn ler(arquivo: &Path) -> Leitura {
    let texto = match std::fs::read_to_string(arquivo) {
        Ok(texto) => texto,
        Err(err) if err.kind() == io::ErrorKind::NotFound => {
            return Leitura {
                preferencias: Preferencias::default(),
                ilegivel: None,
            }
        }
        Err(err) => {
            return Leitura {
                preferencias: Preferencias::default(),
                ilegivel: Some(format!(
                    "{} não pôde ser lido ({err}); valem os padrões.",
                    arquivo.display()
                )),
            }
        }
    };
    match serde_json::from_str::<Preferencias>(&texto) {
        Ok(preferencias) => Leitura {
            preferencias,
            ilegivel: None,
        },
        Err(err) => Leitura {
            preferencias: Preferencias::default(),
            ilegivel: Some(format!(
                "{} está corrompido ({err}); valem os padrões, e o arquivo é \
                 regravado na próxima alteração.",
                arquivo.display()
            )),
        },
    }
}

/// Write the whole file through a temporary and a rename, so a crash leaves
/// the previous file or the new one and never half of either.
pub fn gravar(arquivo: &Path, preferencias: &Preferencias) -> io::Result<()> {
    if let Some(pasta) = arquivo.parent() {
        std::fs::create_dir_all(pasta)?;
    }
    let texto = serde_json::to_string_pretty(preferencias).map_err(io::Error::other)?;
    let temporario = arquivo.with_extension("json.tmp");
    std::fs::write(&temporario, texto)?;
    std::fs::rename(&temporario, arquivo)
}

#[cfg(test)]
mod testes {
    use super::*;

    fn pasta() -> PathBuf {
        let pasta = std::env::temp_dir().join(format!(
            "voxvault-prefs-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&pasta).unwrap();
        pasta
    }

    #[test]
    fn ida_e_volta_preserva_tudo() {
        let arquivo = pasta().join("aplicativo.json");
        let preferencias = Preferencias {
            atalho: "Ctrl+Alt+G".to_string(),
            notificacoes: Notificacoes {
                gravacao_iniciada: false,
                ..Notificacoes::default()
            },
            aviso_da_bandeja_mostrado: true,
        };

        gravar(&arquivo, &preferencias).unwrap();
        let lida = ler(&arquivo);

        assert_eq!(lida.preferencias, preferencias);
        assert_eq!(lida.ilegivel, None);
        assert!(!arquivo.with_extension("json.tmp").exists());
    }

    #[test]
    fn sem_arquivo_valem_os_padroes_sem_aviso() {
        let lida = ler(&pasta().join("nao-existe.json"));
        assert_eq!(lida.preferencias, Preferencias::default());
        assert_eq!(lida.preferencias.atalho, "Alt+Shift+R");
        assert!(lida.preferencias.notificacoes.reuniao_detectada);
        assert!(!lida.preferencias.aviso_da_bandeja_mostrado);
        assert_eq!(lida.ilegivel, None);
    }

    #[test]
    fn arquivo_corrompido_vale_os_padroes_e_diz_por_que() {
        let arquivo = pasta().join("aplicativo.json");
        std::fs::write(&arquivo, "{ \"atalho\": ").unwrap();

        let lida = ler(&arquivo);

        assert_eq!(lida.preferencias, Preferencias::default());
        assert!(lida.ilegivel.unwrap().contains("corrompido"));
    }

    #[test]
    fn campos_ausentes_ficam_com_o_padrao() {
        let arquivo = pasta().join("aplicativo.json");
        std::fs::write(&arquivo, r#"{"notificacoes": {"avisos_de_captura": false}}"#).unwrap();

        let lida = ler(&arquivo).preferencias;

        assert_eq!(lida.atalho, ATALHO_PADRAO);
        assert!(!lida.notificacoes.avisos_de_captura);
        assert!(lida.notificacoes.gravacao_iniciada);
    }
}
