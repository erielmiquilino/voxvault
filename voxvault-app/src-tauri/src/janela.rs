//! The main window: created on demand, destroyed when collapsed.
//!
//! Hiding a WebView2 window frees nothing -- measured, 361 MB hidden against
//! 350 MB shown -- so collapsing to the tray destroys it, and reopening builds
//! it again on the last route the interface reported. What survives in the
//! process is the tray, the service supervision and the notifications: about a
//! tenth of the memory, and everything that has to keep working unseen.

use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter, Manager, WebviewWindow, WebviewWindowBuilder};

use crate::avisos;
use crate::prefs;
use crate::residente::Residente;

pub const ROTULO: &str = "main";
/// Where a fresh process opens. Collapsing and reopening keeps the last route
/// instead; this is only the answer for a window nobody has seen yet.
pub const ROTA_INICIAL: &str = "gravacao";
/// Time for WebView2 to finish tearing a window down before what it left in
/// this process is handed back.
const ESPERA_PARA_DEVOLVER: Duration = Duration::from_secs(10);

/// Show the window, building it if it does not exist, on `rota` if given.
///
/// Built off the calling thread: creating a WebView2 window from a tray or
/// menu event handler, which run on the main thread, deadlocks waiting for the
/// very loop it is blocking.
pub fn abrir(app: &AppHandle, rota: Option<&str>) {
    let estado = app.state::<Residente>();
    if let Some(rota) = rota {
        *estado.rota.lock().unwrap() = rota.to_string();
    }

    if let Some(janela) = app.get_webview_window(ROTULO) {
        if let Some(rota) = rota {
            let _ = janela.emit("app://navegar", rota);
        }
        trazer_para_frente(&janela);
        return;
    }

    if estado.criando_janela.swap(true, Ordering::SeqCst) {
        return;
    }
    *estado.abertura_pedida_em.lock().unwrap() = Some(Instant::now());
    let app = app.clone();
    std::thread::spawn(move || {
        let resultado = construir(&app);
        app.state::<Residente>()
            .criando_janela
            .store(false, Ordering::SeqCst);
        match resultado {
            Ok(janela) => trazer_para_frente(&janela),
            Err(erro) => eprintln!("não foi possível abrir a janela: {erro}"),
        }
    });
}

/// Build the window from its declaration in `tauri.conf.json`, which carries
/// `"create": false` so the process can start with none.
pub fn construir(app: &AppHandle) -> tauri::Result<WebviewWindow> {
    let config = app
        .config()
        .app
        .windows
        .iter()
        .find(|w| w.label == ROTULO)
        .cloned()
        .expect("a janela principal está declarada no tauri.conf.json");
    WebviewWindowBuilder::from_config(app, &config)?.build()
}

fn trazer_para_frente(janela: &WebviewWindow) {
    let _ = janela.unminimize();
    let _ = janela.show();
    let _ = janela.set_focus();
}

/// The window went to the tray, by closing or minimizing. The first time ever,
/// say where the app went and how to really quit; never again after that.
/// Once the webview is gone, the memory it left is handed back.
pub fn recolhida(app: &AppHandle) {
    let estado = app.state::<Residente>();
    let mostrar = {
        let mut preferencias = estado.preferencias.lock().unwrap();
        if preferencias.aviso_da_bandeja_mostrado {
            false
        } else {
            preferencias.aviso_da_bandeja_mostrado = true;
            if let Err(erro) = prefs::gravar(&prefs::caminho(), &preferencias) {
                eprintln!("não foi possível gravar as preferências: {erro}");
            }
            true
        }
    };
    if mostrar {
        avisos::mostrar(app, avisos::Aviso::bandeja());
    }
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(ESPERA_PARA_DEVOLVER);
        // Reopened meanwhile: its pages are in use again.
        if app.get_webview_window(ROTULO).is_none() {
            crate::system::devolver_memoria_ociosa();
        }
    });
}

/// What the interface reported when it finished drawing the restored route.
#[derive(Debug, Clone, Copy, serde::Serialize)]
pub struct MedidaDeAbertura {
    /// From the request to open until the route was on screen, by this
    /// process's clock -- the figure the requirement's 2 s is about.
    pub ms_desde_o_pedido: Option<u64>,
    /// `performance.now()` in the page at that moment: the webview's own share.
    pub ms_na_interface: f64,
}

pub fn registrar_pronta(app: &AppHandle, ms_na_interface: f64) -> MedidaDeAbertura {
    let estado = app.state::<Residente>();
    let pedido = estado.abertura_pedida_em.lock().unwrap().take();
    let medida = MedidaDeAbertura {
        ms_desde_o_pedido: pedido.map(|instante| instante.elapsed().as_millis() as u64),
        ms_na_interface,
    };
    *estado.ultima_abertura.lock().unwrap() = Some(medida);
    eprintln!(
        "janela utilizável: {:?} ms desde o pedido, {:.0} ms na interface",
        medida.ms_desde_o_pedido, medida.ms_na_interface
    );
    medida
}
