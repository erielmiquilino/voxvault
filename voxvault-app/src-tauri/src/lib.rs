//! Application host.
//!
//! The app is resident: closing or minimizing the window collapses it to the
//! tray, and the window itself is destroyed, because a hidden WebView2 frees
//! nothing. What keeps running without it is small on purpose -- the tray, one
//! supervision thread, the global shortcut and the notifications. Only "Sair
//! do VoxVault", the end of the session or a shutdown ends the process, and
//! none of them ends the resident service, which owns the transcription queue.
//!
//! The supervisor's cadence is part of the cost budget. It is a loopback TCP
//! connect and two small GETs every five seconds while idle, every two while
//! recording. It never polls the command line -- each call there is a Python
//! interpreter start -- and it wakes the window only when the picture changed.

mod atalho;
mod ativacao;
mod avisos;
mod cli;
mod commands;
mod hardware;
mod harness;
mod http;
mod janela;
mod library;
mod paths;
mod prefs;
mod preparo;
mod residente;
mod service;
mod system;
mod tray;

use std::time::Duration;

use tauri::{Manager, RunEvent, WindowEvent};

/// While attached and nothing is being recorded. Five seconds is the ceiling
/// the requirement sets for the tray to notice a recording started elsewhere.
const INTERVALO_CONECTADO: Duration = Duration::from_secs(5);
/// While a recording is running: close enough that a warning reaches the
/// screen while it still means something.
const INTERVALO_GRAVANDO: Duration = Duration::from_secs(2);
/// While looking for the service, or right after losing it.
const INTERVALO_PROCURANDO: Duration = Duration::from_secs(3);
/// After the retry budget is spent: the pass is a no-op, so this is only how
/// often the thread wakes to notice a re-arm.
const INTERVALO_PARADO: Duration = Duration::from_secs(5);

/// The argument the start with Windows passes: come up in the tray only.
pub const ARGUMENTO_BANDEJA: &str = "--bandeja";

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let cliente = service::ServiceClient::new();
    let para_a_saida = cliente.clone();

    tauri::Builder::default()
        // Registered first, as the plugin requires: a second launch has to be
        // intercepted before this process builds a window of its own. With the
        // app in the tray, the window is built again on the last route -- or
        // on the one a clicked notification's address names.
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            match args.iter().find_map(|arg| ativacao::interpretar(arg)) {
                Some(pedido) => avisos::atender(app, pedido),
                None => janela::abrir(app, None),
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(
            tauri_plugin_autostart::Builder::new()
                .args([ARGUMENTO_BANDEJA])
                .build(),
        )
        .manage(cliente.clone())
        .manage(residente::Residente::novo(cliente))
        .invoke_handler(tauri::generate_handler![
            commands::ambiente_estado,
            commands::preparo_plano,
            commands::preparo_iniciar,
            commands::modelo_baixar,
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
            commands::app_registrar_rota,
            commands::app_rota_inicial,
            commands::app_janela_pronta,
            commands::app_ultima_abertura,
            commands::aplicativo_ler,
            commands::notificacoes_definir,
            commands::atalho_trocar,
            commands::inicio_com_o_windows_definir,
            commands::notificacao_de_teste,
        ])
        .setup(|app| {
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

            let handle = app.handle().clone();
            match tray::instalar(&handle) {
                Ok(bandeja) => {
                    let _ = handle.state::<residente::Residente>().bandeja.set(bandeja);
                }
                Err(erro) => eprintln!("bandeja indisponível: {erro}"),
            }
            atalho::registrar_inicial(&handle);
            supervisionar(handle.clone());
            harness::iniciar(handle.clone());

            // A notification clicked after the app was quit starts it with its
            // address.
            if let Err(erro) = ativacao::registrar_esquema() {
                eprintln!("o clique nas notificações não abrirá o aplicativo: {erro}");
            }
            let pedido = std::env::args().find_map(|arg| ativacao::interpretar(&arg));

            // Started with Windows: the tray only, and no recording -- that is
            // always a person's decision.
            if let Some(pedido) = pedido {
                avisos::atender(&handle, pedido);
            } else if !std::env::args().any(|arg| arg == ARGUMENTO_BANDEJA) {
                janela::construir(&handle)?;
            }
            Ok(())
        })
        .on_window_event(|window, event| match event {
            // Closing is not quitting. The window is destroyed, which is what
            // the default close does, and the app stays in the tray; nothing
            // is asked and no recording is touched.
            WindowEvent::CloseRequested { .. } => janela::recolhida(window.app_handle()),
            // Minimizing is collapsing too: a minimized window would keep
            // WebView2's memory and a taskbar button for nothing.
            WindowEvent::Resized(_) if window.is_minimized().unwrap_or(false) => {
                let _ = window.destroy();
                janela::recolhida(window.app_handle());
            }
            _ => {}
        })
        .build(tauri::generate_context!())
        .expect("erro ao construir o aplicativo VoxVault")
        .run(move |_app, event| match event {
            // The last window went away. That is the tray, not the end: only
            // an explicit exit carries a code.
            RunEvent::ExitRequested { code: None, api, .. } => api.prevent_exit(),
            // The session is ending or the machine is shutting down: no dialog
            // is possible, so an active recording is ended cleanly with the
            // time available. The service itself is never killed.
            RunEvent::Exit if para_a_saida.gravando() => {
                let _ = service::call(&para_a_saida, "POST", "/gravacao/encerrar", None);
            }
            _ => {}
        });
}

/// Background supervision of the resident service, window or no window.
fn supervisionar(app: tauri::AppHandle) {
    std::thread::Builder::new()
        .name("voxvault-supervisor".into())
        .spawn(move || loop {
            let (_, snapshot) = residente::ciclo(&app);
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
        })
        .expect("não foi possível iniciar a supervisão do serviço");
}
