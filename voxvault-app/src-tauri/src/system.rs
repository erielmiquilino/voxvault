//! Two things the app needs from Windows directly: how much room is left where
//! the recordings go, and showing a meeting's directory to the user.

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
