//! Tray icon and global shortcut: reaching the recording without finding the
//! window first.
//!
//! The reason this exists is narrow and worth stating. A meeting starts while
//! you are already in another application, and the two seconds spent hunting
//! for a window are the two seconds of the meeting you lose. The shortcut and
//! the tray both do exactly one thing -- toggle the recording -- and both go
//! through the resident service, like every other path in this app.
//!
//! The tray lives as long as the window does. It is not a resident agent: when
//! the app is closed the service keeps the queue and ends itself by its idle
//! policy, and nothing here survives to watch for meetings. That is a stated
//! limit of this version, not an oversight.

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, Runtime};

use crate::service::{self, ServiceClient};

/// Chosen to not collide with anything common. Alt is rarer than Ctrl+Shift in
/// application shortcuts, and R for "gravar/record" is the obvious letter.
const ATALHO: &str = "Alt+Shift+R";

pub fn instalar<R: Runtime>(app: &AppHandle<R>, cliente: ServiceClient) -> tauri::Result<()> {
    let abrir = MenuItem::with_id(app, "abrir", "Abrir o VoxVault", true, None::<&str>)?;
    let alternar = MenuItem::with_id(
        app,
        "alternar",
        &format!("Iniciar/encerrar gravação  ({ATALHO})"),
        true,
        None::<&str>,
    )?;
    let separador = PredefinedMenuItem::separator(app)?;
    let sair = MenuItem::with_id(app, "sair", "Fechar o aplicativo", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&abrir, &alternar, &separador, &sair])?;

    let para_menu = cliente.clone();
    TrayIconBuilder::with_id("voxvault")
        .icon(app.default_window_icon().unwrap().clone())
        .tooltip("VoxVault")
        .menu(&menu)
        // The menu must not open on a left click, or the left click cannot mean
        // "show me the window", which is what a tray icon means everywhere else.
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, evento| match evento.id().as_ref() {
            "abrir" => mostrar_janela(app),
            "alternar" => alternar_gravacao(app, &para_menu),
            "sair" => {
                // Goes through the window's own close path, so a recording in
                // progress still asks before anything is ended.
                if let Some(janela) = app.get_webview_window("main") {
                    let _ = janela.close();
                }
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, evento| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = evento
            {
                mostrar_janela(tray.app_handle());
            }
        })
        .build(app)?;

    Ok(())
}

fn mostrar_janela<R: Runtime>(app: &AppHandle<R>) {
    if let Some(janela) = app.get_webview_window("main") {
        let _ = janela.show();
        let _ = janela.unminimize();
        let _ = janela.set_focus();
    }
}

/// Start or stop, whichever the service says is valid right now.
///
/// The decision is the service's, not this window's: the app asks what is
/// happening and acts on that, so a recording started from the command line is
/// stopped by the same shortcut.
pub fn alternar_gravacao<R: Runtime>(app: &AppHandle<R>, cliente: &ServiceClient) {
    let gravando = cliente.gravando();
    let caminho = if gravando {
        "/gravacao/encerrar"
    } else {
        "/gravacao/iniciar"
    };
    let corpo = (!gravando).then_some("{\"titulo\":\"\"}");

    match service::call(cliente, "POST", caminho, corpo) {
        Ok(_) => {
            let _ = app.emit(
                "atalho://gravacao",
                if gravando {
                    "Gravação encerrada pelo atalho."
                } else {
                    "Gravação iniciada pelo atalho."
                },
            );
            // The window is brought forward on a start so the person can see
            // the tracks actually capturing. Silently starting a recording and
            // showing nothing is how you end up with an hour of silence.
            if !gravando {
                mostrar_janela(app);
            }
        }
        Err(causa) => {
            let _ = app.emit("atalho://falhou", causa);
            mostrar_janela(app);
        }
    }
}

pub fn atalho() -> &'static str {
    ATALHO
}
