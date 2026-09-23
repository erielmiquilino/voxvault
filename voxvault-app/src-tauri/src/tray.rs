//! The tray icon: the answer to "is VoxVault listening to my microphone now?".
//!
//! The app is resident. Closing or minimizing the window destroys it and
//! leaves this icon, the service supervision and the notifications -- so the
//! icon has to be right on its own, with nothing else on screen to correct it.
//! Its four states differ by shape, not only by colour: a plain icon when
//! idle, a red dot when recording, two bars when paused, a gray icon with a
//! warning triangle when the local service is unavailable. It reflects only
//! VoxVault's own capture; Windows already shows who else uses the microphone.
//!
//! The state comes from one pure function over the service snapshot, which is
//! what the tests exercise. Changes this app causes are applied at once; those
//! caused elsewhere -- a recording started from the command line -- arrive with
//! the next supervision pass, five seconds at most while idle.
//!
//! Starting or stopping from the tray or the shortcut never opens the window:
//! the confirmation is the icon and a notification, because the person is in
//! another application, often in full screen, when they reach for it.
//!
//! What staying resident costs was measured, not assumed: ten minutes idle in
//! the tray with `tools/medir-custo.ps1 -Janela bandeja` averaged 0.02% of the
//! processors and 65 MB for this process and the service together -- 13 MB of
//! it here, with no WebView2 alive (docs/estado-da-implementacao.md). The
//! tooltip thread that ticks each second parks when nothing is recording.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, Wry};

use crate::avisos;
use crate::cli;
use crate::janela;
use crate::residente::{self, Residente};
use crate::service::{self, ServiceState, Snapshot};

/// Windows cuts a tray tooltip at 128 UTF-16 units.
const DICA_MAXIMA: usize = 120;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EstadoDaBandeja {
    Ocioso,
    Gravando,
    Pausado,
    Falha,
}

/// The part of `/gravacao` the tray shows.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GravacaoVista {
    pub uid: String,
    pub titulo: String,
    pub duracao_ms: u64,
    pub pausada: bool,
}

pub fn gravacao_vista(snapshot: &Snapshot) -> Option<GravacaoVista> {
    let gravacao = snapshot.gravacao.as_ref()?;
    if !gravacao.get("ativa").and_then(|v| v.as_bool()).unwrap_or(false) {
        return None;
    }
    let texto = |chave: &str| {
        gravacao
            .get(chave)
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .to_string()
    };
    Some(GravacaoVista {
        uid: texto("uid"),
        titulo: texto("titulo"),
        duracao_ms: gravacao.get("duracao_ms").and_then(|v| v.as_u64()).unwrap_or(0),
        pausada: gravacao.get("pausada").and_then(|v| v.as_bool()).unwrap_or(false),
    })
}

/// The one mapping from what the service says to what the icon shows.
pub fn estado_da_bandeja(snapshot: &Snapshot) -> EstadoDaBandeja {
    if snapshot.estado != ServiceState::Conectado {
        return EstadoDaBandeja::Falha;
    }
    let gravando = snapshot
        .saude
        .as_ref()
        .map(|saude| saude.gravacao_ativa)
        .unwrap_or(false);
    if !gravando {
        return EstadoDaBandeja::Ocioso;
    }
    match gravacao_vista(snapshot) {
        Some(gravacao) if gravacao.pausada => EstadoDaBandeja::Pausado,
        // Recording, even when the detail could not be read this pass: the
        // health says a capture is running, and that is what the icon is for.
        _ => EstadoDaBandeja::Gravando,
    }
}

pub fn relogio(ms: u64) -> String {
    let segundos = ms / 1000;
    format!(
        "{:02}:{:02}:{:02}",
        segundos / 3600,
        (segundos / 60) % 60,
        segundos % 60
    )
}

fn causa_curta(detalhe: &str) -> String {
    let linha = detalhe.lines().find(|l| !l.trim().is_empty()).unwrap_or("").trim();
    if linha.chars().count() <= 60 {
        linha.to_string()
    } else {
        format!("{}…", linha.chars().take(59).collect::<String>())
    }
}

/// The tooltip for a state. `decorrido_ms` is the recorded time as of now.
pub fn dica(
    estado: EstadoDaBandeja,
    gravacao: Option<&GravacaoVista>,
    decorrido_ms: u64,
    causa: &str,
) -> String {
    let texto = match estado {
        EstadoDaBandeja::Ocioso => "VoxVault — ocioso".to_string(),
        EstadoDaBandeja::Gravando => match gravacao.map(|g| g.titulo.trim()) {
            Some(titulo) if !titulo.is_empty() => {
                format!("VoxVault — gravando {} · {titulo}", relogio(decorrido_ms))
            }
            _ => format!("VoxVault — gravando {}", relogio(decorrido_ms)),
        },
        EstadoDaBandeja::Pausado => format!("VoxVault — pausado em {}", relogio(decorrido_ms)),
        EstadoDaBandeja::Falha => {
            format!("VoxVault — serviço indisponível: {}", causa_curta(causa))
        }
    };
    if texto.chars().count() <= DICA_MAXIMA {
        texto
    } else {
        format!("{}…", texto.chars().take(DICA_MAXIMA - 1).collect::<String>())
    }
}

/// Text and availability of the items that change with the state.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ItensDoMenu {
    pub alternar: (String, bool),
    pub pausar: (String, bool),
    pub ultima: bool,
}

pub fn itens_do_menu(estado: EstadoDaBandeja, tem_reuniao: bool) -> ItensDoMenu {
    let alternar = match estado {
        EstadoDaBandeja::Ocioso => ("Iniciar gravação", true),
        EstadoDaBandeja::Gravando | EstadoDaBandeja::Pausado => ("Encerrar gravação", true),
        EstadoDaBandeja::Falha => ("Iniciar gravação (serviço indisponível)", false),
    };
    let pausar = match estado {
        EstadoDaBandeja::Gravando => ("Pausar", true),
        EstadoDaBandeja::Pausado => ("Retomar", true),
        _ => ("Pausar", false),
    };
    ItensDoMenu {
        alternar: (alternar.0.to_string(), alternar.1),
        pausar: (pausar.0.to_string(), pausar.1),
        ultima: tem_reuniao,
    }
}

struct Aplicado {
    estado: Option<EstadoDaBandeja>,
    itens: Option<ItensDoMenu>,
    dica: String,
    gravacao: Option<GravacaoVista>,
    lida_em: Instant,
    causa: String,
}

impl Aplicado {
    fn decorrido_ms(&self) -> u64 {
        let base = self.gravacao.as_ref().map(|g| g.duracao_ms).unwrap_or(0);
        match self.estado {
            Some(EstadoDaBandeja::Gravando) => base + self.lida_em.elapsed().as_millis() as u64,
            _ => base,
        }
    }
}

pub struct Bandeja {
    icone: TrayIcon<Wry>,
    alternar: MenuItem<Wry>,
    pausar: MenuItem<Wry>,
    ultima: MenuItem<Wry>,
    imagens: [Image<'static>; 4],
    aplicado: Mutex<Aplicado>,
    relogio: OnceLock<std::thread::Thread>,
}

fn imagem(bytes: &'static [u8]) -> Image<'static> {
    Image::from_bytes(bytes).expect("ícone de bandeja embutido é um PNG válido")
}

impl Bandeja {
    fn imagem(&self, estado: EstadoDaBandeja) -> Image<'static> {
        self.imagens[match estado {
            EstadoDaBandeja::Ocioso => 0,
            EstadoDaBandeja::Gravando => 1,
            EstadoDaBandeja::Pausado => 2,
            EstadoDaBandeja::Falha => 3,
        }]
        .clone()
    }

    /// Bring icon, tooltip and menu in line with `snapshot`, touching only
    /// what changed.
    pub fn aplicar(&self, snapshot: &Snapshot, tem_reuniao: bool) {
        let estado = estado_da_bandeja(snapshot);
        let itens = itens_do_menu(estado, tem_reuniao);
        let mut aplicado = self.aplicado.lock().unwrap();
        if aplicado.estado != Some(estado) {
            let _ = self.icone.set_icon(Some(self.imagem(estado)));
        }
        if aplicado.itens.as_ref() != Some(&itens) {
            let _ = self.alternar.set_text(&itens.alternar.0);
            let _ = self.alternar.set_enabled(itens.alternar.1);
            let _ = self.pausar.set_text(&itens.pausar.0);
            let _ = self.pausar.set_enabled(itens.pausar.1);
            let _ = self.ultima.set_enabled(itens.ultima);
            aplicado.itens = Some(itens);
        }
        aplicado.estado = Some(estado);
        aplicado.gravacao = gravacao_vista(snapshot);
        aplicado.lida_em = Instant::now();
        aplicado.causa = snapshot.detalhe.clone();
        self.escrever_dica(&mut aplicado);
        let gravando = estado == EstadoDaBandeja::Gravando;
        drop(aplicado);
        if gravando {
            if let Some(relogio) = self.relogio.get() {
                relogio.unpark();
            }
        }
    }

    fn escrever_dica(&self, aplicado: &mut Aplicado) {
        let Some(estado) = aplicado.estado else {
            return;
        };
        let texto = dica(
            estado,
            aplicado.gravacao.as_ref(),
            aplicado.decorrido_ms(),
            &aplicado.causa,
        );
        if texto != aplicado.dica {
            let _ = self.icone.set_tooltip(Some(&texto));
            aplicado.dica = texto;
        }
    }

    /// The tooltip clock. Runs only while recording: the supervision pass is
    /// every two seconds then, and the requirement allows two seconds of lag,
    /// so a second of its own keeps the elapsed time honest. Parked otherwise.
    fn relogio(self: Arc<Self>) {
        loop {
            let gravando = {
                let mut aplicado = self.aplicado.lock().unwrap();
                let gravando = aplicado.estado == Some(EstadoDaBandeja::Gravando);
                if gravando {
                    self.escrever_dica(&mut aplicado);
                }
                gravando
            };
            if gravando {
                std::thread::sleep(Duration::from_secs(1));
            } else {
                std::thread::park();
            }
        }
    }
}

pub fn instalar(app: &AppHandle) -> tauri::Result<Arc<Bandeja>> {
    let inicial = itens_do_menu(EstadoDaBandeja::Falha, false);
    let alternar = MenuItem::with_id(app, "alternar", &inicial.alternar.0, false, None::<&str>)?;
    let pausar = MenuItem::with_id(app, "pausar", &inicial.pausar.0, false, None::<&str>)?;
    let abrir = MenuItem::with_id(app, "abrir", "Abrir o VoxVault", true, None::<&str>)?;
    let ultima = MenuItem::with_id(app, "ultima", "Abrir a última reunião", false, None::<&str>)?;
    let sair = MenuItem::with_id(app, "sair", "Sair do VoxVault", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &alternar,
            &pausar,
            &PredefinedMenuItem::separator(app)?,
            &abrir,
            &ultima,
            &PredefinedMenuItem::separator(app)?,
            &sair,
        ],
    )?;
    let imagens = [
        imagem(include_bytes!("../icons/bandeja/ocioso.png")),
        imagem(include_bytes!("../icons/bandeja/gravando.png")),
        imagem(include_bytes!("../icons/bandeja/pausado.png")),
        imagem(include_bytes!("../icons/bandeja/falha.png")),
    ];

    let icone = TrayIconBuilder::with_id("voxvault")
        .icon(imagens[3].clone())
        .tooltip("VoxVault — conectando ao serviço local")
        .menu(&menu)
        // The menu must not open on a left click, or the left click cannot mean
        // "show me the window", which is what a tray icon means everywhere else.
        .show_menu_on_left_click(false)
        .on_menu_event(|app, evento| {
            let app = app.clone();
            let id = evento.id().as_ref().to_string();
            // Every action can wait on the service or the command line; none
            // of that belongs on the thread that runs the event loop.
            std::thread::spawn(move || match id.as_str() {
                "alternar" => alternar_gravacao(&app),
                "pausar" => pausar_ou_retomar(&app),
                "abrir" => janela::abrir(&app, None),
                "ultima" => abrir_ultima_reuniao(&app),
                "sair" => residente::sair(&app),
                _ => {}
            });
        })
        .on_tray_icon_event(|tray, evento| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = evento
            {
                janela::abrir(tray.app_handle(), None);
            }
        })
        .build(app)?;

    let bandeja = Arc::new(Bandeja {
        icone,
        alternar,
        pausar,
        ultima,
        imagens,
        aplicado: Mutex::new(Aplicado {
            estado: None,
            itens: None,
            dica: String::new(),
            gravacao: None,
            lida_em: Instant::now(),
            causa: String::new(),
        }),
        relogio: OnceLock::new(),
    });
    let para_o_relogio = bandeja.clone();
    let relogio = std::thread::Builder::new()
        .name("voxvault-dica".into())
        .spawn(move || para_o_relogio.relogio())
        .expect("não foi possível iniciar o relógio da bandeja");
    let _ = bandeja.relogio.set(relogio.thread().clone());
    Ok(bandeja)
}

/// Start or stop, whichever the service says is valid right now.
///
/// The decision is the service's, not this window's: a recording started from
/// the command line is stopped by the same shortcut. The icon changes at once,
/// by a supervision pass run right after the request instead of waiting for the
/// next one.
pub fn alternar_gravacao(app: &AppHandle) {
    let estado = app.state::<Residente>();
    if estado.acao_da_gravacao.swap(true, Ordering::SeqCst) {
        avisos::mostrar(
            app,
            avisos::Aviso::falha_de_acao(
                "A ação anterior ainda está em andamento",
                "O VoxVault ainda espera o serviço local responder ao pedido anterior. \
                 O ícone muda assim que ele responder.",
            ),
        );
        return;
    }
    struct Liberar<'a>(&'a AtomicBool);
    impl Drop for Liberar<'_> {
        fn drop(&mut self) {
            self.0.store(false, Ordering::SeqCst);
        }
    }
    let _liberar = Liberar(&estado.acao_da_gravacao);
    let gravando = estado.cliente.gravando();
    let (caminho, corpo) = if gravando {
        ("/gravacao/encerrar", None)
    } else {
        ("/gravacao/iniciar", Some("{\"titulo\":\"\"}"))
    };
    if gravando {
        // Ending answers only once the audio is compressed -- seconds, for a
        // long meeting -- but the service stops capturing the moment it gets
        // the request. A pass shortly after shows that, instead of leaving
        // the icon claiming a capture that already ended.
        let app = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(400));
            residente::ciclo(&app);
        });
    }
    match service::call_detalhado(&estado.cliente, "POST", caminho, corpo) {
        Ok(_) => {
            residente::ciclo(app);
        }
        // Out of time is not refused: the service may still be opening the
        // devices, and a recording that starts late is announced by the icon
        // and its own notification. Saying "it failed" would be false.
        Err(erro) if erro.prazo_esgotado => {
            avisos::mostrar(
                app,
                avisos::Aviso::falha_de_acao(
                    if gravando {
                        "O encerramento ainda não foi confirmado"
                    } else {
                        "O início ainda não foi confirmado"
                    },
                    "O serviço local não respondeu dentro do prazo — os dispositivos \
                     de áudio podem estar lentos ou travados. O ícone da bandeja \
                     mostra o estado real assim que ele responder.",
                ),
            );
            residente::ciclo(app);
        }
        Err(erro) => {
            avisos::mostrar(
                app,
                avisos::Aviso::falha_de_acao(
                    if gravando {
                        "Não foi possível encerrar a gravação"
                    } else {
                        "Não foi possível iniciar a gravação"
                    },
                    &erro.mensagem,
                ),
            );
            residente::ciclo(app);
        }
    }
}

fn pausar_ou_retomar(app: &AppHandle) {
    let estado = app.state::<Residente>();
    let pausada = gravacao_vista(&estado.cliente.snapshot())
        .map(|g| g.pausada)
        .unwrap_or(false);
    let caminho = if pausada {
        "/gravacao/retomar"
    } else {
        "/gravacao/pausar"
    };
    if let Err(causa) = service::call(&estado.cliente, "POST", caminho, None) {
        avisos::mostrar(
            app,
            avisos::Aviso::falha_de_acao(
                if pausada {
                    "Não foi possível retomar a gravação"
                } else {
                    "Não foi possível pausar a gravação"
                },
                &causa,
            ),
        );
    }
    residente::ciclo(app);
}

/// The most recent meeting, as the library lists it, opened in the window.
fn abrir_ultima_reuniao(app: &AppHandle) {
    let rota = cli::list(1)
        .ok()
        .and_then(|payload| {
            crate::library::listar_do_nucleo(&payload)
                .into_iter()
                .next()
                .map(|resumo| format!("biblioteca/{}", resumo.uid))
        })
        .unwrap_or_else(|| "biblioteca".to_string());
    janela::abrir(app, Some(&rota));
}

#[cfg(test)]
mod testes {
    use super::*;
    use crate::service::Health;

    fn snapshot(estado: ServiceState, gravando: bool, gravacao: Option<serde_json::Value>) -> Snapshot {
        Snapshot {
            estado,
            endereco: None,
            data_dir_do_servico: None,
            saude: Some(Health {
                gravacao_ativa: gravando,
                ..Health::default()
            }),
            gravacao,
            deteccao: None,
            detalhe: "O serviço local falhou ao iniciar 3 vezes seguidas.\n\nCausa: x".into(),
            acao: None,
            tentativas_recentes: 0,
            pode_tentar_de_novo: false,
            caminho_do_log: None,
            pid_do_servico: None,
        }
    }

    fn gravacao(pausada: bool) -> serde_json::Value {
        serde_json::json!({
            "ativa": true, "pausada": pausada, "uid": "abc", "titulo": "Planejamento",
            "duracao_ms": 754_000,
        })
    }

    #[test]
    fn os_quatro_estados() {
        assert_eq!(
            estado_da_bandeja(&snapshot(ServiceState::Conectado, false, None)),
            EstadoDaBandeja::Ocioso
        );
        assert_eq!(
            estado_da_bandeja(&snapshot(ServiceState::Conectado, true, Some(gravacao(false)))),
            EstadoDaBandeja::Gravando
        );
        assert_eq!(
            estado_da_bandeja(&snapshot(ServiceState::Conectado, true, Some(gravacao(true)))),
            EstadoDaBandeja::Pausado
        );
        assert_eq!(
            estado_da_bandeja(&snapshot(ServiceState::Falho, false, None)),
            EstadoDaBandeja::Falha
        );
    }

    #[test]
    fn fora_de_conectado_e_sempre_falha() {
        for estado in [
            ServiceState::Desconhecido,
            ServiceState::Procurando,
            ServiceState::Indisponivel,
            ServiceState::Falho,
        ] {
            // Even with a stale "recording" in hand: an unreachable service
            // cannot be vouched for, and the icon must not claim a capture.
            assert_eq!(
                estado_da_bandeja(&snapshot(estado, true, Some(gravacao(false)))),
                EstadoDaBandeja::Falha,
                "{estado:?}"
            );
        }
    }

    #[test]
    fn gravando_sem_o_detalhe_continua_gravando() {
        assert_eq!(
            estado_da_bandeja(&snapshot(ServiceState::Conectado, true, None)),
            EstadoDaBandeja::Gravando
        );
    }

    #[test]
    fn as_dicas_de_cada_estado() {
        let g = gravacao_vista(&snapshot(ServiceState::Conectado, true, Some(gravacao(false))));
        assert_eq!(
            dica(EstadoDaBandeja::Gravando, g.as_ref(), 754_000, ""),
            "VoxVault — gravando 00:12:34 · Planejamento"
        );
        assert_eq!(dica(EstadoDaBandeja::Ocioso, None, 0, ""), "VoxVault — ocioso");
        assert_eq!(
            dica(EstadoDaBandeja::Pausado, g.as_ref(), 754_000, ""),
            "VoxVault — pausado em 00:12:34"
        );
        assert_eq!(
            dica(EstadoDaBandeja::Falha, None, 0, "O serviço local falhou ao iniciar 3 vezes.\n\nx"),
            "VoxVault — serviço indisponível: O serviço local falhou ao iniciar 3 vezes."
        );
        let longo = "x".repeat(300);
        assert!(dica(EstadoDaBandeja::Falha, None, 0, &longo).chars().count() <= DICA_MAXIMA);
    }

    #[test]
    fn habilitacao_do_menu_por_estado() {
        let ocioso = itens_do_menu(EstadoDaBandeja::Ocioso, true);
        assert_eq!(ocioso.alternar, ("Iniciar gravação".to_string(), true));
        assert!(!ocioso.pausar.1);
        assert!(ocioso.ultima);

        let gravando = itens_do_menu(EstadoDaBandeja::Gravando, true);
        assert_eq!(gravando.alternar, ("Encerrar gravação".to_string(), true));
        assert_eq!(gravando.pausar, ("Pausar".to_string(), true));

        let pausado = itens_do_menu(EstadoDaBandeja::Pausado, true);
        assert_eq!(pausado.alternar, ("Encerrar gravação".to_string(), true));
        assert_eq!(pausado.pausar, ("Retomar".to_string(), true));

        let falha = itens_do_menu(EstadoDaBandeja::Falha, true);
        assert!(!falha.alternar.1, "iniciar fica desabilitado sem serviço");
        assert!(falha.alternar.0.contains("indisponível"));
        assert!(!falha.pausar.1);

        assert!(!itens_do_menu(EstadoDaBandeja::Ocioso, false).ultima, "sem reunião");
    }
}
