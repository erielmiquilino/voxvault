//! Application host.
//!
//! Three things happen here that are requirements rather than plumbing: only
//! one instance ever runs, a background supervisor keeps the app's picture of
//! the resident service honest, and closing the window with a recording running
//! asks first and then ends the recording cleanly.
//!
//! The supervisor's cadence is part of the cost budget. It is a loopback TCP
//! connect and a small GET, every ten seconds while attached. It does not poll
//! the command line, because each command-line call is a Python interpreter
//! start, and it emits to the interface only when the picture actually changed
//! -- an event every tick would wake the webview for nothing, and the webview
//! is where a badly built interface spends its budget.

mod cli;
mod commands;
mod harness;
mod http;
mod library;
mod paths;
mod service;
mod system;
mod tray;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use tauri::{Emitter, Manager, WindowEvent};

/// While attached and nothing is being recorded.
const INTERVALO_CONECTADO: Duration = Duration::from_secs(10);
/// While a recording is running. The service has no push channel yet, so the
/// recording view is polled -- close enough that a warning reaches the screen
/// while it still means something, far enough that an hour of meeting costs
/// eighteen hundred small loopback requests rather than seven thousand.
const INTERVALO_GRAVANDO: Duration = Duration::from_secs(2);
/// While looking for the service, or right after losing it.
const INTERVALO_PROCURANDO: Duration = Duration::from_secs(3);
/// After the retry budget is spent: the pass is a no-op, so this is only how
/// often the thread wakes to notice a re-arm.
const INTERVALO_PARADO: Duration = Duration::from_secs(5);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let cliente = service::ServiceClient::new();
    let fechamento_aprovado = Arc::new(AtomicBool::new(false));

    tauri::Builder::default()
        // Registered first, as the plugin requires: a second launch has to be
        // intercepted before this process builds a window of its own.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(cliente.clone())
        .manage(fechamento_aprovado.clone())
        .invoke_handler(tauri::generate_handler![
            commands::ambiente_estado,
            commands::ambiente_preparar,
            commands::servico_estado,
            commands::servico_rearmar,
            commands::gravacao_estado,
            commands::gravacao_iniciar,
            commands::gravacao_pausar,
            commands::gravacao_retomar,
            commands::gravacao_encerrar,
            commands::reunioes_listar,
            commands::reuniao_detalhar,
            commands::reuniao_do_nucleo,
            commands::reuniao_reprocessar,
            commands::reuniao_exportar,
            commands::reuniao_renomear,
            commands::reuniao_remover_audio_previa,
            commands::reuniao_remover_audio,
            commands::reunioes_excluir_previa,
            commands::reunioes_excluir,
            commands::reuniao_abrir_pasta,
            commands::abrir_caminho,
            commands::busca,
            commands::nota_criar,
            commands::nota_alterar,
            commands::nota_remover,
            commands::importar,
            commands::diretorio_de_dados,
            commands::diretorio_de_dados_validar,
            commands::diretorio_de_dados_alterar,
            commands::configuracao_ler,
            commands::configuracao_gravar,
            commands::dispositivos,
            commands::diagnostico_do_nucleo,
            commands::diagnostico_do_aplicativo,
            commands::mcp_estado,
            commands::mcp_registrar,
            commands::estado_de_fechamento,
        ])
        .setup({
            let cliente = cliente.clone();
            move |app| {
                // The player reads a meeting's audio straight from disk, so the
                // asset protocol is opened for exactly one directory: the
                // recordings folder of the resolved data directory. The static
                // scope in the configuration is empty because the path is only
                // known at run time, and a static scope wide enough to cover it
                // would be a scope wide enough to cover everything else.
                let (data_dir, _) = paths::effective_data_dir();
                let gravacoes = paths::recordings_dir(&data_dir);
                if let Err(erro) = app
                    .asset_protocol_scope()
                    .allow_directory(&gravacoes, true)
                {
                    eprintln!(
                        "não foi possível liberar {} para reprodução: {erro}",
                        gravacoes.display()
                    );
                }
                supervisionar(app.handle().clone(), cliente.clone());
                harness::iniciar(app.handle().clone());

                if let Err(erro) = tray::instalar(app.handle(), cliente.clone()) {
                    eprintln!("bandeja indisponível: {erro}");
                }
                registrar_atalho(app.handle().clone(), cliente.clone());
                Ok(())
            }
        })
        .on_window_event({
            let cliente = cliente.clone();
            let aprovado = fechamento_aprovado.clone();
            move |window, event| {
                if let WindowEvent::CloseRequested { api, .. } = event {
                    if aprovado.load(Ordering::SeqCst) {
                        return;
                    }
                    // Hand the decision to the interface, which knows the
                    // recording's current duration and can state it. Closing
                    // silently would end a meeting nobody was told about.
                    api.prevent_close();
                    let snapshot = cliente.snapshot();
                    let saude = snapshot.saude.unwrap_or_default();
                    let _ = window.emit(
                        "app://fechamento-solicitado",
                        serde_json::json!({
                            "gravando": saude.gravacao_ativa,
                            "fila_pendente": saude.fila_pendente,
                        }),
                    );
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("erro ao construir o aplicativo VoxVault")
        .run(move |_app, event| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                // Last-resort path, which is what a system shutdown takes: no
                // dialog is possible, so an active recording is ended cleanly
                // with the time available. The service itself is never killed
                // -- it has a queue to finish and an idle policy of its own.
                if cliente.gravando() {
                    let _ = service::call(&cliente, "POST", "/gravacao/encerrar", None);
                }
            }
        });
}

/// Register the global shortcut that toggles the recording.
///
/// A failure here is reported and not fatal: another application may already
/// own the combination, and a window that refuses to open because a shortcut
/// was taken would be a worse tool than one without the shortcut.
fn registrar_atalho(app: tauri::AppHandle, cliente: service::ServiceClient) {
    use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

    let atalho = tray::atalho();
    let manipulador = {
        let cliente = cliente.clone();
        move |app: &tauri::AppHandle, _atalho: &_, evento: tauri_plugin_global_shortcut::ShortcutEvent| {
            // Only on release: a held key would otherwise start and stop the
            // recording repeatedly while the person is still pressing it.
            if evento.state() == ShortcutState::Released {
                tray::alternar_gravacao(app, &cliente);
            }
        }
    };

    if let Err(erro) = app.global_shortcut().on_shortcut(atalho, manipulador) {
        eprintln!("atalho global {atalho} indisponível: {erro}");
        let _ = app.emit(
            "atalho://indisponivel",
            format!(
                "O atalho global {atalho} não pôde ser registrado: outra aplicação \
                 provavelmente já o usa. O resto do VoxVault funciona normalmente."
            ),
        );
    }
}

/// Background supervision of the resident service.
fn supervisionar(app: tauri::AppHandle, cliente: service::ServiceClient) {
    std::thread::Builder::new()
        .name("voxvault-supervisor".into())
        .spawn(move || {
            let mut ultimo = String::new();
            loop {
                let transicao = cliente.passo();
                let snapshot = cliente.snapshot();

                match transicao {
                    service::Transition::Crashed => {
                        let _ = app.emit(
                            "servico://queda",
                            "O serviço local terminou inesperadamente. \
                             O aplicativo está tentando reconectar.",
                        );
                    }
                    service::Transition::Connected => {
                        let _ = app.emit("servico://conectado", ());
                    }
                    service::Transition::GaveUp => {
                        let _ = app.emit("servico://desistiu", &snapshot.detalhe);
                    }
                    service::Transition::Unchanged => {}
                }

                // Only wake the webview when the picture actually changed.
                if let Ok(serializado) = serde_json::to_string(&snapshot) {
                    if serializado != ultimo {
                        let _ = app.emit("servico://estado", &snapshot);
                        ultimo = serializado;
                    }
                }

                let gravando = snapshot
                    .saude
                    .as_ref()
                    .map(|saude| saude.gravacao_ativa)
                    .unwrap_or(false);
                std::thread::sleep(match snapshot.estado {
                    service::ServiceState::Conectado if gravando => INTERVALO_GRAVANDO,
                    service::ServiceState::Conectado => INTERVALO_CONECTADO,
                    service::ServiceState::Falho => INTERVALO_PARADO,
                    _ => INTERVALO_PROCURANDO,
                });
            }
        })
        .expect("não foi possível iniciar a supervisão do serviço");
}
