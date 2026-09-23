//! What clicking a notification does, from the screen or from the center.
//!
//! An app without a package hears a click in-process only while Windows still
//! holds the toast it showed: clicked in the notification center -- where
//! every one of them goes with "Do not disturb" on -- a notification reached
//! nothing, and the meeting it announced never opened. So each notification
//! carries a `voxvault://` address instead. Windows opens it from wherever the
//! toast is clicked by starting this executable with it; the single-instance
//! plugin hands it to the process already running, and the address says what
//! to do.
//!
//! Any program can open an address, so none of them records by itself: the
//! "Gravar" of a meeting detection carries a token only this process issued,
//! good while the suggestion is and used once. An address that is not
//! understood opens the window, as a second launch always did.

use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::sync::Mutex;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use crate::avisos::{Destino, VALIDADE_DA_SUGESTAO};

pub const ESQUEMA: &str = "voxvault";
/// Where Windows looks the scheme up, for this user only.
const CHAVE: &str = r"Software\Classes\voxvault";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Pedido {
    /// Open the window on this route.
    Abrir(String),
    /// The "Gravar" button, with the token its notification carried.
    Gravar(String),
}

/// The address a click on the notification opens; none for a notification
/// that leads nowhere.
pub fn endereco_para(destino: &Destino) -> Option<String> {
    match destino {
        Destino::Nada => None,
        Destino::Gravacao => Some(format!("{ESQUEMA}://abrir/gravacao")),
        Destino::Reuniao(uid) => Some(format!("{ESQUEMA}://abrir/biblioteca/{uid}")),
    }
}

pub fn endereco_para_gravar(token: &str) -> String {
    format!("{ESQUEMA}://gravar/{token}")
}

/// What an argument asks for, when it is one of these addresses and well
/// formed. Browsers may add a trailing slash, and nothing else is accepted.
pub fn interpretar(argumento: &str) -> Option<Pedido> {
    let (esquema, resto) = argumento.split_once("://")?;
    if !esquema.eq_ignore_ascii_case(ESQUEMA) {
        return None;
    }
    let resto = resto.trim_end_matches('/');
    if let Some(rota) = resto.strip_prefix("abrir/") {
        return rota_valida(rota).then(|| Pedido::Abrir(rota.to_string()));
    }
    if let Some(token) = resto.strip_prefix("gravar/") {
        let valido = token.len() == 32 && token.chars().all(|c| c.is_ascii_hexdigit());
        return valido.then(|| Pedido::Gravar(token.to_ascii_lowercase()));
    }
    None
}

/// Only the routes a notification points to: the recording screen and one
/// meeting, by an identifier that cannot carry a path or a query.
fn rota_valida(rota: &str) -> bool {
    if rota == "gravacao" {
        return true;
    }
    match rota.strip_prefix("biblioteca/") {
        Some(uid) => {
            !uid.is_empty()
                && uid.len() <= 64
                && uid.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        }
        None => false,
    }
}

// -- the "Gravar" tokens ------------------------------------------------

/// Tokens issued for "Gravar", each with when it was issued.
static EMITIDOS: Mutex<Vec<(String, Instant)>> = Mutex::new(Vec::new());

/// A token for one "Gravar" button, remembered until the suggestion expires.
pub fn emitir_token(agora: Instant) -> String {
    let token = novo_token();
    let mut emitidos = EMITIDOS.lock().unwrap();
    emitidos.retain(|(_, em)| agora.saturating_duration_since(*em) < VALIDADE_DA_SUGESTAO);
    emitidos.push((token.clone(), agora));
    token
}

/// How long ago the token was issued, taking it out: a token works once.
/// `None` for one this process never issued or already forgot.
pub fn resgatar_token(token: &str, agora: Instant) -> Option<Duration> {
    let mut emitidos = EMITIDOS.lock().unwrap();
    let posicao = emitidos.iter().position(|(t, _)| t == token)?;
    let (_, em) = emitidos.remove(posicao);
    Some(agora.saturating_duration_since(em))
}

/// 128 bits nobody outside this process can guess: two SipHash outputs under
/// the keys the standard library draws from the operating system.
fn novo_token() -> String {
    static CONTADOR: AtomicU64 = AtomicU64::new(0);
    let parte = || {
        let mut hasher = RandomState::new().build_hasher();
        hasher.write_u64(CONTADOR.fetch_add(1, Ordering::Relaxed));
        hasher.write_u128(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0),
        );
        hasher.finish()
    };
    format!("{:016x}{:016x}", parte(), parte())
}

// -- the scheme in the registry ------------------------------------------

/// Point `voxvault:` at this executable, for this user, if it does not
/// already. Checked on every start: the executable may have moved, and a
/// development build and the installed one take turns.
pub fn registrar_esquema() -> Result<(), String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let exe = exe.display().to_string();
    let comando = format!("\"{exe}\" \"%1\"");
    let comando_chave = format!(r"{CHAVE}\shell\open\command");
    if registro::ler(&comando_chave, None).as_deref() == Some(comando.as_str()) {
        return Ok(());
    }
    registro::escrever(CHAVE, None, "URL:VoxVault")?;
    registro::escrever(CHAVE, Some("URL Protocol"), "")?;
    registro::escrever(&format!(r"{CHAVE}\DefaultIcon"), None, &format!("\"{exe}\",0"))?;
    registro::escrever(&comando_chave, None, &comando)
}

mod registro {
    use windows_sys::Win32::Foundation::ERROR_SUCCESS;
    use windows_sys::Win32::System::Registry::{
        HKEY, HKEY_CURRENT_USER, KEY_SET_VALUE, REG_OPTION_NON_VOLATILE, REG_SZ, RRF_RT_REG_SZ,
        RegCloseKey, RegCreateKeyExW, RegGetValueW, RegSetValueExW,
    };

    fn largo(texto: &str) -> Vec<u16> {
        texto.encode_utf16().chain(std::iter::once(0)).collect()
    }

    /// A string value of a key under HKEY_CURRENT_USER; `None` for the
    /// default value.
    pub fn ler(chave: &str, nome: Option<&str>) -> Option<String> {
        let chave = largo(chave);
        let nome = nome.map(largo);
        let nome_ptr = nome.as_ref().map_or(std::ptr::null(), |n| n.as_ptr());
        let mut buffer = vec![0u16; 1024];
        let mut bytes = (buffer.len() * 2) as u32;
        // SAFETY: both names are NUL-terminated, and the buffer and its size
        // in bytes agree.
        let status = unsafe {
            RegGetValueW(
                HKEY_CURRENT_USER,
                chave.as_ptr(),
                nome_ptr,
                RRF_RT_REG_SZ,
                std::ptr::null_mut(),
                buffer.as_mut_ptr().cast(),
                &mut bytes,
            )
        };
        if status != ERROR_SUCCESS {
            return None;
        }
        let unidades = (bytes as usize / 2).saturating_sub(1);
        Some(String::from_utf16_lossy(&buffer[..unidades]))
    }

    pub fn escrever(chave: &str, nome: Option<&str>, valor: &str) -> Result<(), String> {
        let chave_larga = largo(chave);
        let nome = nome.map(largo);
        let valor_largo = largo(valor);
        let mut aberta: HKEY = std::ptr::null_mut();
        // SAFETY: NUL-terminated strings, an out-pointer valid for the call,
        // and the handle closed on every path after it was opened.
        unsafe {
            let status = RegCreateKeyExW(
                HKEY_CURRENT_USER,
                chave_larga.as_ptr(),
                0,
                std::ptr::null(),
                REG_OPTION_NON_VOLATILE,
                KEY_SET_VALUE,
                std::ptr::null(),
                &mut aberta,
                std::ptr::null_mut(),
            );
            if status != ERROR_SUCCESS {
                return Err(format!("não foi possível abrir {chave} ({status})"));
            }
            let status = RegSetValueExW(
                aberta,
                nome.as_ref().map_or(std::ptr::null(), |n| n.as_ptr()),
                0,
                REG_SZ,
                valor_largo.as_ptr().cast(),
                (valor_largo.len() * 2) as u32,
            );
            RegCloseKey(aberta);
            if status != ERROR_SUCCESS {
                return Err(format!("não foi possível gravar em {chave} ({status})"));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod testes {
    use super::*;

    #[test]
    fn cada_destino_tem_o_seu_endereco() {
        assert_eq!(endereco_para(&Destino::Nada), None);
        assert_eq!(
            endereco_para(&Destino::Gravacao).as_deref(),
            Some("voxvault://abrir/gravacao")
        );
        assert_eq!(
            endereco_para(&Destino::Reuniao("6cced371a4c94f37".into())).as_deref(),
            Some("voxvault://abrir/biblioteca/6cced371a4c94f37")
        );
    }

    #[test]
    fn o_endereco_de_volta_e_o_mesmo_pedido() {
        for destino in [Destino::Gravacao, Destino::Reuniao("abc123".into())] {
            let endereco = endereco_para(&destino).unwrap();
            let rota = endereco.trim_start_matches("voxvault://abrir/").to_string();
            assert_eq!(interpretar(&endereco), Some(Pedido::Abrir(rota)));
        }
        let token = "0123456789abcdef0123456789ABCDEF";
        assert_eq!(
            interpretar(&endereco_para_gravar(token)),
            Some(Pedido::Gravar(token.to_ascii_lowercase()))
        );
    }

    #[test]
    fn uma_barra_no_fim_e_o_esquema_em_maiusculas_sao_aceitos() {
        assert_eq!(
            interpretar("VoxVault://abrir/gravacao/"),
            Some(Pedido::Abrir("gravacao".into()))
        );
    }

    #[test]
    fn nada_fora_do_que_uma_notificacao_aponta_e_aceito() {
        for recusado in [
            "--bandeja",
            "C:\\qualquer\\arquivo",
            "http://abrir/gravacao",
            "voxvault://",
            "voxvault://abrir/configuracoes",
            "voxvault://abrir/biblioteca/",
            "voxvault://abrir/biblioteca/../../x",
            "voxvault://abrir/biblioteca/abc?x=1",
            "voxvault://abrir/biblioteca/abc/def",
            "voxvault://gravar/curto",
            "voxvault://gravar/0123456789abcdef0123456789abcdeg",
            "voxvault://apagar/tudo",
        ] {
            assert_eq!(interpretar(recusado), None, "{recusado}");
        }
    }

    #[test]
    fn um_token_vale_uma_vez_e_so_se_foi_emitido_aqui() {
        let agora = Instant::now();
        let token = emitir_token(agora);
        assert_eq!(token.len(), 32);
        assert!(interpretar(&endereco_para_gravar(&token)).is_some());
        let depois = agora + Duration::from_secs(5);
        assert_eq!(resgatar_token(&token, depois), Some(Duration::from_secs(5)));
        assert_eq!(resgatar_token(&token, depois), None, "usado duas vezes");
        assert_eq!(resgatar_token("0123456789abcdef0123456789abcdef", depois), None);
    }

    #[test]
    fn os_tokens_nao_se_repetem() {
        let agora = Instant::now();
        let a = emitir_token(agora);
        let b = emitir_token(agora);
        assert_ne!(a, b);
        let _ = (resgatar_token(&a, agora), resgatar_token(&b, agora));
    }
}
