//! What the app needs from Windows directly: how much room is left where the
//! recordings go, showing a meeting's directory to the user, and handing back
//! the memory a destroyed window leaves behind.

use std::ffi::OsStr;
use std::os::windows::ffi::OsStrExt;
use std::path::Path;
use std::process::Command;

use windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;

fn wide(path: &OsStr) -> Vec<u16> {
    path.encode_wide().chain(std::iter::once(0)).collect()
}

/// Free and total bytes on the volume holding `path`.
///
/// Walks up to the nearest existing ancestor: the settings screen has to be
/// able to report free space for a directory the user just typed and that does
/// not exist yet, which is exactly when the answer matters.
pub fn disk_space(path: &Path) -> Option<(u64, u64)> {
    let mut cursor = Some(path);
    while let Some(candidate) = cursor {
        if candidate.exists() {
            let wide = wide(candidate.as_os_str());
            let mut free: u64 = 0;
            let mut total: u64 = 0;
            // SAFETY: `wide` is NUL-terminated and both out-pointers are valid
            // for the duration of the call.
            let ok = unsafe {
                GetDiskFreeSpaceExW(
                    wide.as_ptr(),
                    &mut free as *mut u64,
                    &mut total as *mut u64,
                    std::ptr::null_mut(),
                )
            };
            return (ok != 0).then_some((free, total));
        }
        cursor = candidate.parent();
    }
    None
}

/// Whether a directory can actually be written to, verified by writing.
///
/// A permission check that only reads the ACL is the kind of check that passes
/// and then fails at the first recording.
pub fn writable(path: &Path) -> Result<(), String> {
    if !path.exists() {
        std::fs::create_dir_all(path)
            .map_err(|err| format!("Não foi possível criar o diretório: {err}"))?;
    }
    if !path.is_dir() {
        return Err("O caminho existe, mas não é um diretório.".to_string());
    }
    let probe = path.join(".voxvault-escrita");
    std::fs::write(&probe, b"ok")
        .map_err(|err| format!("O diretório não é gravável: {err}"))?;
    let _ = std::fs::remove_file(&probe);
    Ok(())
}

/// Whether the process that published a rendezvous still exists.
///
/// A rendezvous file outlives a process that was killed abruptly, so a client
/// that trusted it blindly would wait for an answer that is never coming. It
/// matters in the other direction too: a service that is merely slow to answer
/// one request is still the owner of this machine's capture, and a client that
/// concluded otherwise would try to start a second one.
pub fn process_is_alive(pid: u32) -> bool {
    use windows_sys::Win32::Foundation::{CloseHandle, STILL_ACTIVE};
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };

    if pid == 0 {
        return false;
    }
    // SAFETY: a null handle is checked before use and closed on every path.
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return false;
        }
        let mut code: u32 = 0;
        let ok = GetExitCodeProcess(handle, &mut code as *mut u32);
        CloseHandle(handle);
        ok != 0 && code == STILL_ACTIVE as u32
    }
}

/// Hand every page of this process that nothing touches back to Windows.
///
/// A destroyed window leaves the process holding the pages of everything the
/// webview loaded, which nothing in the tray touches again: measured on the
/// installed app, 36.7 MB after collapsing, of which 6.5 MB private, and
/// 2.6 MB a minute after emptying. The pages go to the standby list and fault
/// back in if used -- what Windows itself does to a minimized window.
pub fn devolver_memoria_ociosa() -> bool {
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, SetProcessWorkingSetSize};

    // SAFETY: the pseudo-handle of the current process needs no closing, and
    // (-1, -1) is the documented request to empty the working set.
    unsafe { SetProcessWorkingSetSize(GetCurrentProcess(), usize::MAX, usize::MAX) != 0 }
}

/// Reveal a path in Explorer.
pub fn reveal(path: &Path) -> Result<(), String> {
    if !path.exists() {
        return Err(format!("O caminho não existe mais: {}", path.display()));
    }
    Command::new("explorer.exe")
        .arg(path.as_os_str())
        .spawn()
        // Explorer returns a non-zero exit code even on success, so the spawn
        // is the only thing worth checking.
        .map(|_| ())
        .map_err(|err| format!("Não foi possível abrir o gerenciador de arquivos: {err}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_memoria_ociosa_volta_ao_windows() {
        let tocada = vec![1u8; 32 * 1024 * 1024];
        assert!(tocada.iter().all(|&b| b == 1));
        assert!(devolver_memoria_ociosa());
    }
}
