//! The few values of HKEY_CURRENT_USER the app keeps: the `voxvault:` scheme
//! a notification's click opens, and the user's PATH.
//!
//! Strings only, read without expanding variables and written back with the
//! type they had: a `%USERPROFILE%` in someone's PATH has to stay a variable.
//! Read at their full length, too -- a PATH is often longer than any fixed
//! buffer, and editing it through a tool that truncates is how PATHs get lost.

use windows_sys::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_MORE_DATA, ERROR_SUCCESS};
use windows_sys::Win32::System::Registry::{
    HKEY, HKEY_CURRENT_USER, KEY_SET_VALUE, REG_EXPAND_SZ, REG_OPTION_NON_VOLATILE, REG_SZ,
    RRF_NOEXPAND, RRF_RT_REG_EXPAND_SZ, RRF_RT_REG_SZ, RegCloseKey, RegCreateKeyExW,
    RegGetValueW, RegSetValueExW,
};

/// A plain string value.
pub const TEXTO: u32 = REG_SZ;
/// A string whose `%VARIABLES%` Windows expands when it reads it.
pub const TEXTO_EXPANSIVEL: u32 = REG_EXPAND_SZ;

fn largo(texto: &str) -> Vec<u16> {
    texto.encode_utf16().chain(std::iter::once(0)).collect()
}

/// A string value of a key under HKEY_CURRENT_USER and its type, variables
/// left as written; `Ok(None)` only when the value does not exist. `nome` is
/// `None` for the key's default value.
///
/// Any other failure is an error, never an absent value: the PATH is written
/// back from what this returns, and a failed read taken for an empty one
/// would replace someone's whole PATH with a single folder.
pub fn ler(chave: &str, nome: Option<&str>) -> Result<Option<(String, u32)>, String> {
    let chave_larga = largo(chave);
    let nome = nome.map(largo);
    let nome_ptr = nome.as_ref().map_or(std::ptr::null(), |n| n.as_ptr());
    let flags = RRF_RT_REG_SZ | RRF_RT_REG_EXPAND_SZ | RRF_NOEXPAND;
    // Empty, the first call only measures. A value that grew in between --
    // another program editing the same PATH -- is measured again.
    let mut buffer: Vec<u16> = Vec::new();
    for _ in 0..8 {
        let mut tipo: u32 = 0;
        let mut bytes = (buffer.len() * 2) as u32;
        let destino = if buffer.is_empty() {
            std::ptr::null_mut()
        } else {
            buffer.as_mut_ptr().cast()
        };
        // SAFETY: NUL-terminated names, and a buffer either absent or of
        // exactly the size passed with it.
        let status = unsafe {
            RegGetValueW(
                HKEY_CURRENT_USER,
                chave_larga.as_ptr(),
                nome_ptr,
                flags,
                &mut tipo,
                destino,
                &mut bytes,
            )
        };
        match status {
            ERROR_SUCCESS if !buffer.is_empty() => {
                let unidades = (bytes as usize / 2).saturating_sub(1);
                return Ok(Some((String::from_utf16_lossy(&buffer[..unidades]), tipo)));
            }
            ERROR_SUCCESS | ERROR_MORE_DATA => {
                buffer = vec![0u16; (bytes as usize).div_ceil(2) + 1];
            }
            ERROR_FILE_NOT_FOUND => return Ok(None),
            outro => return Err(format!("não foi possível ler {chave} ({outro})")),
        }
    }
    Err(format!("{chave} mudou enquanto era lido"))
}

/// Write a string value of type `tipo` ([`TEXTO`] or [`TEXTO_EXPANSIVEL`]),
/// creating the key when it is missing.
pub fn escrever(chave: &str, nome: Option<&str>, valor: &str, tipo: u32) -> Result<(), String> {
    let chave_larga = largo(chave);
    let nome = nome.map(largo);
    let valor_largo = largo(valor);
    let mut aberta: HKEY = std::ptr::null_mut();
    // SAFETY: NUL-terminated strings, an out-pointer valid for the call, and
    // the handle closed on every path after it was opened.
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
            tipo,
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

/// Tell the programs already running -- Explorer above all, which starts the
/// next terminal -- that the user's environment changed.
pub fn avisar_mudanca_de_ambiente() {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        HWND_BROADCAST, SMTO_ABORTIFHUNG, SendMessageTimeoutW, WM_SETTINGCHANGE,
    };

    let ambiente = largo("Environment");
    let mut resultado: usize = 0;
    // SAFETY: the string outlives the call, which waits at most 5 s for any
    // one window and never for a hung one.
    unsafe {
        SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            ambiente.as_ptr() as isize,
            SMTO_ABORTIFHUNG,
            5000,
            &mut resultado,
        );
    }
}

#[cfg(test)]
mod testes {
    use super::*;
    use windows_sys::Win32::System::Registry::{RegDeleteKeyW, RegDeleteTreeW};

    const RAIZ_DOS_TESTES: &str = r"Software\VoxVault-testes";

    /// Each test under a key of its own -- they run in parallel -- and the
    /// shared parent goes with the last of them.
    fn apagar(chave: &str) {
        let chave = largo(chave);
        let raiz = largo(RAIZ_DOS_TESTES);
        // SAFETY: NUL-terminated names under this user's own hive; deleting
        // the parent fails, harmlessly, while another test still has a key.
        unsafe {
            RegDeleteTreeW(HKEY_CURRENT_USER, chave.as_ptr());
            RegDeleteKeyW(HKEY_CURRENT_USER, raiz.as_ptr());
        }
    }

    #[test]
    fn o_valor_volta_como_foi_escrito_e_com_o_seu_tipo() {
        let chave = r"Software\VoxVault-testes\registro";
        apagar(chave);
        // Past 8192, the length of even the "large strings" build of NSIS.
        let longo = format!(r"%USERPROFILE%\bin;{}", ";C:\\pasta".repeat(1000));
        assert!(longo.len() > 8192);

        escrever(chave, Some("Path"), &longo, TEXTO_EXPANSIVEL).unwrap();
        escrever(chave, None, "padrão", TEXTO).unwrap();

        assert_eq!(
            ler(chave, Some("Path")),
            Ok(Some((longo, TEXTO_EXPANSIVEL))),
            "a variavel foi expandida ou o valor longo foi cortado"
        );
        assert_eq!(ler(chave, None), Ok(Some(("padrão".to_string(), TEXTO))));
        assert_eq!(ler(chave, Some("inexistente")), Ok(None));
        assert_eq!(ler(r"Software\VoxVault-testes\nenhuma", Some("Path")), Ok(None));
        apagar(chave);
    }

    #[test]
    fn um_valor_que_nao_e_texto_e_erro_e_nao_ausencia() {
        use windows_sys::Win32::System::Registry::{REG_DWORD, RegSetKeyValueW};

        let chave = r"Software\VoxVault-testes\numero";
        apagar(chave);
        let chave_larga = largo(chave);
        let nome = largo("Path");
        let valor: u32 = 7;
        // SAFETY: NUL-terminated names and a 4-byte value for a REG_DWORD.
        let status = unsafe {
            RegSetKeyValueW(
                HKEY_CURRENT_USER,
                chave_larga.as_ptr(),
                nome.as_ptr(),
                REG_DWORD,
                std::ptr::from_ref(&valor).cast(),
                4,
            )
        };
        assert_eq!(status, ERROR_SUCCESS);

        assert!(
            ler(chave, Some("Path")).is_err(),
            "um PATH ilegivel seria regravado como se estivesse vazio"
        );
        apagar(chave);
    }
}
