//! The global shortcut: start or stop a recording from any application.
//!
//! Configurable, and changed at run time without a restart. The order of a
//! change is what keeps it safe: the new combination is registered first, and
//! only once Windows accepted it is the old one released and the choice
//! persisted. A combination another application already holds is refused by
//! the registration itself, and the old one keeps working.

use std::str::FromStr;

use serde::Serialize;
use tauri::{AppHandle, Manager};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutEvent, ShortcutState};

use crate::prefs;
use crate::residente::Residente;
use crate::tray;

/// What the settings screen shows about the shortcut.
#[derive(Debug, Clone, Default, Serialize)]
pub struct EstadoDoAtalho {
    /// The combination actually registered now, if any.
    pub em_vigor: Option<String>,
    /// Why the configured combination is not the one in force.
    pub conflito: Option<String>,
}

/// A combination the user may choose: at least one modifier and one key that
/// is not a modifier. Returns the canonical text to store.
pub fn validar(texto: &str) -> Result<Shortcut, String> {
    let partes: Vec<&str> = texto.split('+').map(str::trim).collect();
    // The names the registration itself understands; the Windows key is
    // "Super" there.
    let modificadores = [
        "ctrl", "control", "alt", "option", "shift", "super", "cmd", "command",
        "commandorcontrol", "commandorctrl", "cmdorctrl", "cmdorcontrol",
    ];
    let tem_modificador = partes
        .iter()
        .any(|p| modificadores.contains(&p.to_ascii_lowercase().as_str()));
    let teclas = partes
        .iter()
        .filter(|p| !modificadores.contains(&p.to_ascii_lowercase().as_str()))
        .count();
    if !tem_modificador || teclas != 1 {
        return Err(format!(
            "\"{texto}\" não serve como atalho global: use ao menos um modificador \
             (Ctrl, Alt, Shift ou Win, que aqui se chama Super) e uma única tecla."
        ));
    }
    Shortcut::from_str(texto)
        .map_err(|erro| format!("\"{texto}\" não é uma combinação reconhecida: {erro}"))
}

fn manipulador(app: &AppHandle, _atalho: &Shortcut, evento: ShortcutEvent) {
    // Only on release: a held key would otherwise start and stop the
    // recording repeatedly while the person is still pressing it.
    if evento.state() == ShortcutState::Released {
        let app = app.clone();
        std::thread::spawn(move || tray::alternar_gravacao(&app));
    }
}

/// Register the configured shortcut at startup. A failure is kept and shown
/// in the settings; the rest of the app works without it.
pub fn registrar_inicial(app: &AppHandle) {
    let estado = app.state::<Residente>();
    let texto = estado.preferencias.lock().unwrap().atalho.clone();
    let resultado = validar(&texto).and_then(|atalho| {
        app.global_shortcut()
            .on_shortcut(atalho, manipulador)
            .map_err(|erro| conflito(&texto, &erro.to_string()))
    });
    let mut atalho = estado.atalho.lock().unwrap();
    match resultado {
        Ok(()) => {
            atalho.em_vigor = Some(texto);
            atalho.conflito = None;
        }
        Err(causa) => {
            eprintln!("atalho global {texto} indisponível: {causa}");
            atalho.em_vigor = None;
            atalho.conflito = Some(causa);
        }
    }
}

fn conflito(texto: &str, erro: &str) -> String {
    format!(
        "O atalho {texto} não pôde ser registrado: outra aplicação provavelmente já \
         o usa ({erro})."
    )
}

/// Switch to `novo`: register it, then release the old one, then persist.
pub fn trocar(app: &AppHandle, novo: &str) -> Result<EstadoDoAtalho, String> {
    let atalho_novo = validar(novo)?;
    let estado = app.state::<Residente>();
    let anterior = estado.atalho.lock().unwrap().em_vigor.clone();
    if anterior.as_deref() == Some(novo) {
        return Ok(estado.atalho.lock().unwrap().clone());
    }
    if let Err(erro) = app.global_shortcut().on_shortcut(atalho_novo, manipulador) {
        let causa = conflito(novo, &erro.to_string());
        let mut atalho = estado.atalho.lock().unwrap();
        atalho.conflito = Some(match &atalho.em_vigor {
            Some(vigente) => format!("{causa} O atalho {vigente} continua valendo."),
            None => causa,
        });
        return Err(atalho.conflito.clone().unwrap_or_default());
    }
    if let Some(vigente) = anterior {
        if let Ok(antigo) = Shortcut::from_str(&vigente) {
            let _ = app.global_shortcut().unregister(antigo);
        }
    }
    {
        let mut preferencias = estado.preferencias.lock().unwrap();
        preferencias.atalho = novo.to_string();
        if let Err(erro) = prefs::gravar(&prefs::caminho(), &preferencias) {
            eprintln!("não foi possível gravar as preferências: {erro}");
        }
    }
    let mut atalho = estado.atalho.lock().unwrap();
    atalho.em_vigor = Some(novo.to_string());
    atalho.conflito = None;
    Ok(atalho.clone())
}

#[cfg(test)]
mod testes {
    use super::*;

    #[test]
    fn combinacoes_validas() {
        for texto in ["Alt+Shift+R", "Ctrl+Alt+G", "Super+F9", "Ctrl+Shift+Space"] {
            assert!(validar(texto).is_ok(), "{texto}");
        }
    }

    #[test]
    fn sem_modificador_ou_sem_tecla_e_recusado() {
        for texto in ["R", "F9", "Ctrl+Shift", "Alt", "Ctrl+A+B"] {
            let erro = validar(texto).unwrap_err();
            assert!(erro.contains("modificador"), "{texto}: {erro}");
        }
    }
}
