//! What stays alive with no window: the shared state, one supervision pass,
//! and the way out.
//!
//! One pass is the same whether the supervisor's timer or a tray action runs
//! it: ask the service how it is, tell the window if there is one and the
//! picture changed, bring the tray in line, and turn whatever happened into
//! notifications. A tray action runs a pass right after its request, which is
//! how the icon changes within the second instead of at the next tick.

use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Instant;

use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

use crate::atalho::EstadoDoAtalho;
use crate::avisos::{self, Observador};
use crate::janela::{self, MedidaDeAbertura};
use crate::paths;
use crate::prefs::{self, Preferencias};
use crate::service::{self, ServiceClient, Snapshot, Transition};
use crate::tray::{self, Bandeja};

pub struct Residente {
    pub cliente: ServiceClient,
    pub preferencias: Mutex<Preferencias>,
    /// Why `aplicativo.json` was set aside, when it was.
    pub preferencias_ilegiveis: Mutex<Option<String>>,
    pub bandeja: OnceLock<Arc<Bandeja>>,
    pub observador: Mutex<Observador>,
    /// The route the interface last reported, restored when the window is
    /// built again.
    pub rota: Mutex<String>,
    pub abertura_pedida_em: Mutex<Option<Instant>>,
    pub ultima_abertura: Mutex<Option<MedidaDeAbertura>>,
    pub criando_janela: AtomicBool,
    /// A start or stop from the tray or the shortcut is in flight. A second
    /// press meanwhile is told to wait instead of queueing another request
    /// behind a service that may be stuck opening a device.
    pub acao_da_gravacao: AtomicBool,
    pub atalho: Mutex<EstadoDoAtalho>,
    /// The last picture sent to the window, so it is woken only by changes.
    ultimo_emitido: Mutex<String>,
    /// One pass at a time, whoever runs it.
    em_ciclo: Mutex<()>,
}

impl Residente {
    pub fn novo(cliente: ServiceClient) -> Self {
        let leitura = prefs::ler(&prefs::caminho());
        Self {
            cliente,
            preferencias: Mutex::new(leitura.preferencias),
            preferencias_ilegiveis: Mutex::new(leitura.ilegivel),
            bandeja: OnceLock::new(),
            observador: Mutex::new(Observador::default()),
            rota: Mutex::new(janela::ROTA_INICIAL.to_string()),
            abertura_pedida_em: Mutex::new(None),
            ultima_abertura: Mutex::new(None),
            criando_janela: AtomicBool::new(false),
            acao_da_gravacao: AtomicBool::new(false),
            atalho: Mutex::new(EstadoDoAtalho::default()),
            ultimo_emitido: Mutex::new(String::new()),
            em_ciclo: Mutex::new(()),
        }
    }
}

/// One supervision pass. Returns what changed and the resulting picture.
pub fn ciclo(app: &AppHandle) -> (Transition, Snapshot) {
    let estado = app.state::<Residente>();
    let _vez = estado.em_ciclo.lock().unwrap();
    let transicao = estado.cliente.passo();
    let snapshot = estado.cliente.snapshot();

    match transicao {
        Transition::Crashed => {
            let _ = app.emit(
                "servico://queda",
                "O serviço local terminou inesperadamente. O aplicativo está tentando \
                 reconectar.",
            );
        }
        Transition::Connected => {
            let _ = app.emit("servico://conectado", ());
        }
        Transition::GaveUp => {
            let _ = app.emit("servico://desistiu", &snapshot.detalhe);
        }
        Transition::Unchanged => {}
    }

    if app.get_webview_window(janela::ROTULO).is_some() {
        if let Ok(serializado) = serde_json::to_string(&snapshot) {
            let mut ultimo = estado.ultimo_emitido.lock().unwrap();
            if serializado != *ultimo {
                let _ = app.emit("servico://estado", &snapshot);
                *ultimo = serializado;
            }
        }
    } else {
        // A window built later must get the picture on its first pass.
        estado.ultimo_emitido.lock().unwrap().clear();
    }

    if let Some(bandeja) = estado.bandeja.get() {
        bandeja.aplicar(&snapshot, tem_reuniao(&snapshot));
    }

    let mut novos = estado.observador.lock().unwrap().observar(&snapshot);
    if snapshot.estado == service::ServiceState::Conectado {
        let desde = estado.observador.lock().unwrap().desde();
        let rota = match desde {
            Some(seq) => format!("/eventos?desde={seq}"),
            None => "/eventos".to_string(),
        };
        if let Ok(resposta) = service::call(&estado.cliente, "GET", &rota, None) {
            novos.extend(estado.observador.lock().unwrap().eventos(&resposta));
        }
    }
    for aviso in novos {
        avisos::mostrar(app, aviso);
    }
    (transicao, snapshot)
}

/// Whether any meeting exists, for "Abrir a última reunião". A directory
/// listing that stops at the first meeting folder: no command line, no
/// database, nothing that grows with the library.
fn tem_reuniao(snapshot: &Snapshot) -> bool {
    let dados = snapshot
        .data_dir_do_servico
        .as_ref()
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| paths::effective_data_dir().0);
    let Ok(entradas) = std::fs::read_dir(paths::recordings_dir(&dados)) else {
        return false;
    };
    entradas.flatten().any(|entrada| {
        entrada.file_type().map(|t| t.is_dir()).unwrap_or(false)
            && !entrada.file_name().to_string_lossy().starts_with('.')
    })
}

/// "Sair do VoxVault". With a recording running, a native confirmation that
/// names its duration -- there may be no window to show one of ours -- and a
/// clean stop before leaving. The service is never ended from here: it has
/// the transcription queue, and an idle policy of its own.
pub fn sair(app: &AppHandle) {
    let estado = app.state::<Residente>();
    let snapshot = estado.cliente.snapshot();
    let gravando = snapshot
        .saude
        .as_ref()
        .map(|s| s.gravacao_ativa)
        .unwrap_or(false);
    if gravando {
        let duracao = tray::gravacao_vista(&snapshot)
            .map(|g| tray::relogio(g.duracao_ms))
            .unwrap_or_else(|| "algum tempo".to_string());
        let confirmado = app
            .dialog()
            .message(format!(
                "Há uma gravação em andamento há {duracao}.\n\nAo sair, ela é \
                 encerrada de forma limpa e enviada para transcrição, que continua \
                 no serviço local mesmo com o VoxVault fechado."
            ))
            .title("Sair do VoxVault")
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancelCustom(
                "Encerrar gravação e sair".to_string(),
                "Cancelar".to_string(),
            ))
            .blocking_show();
        if !confirmado {
            return;
        }
        if let Err(causa) = service::call(&estado.cliente, "POST", "/gravacao/encerrar", None) {
            // A stop that failed must not become a silent exit: the recording
            // may still be running, and the person has to know.
            app.dialog()
                .message(format!(
                    "A gravação não pôde ser encerrada, então o VoxVault continua \
                     aberto.\n\n{causa}"
                ))
                .title("Sair do VoxVault")
                .kind(MessageDialogKind::Error)
                .blocking_show();
            return;
        }
    }
    app.exit(0);
}
