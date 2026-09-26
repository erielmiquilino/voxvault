//! System notifications, working with the window closed.
//!
//! Six categories, each one a switch in the settings, all on by default:
//! recording started, recording ended, transcription done, transcription
//! failed, capture warnings, and meeting detected -- the last one with a
//! "Gravar" button. Two rules keep them trustworthy rather than noisy: nothing
//! is notified twice, and nothing that happened before the app was open is
//! notified at all. Both are decided by [`Observador`], which is plain state
//! over what the service reports and is what the tests exercise.
//!
//! Where each one comes from:
//!
//! - recording started and ended: the tray state changing between idle and
//!   recording, whoever caused it, keyed by the recording's identifier so the
//!   tray's own start is notified once;
//! - transcription done and failed: the service's `/eventos`, read by cursor;
//! - capture warnings: new device entries in `/gravacao.avisos`, and the
//!   microphone delivering digital silence -- not one non-zero sample -- for 60
//!   seconds, once per episode. A quiet room is not digital silence: a live
//!   microphone always has a noise floor;
//! - meeting detected: `/deteccao` starting to report one.
//!
//! Toasts go through WinRT directly because the button is required and the
//! official notification plugin has no action buttons on desktop. Clicking
//! one opens a `voxvault://` address rather than calling back into this
//! process, which is the only way a click in the notification center reaches
//! it; see [`crate::ativacao`].

use std::collections::HashSet;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager};

use crate::ativacao::{self, Pedido};
use crate::janela;
use crate::prefs::Notificacoes;
use crate::residente::Residente;
use crate::service::Snapshot;
use crate::tray::{self, EstadoDaBandeja};

/// The application's identity for Windows, registered by the Start menu
/// shortcut the installer creates.
const AUMID: &str = "com.erielmiquilino.voxvault";
/// PowerShell's identity, registered on every Windows: the one a toast can use
/// from a build folder, where no shortcut registers the application's own.
const AUMID_DO_POWERSHELL: &str =
    r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe";
/// Microphone silence that becomes a warning.
const SILENCIO_S: f64 = 60.0;
/// System-track silence that becomes a warning. Longer than the
/// microphone's: nobody else talking for a while is ordinary, but two minutes
/// of nothing at all is how a call playing on another output looked -- a
/// Teams call on a Bluetooth headset, recorded without the other side.
const SILENCIO_DO_SISTEMA_S: f64 = 120.0;
/// How long a "meeting detected" suggestion stays a suggestion. After that,
/// acting on the notification opens the window instead of recording.
pub const VALIDADE_DA_SUGESTAO: Duration = Duration::from_secs(120);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Categoria {
    GravacaoIniciada,
    GravacaoEncerrada,
    TranscricaoConcluida,
    TranscricaoFalha,
    AvisosDeCaptura,
    ReuniaoDetectada,
}

impl Categoria {
    pub fn permitida(self, chaves: &Notificacoes) -> bool {
        match self {
            Categoria::GravacaoIniciada => chaves.gravacao_iniciada,
            Categoria::GravacaoEncerrada => chaves.gravacao_encerrada,
            Categoria::TranscricaoConcluida => chaves.transcricao_concluida,
            Categoria::TranscricaoFalha => chaves.transcricao_falha,
            Categoria::AvisosDeCaptura => chaves.avisos_de_captura,
            Categoria::ReuniaoDetectada => chaves.reuniao_detectada,
        }
    }

    pub fn de_nome(nome: &str) -> Option<Self> {
        Some(match nome {
            "gravacao_iniciada" => Categoria::GravacaoIniciada,
            "gravacao_encerrada" => Categoria::GravacaoEncerrada,
            "transcricao_concluida" => Categoria::TranscricaoConcluida,
            "transcricao_falha" => Categoria::TranscricaoFalha,
            "avisos_de_captura" => Categoria::AvisosDeCaptura,
            "reuniao_detectada" => Categoria::ReuniaoDetectada,
            _ => return None,
        })
    }
}

/// What a click on the notification opens.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Destino {
    Nada,
    Gravacao,
    Reuniao(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Aviso {
    /// Never notified twice: `tipo:uid:seq`, or the like.
    pub chave: String,
    /// `None` for what is not a category -- the one-time tray notice, and a
    /// failed tray action -- which is always shown.
    pub categoria: Option<Categoria>,
    pub titulo: String,
    pub texto: String,
    pub destino: Destino,
    /// The "Gravar" button of a meeting detection.
    pub gravar: bool,
}

impl Aviso {
    fn de(categoria: Categoria, chave: String, titulo: &str, texto: String, destino: Destino) -> Self {
        Aviso {
            chave,
            categoria: Some(categoria),
            titulo: titulo.to_string(),
            texto,
            destino,
            gravar: false,
        }
    }

    pub fn bandeja() -> Self {
        Aviso {
            chave: "bandeja".into(),
            categoria: None,
            titulo: "O VoxVault continua na bandeja".into(),
            texto: "Fechar e minimizar recolhem o aplicativo, que segue pronto para \
                    gravar pelo ícone e pelo atalho. Para encerrar de vez, use \
                    \"Sair do VoxVault\" no menu do ícone."
                .into(),
            destino: Destino::Nada,
            gravar: false,
        }
    }

    pub fn falha_de_acao(titulo: &str, causa: &str) -> Self {
        Aviso {
            chave: String::new(),
            categoria: None,
            titulo: titulo.to_string(),
            texto: causa.lines().next().unwrap_or(causa).to_string(),
            destino: Destino::Gravacao,
            gravar: false,
        }
    }

    /// One sample of each category, for checking that notifications reach the
    /// screen on this machine.
    pub fn exemplo(categoria: Categoria) -> Self {
        let chave = String::new();
        match categoria {
            Categoria::GravacaoIniciada => Aviso::de(categoria, chave, "Gravação iniciada",
                "Reunião de exemplo — o VoxVault está gravando o microfone e o áudio do sistema.".into(),
                Destino::Gravacao),
            Categoria::GravacaoEncerrada => Aviso::de(categoria, chave, "Gravação encerrada",
                "Reunião de exemplo · 00:42:10 — enviada para transcrição.".into(), Destino::Gravacao),
            Categoria::TranscricaoConcluida => Aviso::de(categoria, chave, "Transcrição concluída",
                "Reunião de exemplo".into(), Destino::Gravacao),
            Categoria::TranscricaoFalha => Aviso::de(categoria, chave, "A transcrição falhou",
                "Reunião de exemplo: a GPU ficou sem memória.".into(), Destino::Gravacao),
            Categoria::AvisosDeCaptura => Aviso::de(categoria, chave, "Aviso de captura",
                "O microfone está entregando silêncio absoluto há 1 minuto — confira se não está mudo.".into(),
                Destino::Gravacao),
            Categoria::ReuniaoDetectada => Aviso {
                gravar: true,
                ..Aviso::de(categoria, chave, "Reunião detectada",
                    "Teams está usando o microfone. Gravar esta reunião?".into(), Destino::Gravacao)
            },
        }
    }
}

fn humanizar(aviso: &str) -> String {
    aviso
        .replace("trilha 'mic'", "Microfone")
        .replace("trilha 'system'", "Áudio do sistema")
}

/// A capture warning that is about a device: lost, recovering, moved to
/// another, ended as incomplete.
fn e_de_dispositivo(aviso: &str) -> bool {
    aviso.starts_with("trilha '") || aviso.contains("dispositivo")
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Cursor {
    execucao: String,
    seq: u64,
}

/// What has been seen, so each thing is notified once and only if it happened
/// while the app was listening.
#[derive(Debug, Default)]
pub struct Observador {
    iniciado: bool,
    gravacao: Option<(String, String)>,
    gravacao_duracao_ms: u64,
    avisos_vistos: (String, usize),
    silencio_em_episodio: bool,
    silencios: u32,
    silencio_do_sistema_em_episodio: bool,
    silencios_do_sistema: u32,
    deteccao_em_episodio: Option<String>,
    deteccoes: u32,
    cursor: Option<Cursor>,
    notificados: HashSet<String>,
}

impl Observador {
    fn novo(&mut self, aviso: Aviso, saida: &mut Vec<Aviso>) {
        if self.notificados.len() > 4096 {
            self.notificados.clear();
        }
        if self.notificados.insert(aviso.chave.clone()) {
            saida.push(aviso);
        }
    }

    /// One snapshot of the service: transitions, capture warnings, silence and
    /// detection. The first one only records where things stand.
    pub fn observar(&mut self, snapshot: &Snapshot) -> Vec<Aviso> {
        let estado = tray::estado_da_bandeja(snapshot);
        let gravacao = tray::gravacao_vista(snapshot);
        let mut saida = Vec::new();
        let gravando = matches!(estado, EstadoDaBandeja::Gravando | EstadoDaBandeja::Pausado);

        if !self.iniciado {
            self.iniciado = true;
            if let Some(g) = &gravacao {
                self.gravacao = Some((g.uid.clone(), g.titulo.clone()));
                self.gravacao_duracao_ms = g.duracao_ms;
                self.avisos_vistos = (g.uid.clone(), avisos_de(snapshot).len());
                self.silencio_em_episodio = silencio_de(snapshot, "mic") >= SILENCIO_S;
                self.silencio_do_sistema_em_episodio =
                    silencio_de(snapshot, "system") >= SILENCIO_DO_SISTEMA_S;
            }
            self.deteccao_em_episodio = deteccao_de(snapshot);
            return saida;
        }

        // -- recording started and ended -----------------------------------
        match (&gravacao, estado) {
            (Some(g), EstadoDaBandeja::Gravando | EstadoDaBandeja::Pausado) if !g.uid.is_empty() => {
                let mudou = self.gravacao.as_ref().map(|(uid, _)| uid != &g.uid).unwrap_or(true);
                if mudou {
                    if let Some((anterior, titulo)) = self.gravacao.take() {
                        self.encerrada(&anterior, &titulo, &mut saida);
                    }
                    self.novo(
                        Aviso::de(
                            Categoria::GravacaoIniciada,
                            format!("iniciada:{}", g.uid),
                            "Gravação iniciada",
                            format!(
                                "{} — o VoxVault está gravando o microfone e o áudio do sistema.",
                                nome_da_reuniao(&g.titulo)
                            ),
                            Destino::Gravacao,
                        ),
                        &mut saida,
                    );
                    self.gravacao = Some((g.uid.clone(), g.titulo.clone()));
                }
                self.gravacao_duracao_ms = g.duracao_ms;
            }
            (_, EstadoDaBandeja::Ocioso) => {
                if let Some((anterior, titulo)) = self.gravacao.take() {
                    self.encerrada(&anterior, &titulo, &mut saida);
                }
            }
            // Recording without the detail this pass, or the service out of
            // reach: nothing can be said about a transition, so nothing is.
            _ => {}
        }

        // -- capture warnings ----------------------------------------------
        if let Some(g) = gravacao.as_ref().filter(|_| gravando) {
            let avisos = avisos_de(snapshot);
            if self.avisos_vistos.0 != g.uid {
                self.avisos_vistos = (g.uid.clone(), 0);
            }
            for (indice, aviso) in avisos.iter().enumerate().skip(self.avisos_vistos.1) {
                if e_de_dispositivo(aviso) {
                    self.novo(
                        Aviso::de(
                            Categoria::AvisosDeCaptura,
                            format!("captura:{}:{indice}", g.uid),
                            "Aviso de captura",
                            humanizar(aviso),
                            Destino::Gravacao,
                        ),
                        &mut saida,
                    );
                }
            }
            self.avisos_vistos.1 = self.avisos_vistos.1.max(avisos.len());

            // A paused recording captures nothing, so its silence says nothing.
            if estado == EstadoDaBandeja::Gravando {
                let mudo = silencio_de(snapshot, "mic") >= SILENCIO_S;
                if mudo && !self.silencio_em_episodio {
                    self.silencios += 1;
                    self.novo(
                        Aviso::de(
                            Categoria::AvisosDeCaptura,
                            format!("silencio:{}:{}", g.uid, self.silencios),
                            "Aviso de captura",
                            "O microfone está entregando silêncio absoluto há 1 minuto — \
                             confira se não está mudo."
                                .into(),
                            Destino::Gravacao,
                        ),
                        &mut saida,
                    );
                }
                self.silencio_em_episodio = mudo;

                let sistema_mudo = silencio_de(snapshot, "system") >= SILENCIO_DO_SISTEMA_S;
                if sistema_mudo && !self.silencio_do_sistema_em_episodio {
                    self.silencios_do_sistema += 1;
                    self.novo(
                        Aviso::de(
                            Categoria::AvisosDeCaptura,
                            format!("silencio-sistema:{}:{}", g.uid, self.silencios_do_sistema),
                            "Aviso de captura",
                            silencio_do_sistema(dispositivo_de(snapshot, "system").as_deref()),
                            Destino::Gravacao,
                        ),
                        &mut saida,
                    );
                }
                self.silencio_do_sistema_em_episodio = sistema_mudo;
            }
        } else if !gravando {
            self.silencio_em_episodio = false;
            self.silencio_do_sistema_em_episodio = false;
        }

        // -- meeting detected ----------------------------------------------
        // Only asked for while idle; a pass without the answer leaves the
        // episode alone, or stopping a recording mid-call would suggest
        // recording the very same call again.
        if snapshot.deteccao.is_some() {
            let atual = deteccao_de(snapshot);
            if let (Some(aplicativo), false) = (&atual, gravando) {
                if self.deteccao_em_episodio.is_none() {
                    self.deteccoes += 1;
                    let mut aviso = Aviso::de(
                        Categoria::ReuniaoDetectada,
                        format!("deteccao:{aplicativo}:{}", self.deteccoes),
                        "Reunião detectada",
                        format!("{aplicativo} está usando o microfone. Gravar esta reunião?"),
                        Destino::Gravacao,
                    );
                    aviso.gravar = true;
                    self.novo(aviso, &mut saida);
                }
            }
            self.deteccao_em_episodio = atual;
        }

        saida
    }

    fn encerrada(&mut self, uid: &str, titulo: &str, saida: &mut Vec<Aviso>) {
        let duracao = tray::relogio(self.gravacao_duracao_ms);
        self.novo(
            Aviso::de(
                Categoria::GravacaoEncerrada,
                format!("encerrada:{uid}"),
                "Gravação encerrada",
                format!("{} · {duracao} — enviada para transcrição.", nome_da_reuniao(titulo)),
                Destino::Gravacao,
            ),
            saida,
        );
    }

    /// The cursor for the next `/eventos` question: `None` on the very first,
    /// which asks the service only where "now" is.
    pub fn desde(&self) -> Option<u64> {
        self.cursor.as_ref().map(|c| c.seq)
    }

    /// One answer of `/eventos`. The first only records the newest number;
    /// a new `execucao` means the service restarted and numbering started
    /// over, and everything in the new run happened with this app listening.
    pub fn eventos(&mut self, resposta: &serde_json::Value) -> Vec<Aviso> {
        let execucao = resposta
            .get("execucao")
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .to_string();
        let ultimo = resposta.get("ultimo").and_then(|v| v.as_u64()).unwrap_or(0);
        let mut saida = Vec::new();
        let Some(cursor) = self.cursor.clone() else {
            self.cursor = Some(Cursor { execucao, seq: ultimo });
            return saida;
        };
        if cursor.execucao != execucao {
            self.cursor = Some(Cursor { execucao, seq: 0 });
            return saida;
        }
        let eventos = resposta
            .get("eventos")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        for evento in eventos {
            let seq = evento.get("seq").and_then(|v| v.as_u64()).unwrap_or(0);
            if seq <= cursor.seq {
                continue;
            }
            let texto = |chave: &str| {
                evento
                    .get(chave)
                    .and_then(|v| v.as_str())
                    .unwrap_or_default()
                    .to_string()
            };
            let (tipo, uid) = (texto("tipo"), texto("uid"));
            if uid.is_empty() {
                continue;
            }
            match tipo.as_str() {
                "pronta" => self.novo(
                    Aviso::de(
                        Categoria::TranscricaoConcluida,
                        format!("pronta:{uid}:{seq}"),
                        "Transcrição concluída",
                        format!("{} — pronta para ler e buscar.", nome_da_reuniao(&texto("titulo"))),
                        Destino::Reuniao(uid),
                    ),
                    &mut saida,
                ),
                "falhou" => self.novo(
                    Aviso::de(
                        Categoria::TranscricaoFalha,
                        format!("falhou:{uid}:{seq}"),
                        "A transcrição falhou",
                        format!("{}: {}", nome_da_reuniao(&texto("titulo")), texto("detalhe")),
                        Destino::Reuniao(uid),
                    ),
                    &mut saida,
                ),
                _ => {}
            }
        }
        self.cursor = Some(Cursor {
            execucao,
            seq: ultimo.max(cursor.seq),
        });
        saida
    }
}

fn nome_da_reuniao(titulo: &str) -> String {
    let titulo = titulo.trim();
    if titulo.is_empty() {
        "Reunião sem título".to_string()
    } else {
        titulo.to_string()
    }
}

fn avisos_de(snapshot: &Snapshot) -> Vec<String> {
    snapshot
        .gravacao
        .as_ref()
        .and_then(|g| g.get("avisos"))
        .and_then(|v| v.as_array())
        .map(|itens| {
            itens
                .iter()
                .filter_map(|item| item.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

/// How long a track has delivered nothing but digital silence.
fn silencio_de(snapshot: &Snapshot, trilha: &str) -> f64 {
    snapshot
        .gravacao
        .as_ref()
        .and_then(|g| g.pointer(&format!("/niveis/{trilha}/silencio_ha_s")))
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0)
}

/// What a track records from right now, by name.
fn dispositivo_de(snapshot: &Snapshot, trilha: &str) -> Option<String> {
    snapshot
        .gravacao
        .as_ref()
        .and_then(|g| g.pointer(&format!("/dispositivos/{trilha}")))
        .and_then(|v| v.as_str())
        .filter(|nome| !nome.is_empty())
        .map(str::to_string)
}

/// Conditional on purpose: the others being quiet is silence too.
fn silencio_do_sistema(saida: Option<&str>) -> String {
    let gravando = saida.map(|nome| format!(", gravando \u{201c}{nome}\u{201d}")).unwrap_or_default();
    format!(
        "O áudio do sistema está em silêncio há 2 minutos{gravando}. Se os outros \
         participantes estão falando, o som deles está saindo por outra saída."
    )
}

fn deteccao_de(snapshot: &Snapshot) -> Option<String> {
    snapshot
        .deteccao
        .as_ref()
        .and_then(|d| d.get("deteccao"))
        .filter(|d| !d.is_null())
        .map(|d| {
            d.get("aplicativo")
                .and_then(|v| v.as_str())
                .unwrap_or("Um aplicativo de reunião")
                .to_string()
        })
}

// -- showing them -----------------------------------------------------------

/// Which identity the toast is shown under. Installed, the application's own,
/// which the Start menu shortcut registers; run from a build folder, where no
/// shortcut exists and Windows would drop the toast silently, PowerShell's.
pub fn identidade() -> &'static str {
    use std::sync::OnceLock;
    static ID: OnceLock<&'static str> = OnceLock::new();
    ID.get_or_init(|| {
        let atalho = |raiz: Option<std::ffi::OsString>| {
            raiz.map(|r| {
                std::path::PathBuf::from(r)
                    .join(r"Microsoft\Windows\Start Menu\Programs\VoxVault.lnk")
                    .exists()
            })
            .unwrap_or(false)
        };
        if atalho(std::env::var_os("APPDATA")) || atalho(std::env::var_os("ProgramData")) {
            AUMID
        } else {
            AUMID_DO_POWERSHELL
        }
    })
}

/// Show one notification, if its category is on.
pub fn mostrar(app: &AppHandle, aviso: Aviso) {
    let chaves = app
        .state::<Residente>()
        .preferencias
        .lock()
        .unwrap()
        .notificacoes
        .clone();
    if let Some(categoria) = aviso.categoria {
        if !categoria.permitida(&chaves) {
            return;
        }
    }
    let lancamento = ativacao::endereco_para(&aviso.destino);
    let botao = aviso
        .gravar
        .then(|| ativacao::endereco_para_gravar(&ativacao::emitir_token(Instant::now())));
    let xml = xml_do_aviso(&aviso, lancamento.as_deref(), botao.as_deref());
    if let Err(erro) = exibir(&xml) {
        eprintln!("notificação não exibida ({}): {erro}", aviso.titulo);
    }
}

/// The toast, as the XML Windows reads. A click on it opens `lancamento`, and
/// the "Gravar" button, `botao`; with neither, clicking only dismisses it.
pub fn xml_do_aviso(aviso: &Aviso, lancamento: Option<&str>, botao: Option<&str>) -> String {
    let mut xml = String::from("<toast");
    if let Some(endereco) = lancamento {
        xml.push_str(&format!(
            " activationType=\"protocol\" launch=\"{}\"",
            escapar(endereco)
        ));
    }
    xml.push_str(if aviso.gravar {
        " duration=\"long\">"
    } else {
        " duration=\"short\">"
    });
    xml.push_str(&format!(
        "<visual><binding template=\"ToastGeneric\"><text>{}</text><text>{}</text>\
         </binding></visual>",
        escapar(&aviso.titulo),
        escapar(&aviso.texto)
    ));
    if let Some(endereco) = botao {
        xml.push_str(&format!(
            "<actions><action content=\"Gravar\" activationType=\"protocol\" \
             arguments=\"{}\"/></actions>",
            escapar(endereco)
        ));
    }
    xml.push_str("</toast>");
    xml
}

/// Titles come from people -- a meeting can be called anything.
fn escapar(texto: &str) -> String {
    let mut saida = String::with_capacity(texto.len());
    for c in texto.chars() {
        match c {
            '&' => saida.push_str("&amp;"),
            '<' => saida.push_str("&lt;"),
            '>' => saida.push_str("&gt;"),
            '"' => saida.push_str("&quot;"),
            '\'' => saida.push_str("&apos;"),
            _ => saida.push(c),
        }
    }
    saida
}

fn exibir(xml: &str) -> windows::core::Result<()> {
    use windows::Data::Xml::Dom::XmlDocument;
    use windows::UI::Notifications::{ToastNotification, ToastNotificationManager};
    use windows::core::HSTRING;

    let documento = XmlDocument::new()?;
    documento.LoadXml(&HSTRING::from(xml))?;
    let toast = ToastNotification::CreateToastNotification(&documento)?;
    ToastNotificationManager::CreateToastNotifierWithId(&HSTRING::from(identidade()))?
        .Show(&toast)
}

/// What acting on a notification does, decided apart from doing it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Resposta {
    /// The "Gravar" button of a detection, while the suggestion still holds.
    Gravar,
    /// Open the window on this route.
    Abrir(String),
    Nada,
}

/// The "Gravar" button starts a recording only within the suggestion's
/// validity: two minutes later the meeting may be over, or another one, and
/// recording on a stale suggestion would be recording without a decision.
/// Past that, and for a click anywhere else, the window opens where the
/// notification points.
pub fn resposta_ao_acionamento(
    destino: &Destino,
    gravar: bool,
    acao: Option<&str>,
    decorrido: Duration,
) -> Resposta {
    if gravar && acao == Some("gravar") && decorrido < VALIDADE_DA_SUGESTAO {
        return Resposta::Gravar;
    }
    match destino {
        Destino::Nada => Resposta::Nada,
        Destino::Gravacao => Resposta::Abrir("gravacao".to_string()),
        Destino::Reuniao(uid) => Resposta::Abrir(format!("biblioteca/{uid}")),
    }
}

/// What an address opened from a notification asks for, done. A "Gravar"
/// whose token this process did not issue, or issued too long ago, only opens
/// the window.
pub fn atender(app: &AppHandle, pedido: Pedido) {
    let resposta = match pedido {
        Pedido::Abrir(rota) => Resposta::Abrir(rota),
        Pedido::Gravar(token) => {
            let decorrido = ativacao::resgatar_token(&token, Instant::now())
                .unwrap_or(Duration::MAX);
            resposta_ao_acionamento(&Destino::Gravacao, true, Some("gravar"), decorrido)
        }
    };
    let app = app.clone();
    std::thread::spawn(move || match resposta {
        Resposta::Gravar => {
            if !app.state::<Residente>().cliente.gravando() {
                tray::alternar_gravacao(&app);
            }
        }
        Resposta::Abrir(rota) => janela::abrir(&app, Some(&rota)),
        Resposta::Nada => {}
    });
}

#[cfg(test)]
mod testes {
    use super::*;
    use crate::service::{Health, ServiceState};

    fn snapshot(gravacao: Option<serde_json::Value>, deteccao: Option<serde_json::Value>) -> Snapshot {
        Snapshot {
            estado: ServiceState::Conectado,
            endereco: None,
            data_dir_do_servico: None,
            saude: Some(Health {
                gravacao_ativa: gravacao.is_some(),
                ..Health::default()
            }),
            gravacao,
            deteccao,
            detalhe: String::new(),
            acao: None,
            tentativas_recentes: 0,
            pode_tentar_de_novo: false,
            caminho_do_log: None,
            pid_do_servico: None,
        }
    }

    fn gravando(uid: &str, avisos: &[&str], silencio: f64) -> Option<serde_json::Value> {
        Some(serde_json::json!({
            "ativa": true, "pausada": false, "uid": uid, "titulo": "Planejamento",
            "duracao_ms": 60_000, "avisos": avisos,
            "niveis": {"mic": {"pico": 0.0, "silencio_ha_s": silencio}},
        }))
    }

    fn ocioso() -> Snapshot {
        snapshot(None, Some(serde_json::json!({"disponivel": true, "deteccao": null})))
    }

    fn chaves(avisos: &[Aviso]) -> Vec<&str> {
        avisos.iter().map(|a| a.chave.as_str()).collect()
    }

    #[test]
    fn a_primeira_observacao_so_registra() {
        let mut o = Observador::default();
        assert!(o.observar(&snapshot(gravando("g1", &["trilha 'mic': x"], 90.0), None)).is_empty());
        // The same recording, the same warning and the same silence: nothing.
        assert!(o.observar(&snapshot(gravando("g1", &["trilha 'mic': x"], 95.0), None)).is_empty());
    }

    #[test]
    fn inicio_e_fim_uma_vez_cada() {
        let mut o = Observador::default();
        o.observar(&ocioso());
        let inicio = o.observar(&snapshot(gravando("g1", &[], 0.0), None));
        assert_eq!(chaves(&inicio), ["iniciada:g1"]);
        assert!(o.observar(&snapshot(gravando("g1", &[], 0.0), None)).is_empty());
        let fim = o.observar(&ocioso());
        assert_eq!(chaves(&fim), ["encerrada:g1"]);
        assert!(o.observar(&ocioso()).is_empty());
    }

    #[test]
    fn sem_o_detalhe_da_gravacao_nada_e_dito() {
        let mut o = Observador::default();
        o.observar(&ocioso());
        // Health says recording, but /gravacao did not come this pass.
        let mut sem_detalhe = snapshot(None, None);
        sem_detalhe.saude = Some(Health { gravacao_ativa: true, ..Health::default() });
        assert!(o.observar(&sem_detalhe).is_empty());
        assert_eq!(chaves(&o.observar(&snapshot(gravando("g1", &[], 0.0), None))), ["iniciada:g1"]);
    }

    #[test]
    fn aviso_de_dispositivo_novo_notifica_uma_vez() {
        let mut o = Observador::default();
        o.observar(&ocioso());
        o.observar(&snapshot(gravando("g1", &[], 0.0), None));
        let perdido = "trilha 'mic': o dispositivo parou; recuperando por ate 30s";
        let avisos = o.observar(&snapshot(gravando("g1", &[perdido], 0.0), None));
        assert_eq!(chaves(&avisos), ["captura:g1:0"]);
        assert!(avisos[0].texto.starts_with("Microfone"));
        assert!(o.observar(&snapshot(gravando("g1", &[perdido], 0.0), None)).is_empty());
        // Not about a device: not a capture warning.
        assert!(o
            .observar(&snapshot(gravando("g1", &[perdido, "falha ao escrever metadados"], 0.0), None))
            .is_empty());
    }

    #[test]
    fn silencio_digital_uma_vez_por_episodio() {
        let mut o = Observador::default();
        o.observar(&ocioso());
        o.observar(&snapshot(gravando("g1", &[], 0.0), None));
        assert!(o.observar(&snapshot(gravando("g1", &[], 59.0), None)).is_empty());
        assert_eq!(chaves(&o.observar(&snapshot(gravando("g1", &[], 60.0), None))), ["silencio:g1:1"]);
        assert!(o.observar(&snapshot(gravando("g1", &[], 180.0), None)).is_empty());
        // The signal came back, then went away again for a minute.
        assert!(o.observar(&snapshot(gravando("g1", &[], 0.0), None)).is_empty());
        assert_eq!(chaves(&o.observar(&snapshot(gravando("g1", &[], 61.0), None))), ["silencio:g1:2"]);
    }

    fn gravando_com_sistema(uid: &str, silencio: f64, saida: &str) -> Option<serde_json::Value> {
        Some(serde_json::json!({
            "ativa": true, "pausada": false, "uid": uid, "titulo": "Diária",
            "duracao_ms": 300_000, "avisos": [],
            "niveis": {
                "mic": {"pico": 0.3, "silencio_ha_s": 0.0},
                "system": {"pico": 0.0, "silencio_ha_s": silencio},
            },
            "dispositivos": {"mic": "Microfone (JBL)", "system": saida},
        }))
    }

    #[test]
    fn silencio_do_sistema_nomeia_a_saida_uma_vez_por_episodio() {
        let saida = "Fones de ouvido (JBL Tune Flex 2)";
        let mut o = Observador::default();
        o.observar(&ocioso());
        o.observar(&snapshot(gravando_com_sistema("g1", 0.0, saida), None));
        assert!(o.observar(&snapshot(gravando_com_sistema("g1", 119.0, saida), None)).is_empty());
        let avisos = o.observar(&snapshot(gravando_com_sistema("g1", 120.0, saida), None));
        assert_eq!(chaves(&avisos), ["silencio-sistema:g1:1"]);
        assert!(avisos[0].texto.contains("Fones de ouvido (JBL Tune Flex 2)"), "{}", avisos[0].texto);
        assert!(avisos[0].texto.contains("Se os outros"));
        assert!(o.observar(&snapshot(gravando_com_sistema("g1", 600.0, saida), None)).is_empty());
        // The others spoke, then went silent for two minutes again.
        assert!(o.observar(&snapshot(gravando_com_sistema("g1", 0.0, saida), None)).is_empty());
        assert_eq!(
            chaves(&o.observar(&snapshot(gravando_com_sistema("g1", 121.0, saida), None))),
            ["silencio-sistema:g1:2"]
        );
    }

    #[test]
    fn um_minuto_calado_do_outro_lado_nao_e_aviso() {
        let mut o = Observador::default();
        o.observar(&ocioso());
        o.observar(&snapshot(gravando_com_sistema("g1", 0.0, "Alto-falantes"), None));
        assert!(o.observar(&snapshot(gravando_com_sistema("g1", 60.0, "Alto-falantes"), None)).is_empty());
    }

    #[test]
    fn deteccao_com_botao_uma_vez_por_episodio() {
        let mut o = Observador::default();
        o.observar(&ocioso());
        let chamada = serde_json::json!({"disponivel": true, "deteccao": {"aplicativo": "Teams"}});
        let aviso = o.observar(&snapshot(None, Some(chamada.clone())));
        assert_eq!(chaves(&aviso), ["deteccao:Teams:1"]);
        assert!(aviso[0].gravar);
        assert!(o.observar(&snapshot(None, Some(chamada.clone()))).is_empty());
        // Recording the call and stopping: the call goes on, no new suggestion.
        o.observar(&snapshot(gravando("g1", &[], 0.0), None));
        let depois = o.observar(&snapshot(None, Some(chamada)));
        assert_eq!(chaves(&depois), ["encerrada:g1"]);
    }

    #[test]
    fn eventos_a_primeira_resposta_so_registra() {
        let mut o = Observador::default();
        assert_eq!(o.desde(), None);
        let primeira = serde_json::json!({"eventos": [], "ultimo": 7, "execucao": "e1"});
        assert!(o.eventos(&primeira).is_empty());
        assert_eq!(o.desde(), Some(7));
    }

    #[test]
    fn eventos_novos_viram_avisos_com_a_reuniao() {
        let mut o = Observador::default();
        o.eventos(&serde_json::json!({"eventos": [], "ultimo": 7, "execucao": "e1"}));
        let resposta = serde_json::json!({"execucao": "e1", "ultimo": 9, "eventos": [
            {"seq": 8, "tipo": "pronta", "uid": "r1", "titulo": "Planejamento", "detalhe": ""},
            {"seq": 9, "tipo": "falhou", "uid": "r2", "titulo": "Revisão", "detalhe": "sem memória"},
        ]});
        let avisos = o.eventos(&resposta);
        assert_eq!(chaves(&avisos), ["pronta:r1:8", "falhou:r2:9"]);
        assert_eq!(avisos[0].destino, Destino::Reuniao("r1".into()));
        assert!(avisos[1].texto.contains("sem memória"));
        assert!(o.eventos(&resposta).is_empty(), "a mesma resposta não notifica de novo");
        assert_eq!(o.desde(), Some(9));
    }

    #[test]
    fn servico_reiniciado_recomeca_a_contagem() {
        let mut o = Observador::default();
        o.eventos(&serde_json::json!({"eventos": [], "ultimo": 50, "execucao": "e1"}));
        assert!(o.eventos(&serde_json::json!({"eventos": [], "ultimo": 2, "execucao": "e2"})).is_empty());
        assert_eq!(o.desde(), Some(0));
        let avisos = o.eventos(&serde_json::json!({"execucao": "e2", "ultimo": 2, "eventos": [
            {"seq": 2, "tipo": "pronta", "uid": "r9", "titulo": "", "detalhe": ""},
        ]}));
        assert_eq!(chaves(&avisos), ["pronta:r9:2"]);
    }

    #[test]
    fn clique_em_transcricao_abre_a_reuniao() {
        let destino = Destino::Reuniao("r1".into());
        assert_eq!(
            resposta_ao_acionamento(&destino, false, None, Duration::from_secs(3600)),
            Resposta::Abrir("biblioteca/r1".into())
        );
    }

    #[test]
    fn gravar_so_vale_dentro_do_prazo_da_sugestao() {
        let d = Destino::Gravacao;
        assert_eq!(
            resposta_ao_acionamento(&d, true, Some("gravar"), Duration::from_secs(30)),
            Resposta::Gravar
        );
        assert_eq!(
            resposta_ao_acionamento(&d, true, Some("gravar"), VALIDADE_DA_SUGESTAO),
            Resposta::Abrir("gravacao".into()),
            "depois de 2 minutos, so abre a janela"
        );
        // A click on the body of the detection, not on the button.
        assert_eq!(
            resposta_ao_acionamento(&d, true, None, Duration::from_secs(5)),
            Resposta::Abrir("gravacao".into())
        );
        assert_eq!(
            resposta_ao_acionamento(&Destino::Nada, false, None, Duration::ZERO),
            Resposta::Nada
        );
    }

    #[test]
    fn categoria_desligada_e_respeitada() {
        let chaves = Notificacoes {
            gravacao_iniciada: false,
            ..Notificacoes::default()
        };
        assert!(!Categoria::GravacaoIniciada.permitida(&chaves));
        assert!(Categoria::GravacaoEncerrada.permitida(&chaves));
        assert_eq!(Categoria::de_nome("reuniao_detectada"), Some(Categoria::ReuniaoDetectada));
    }

    fn concluida(titulo: &str) -> Aviso {
        Aviso::de(
            Categoria::TranscricaoConcluida,
            "concluida:r1".into(),
            "Transcrição concluída",
            format!("{titulo} — pronta para ler e buscar."),
            Destino::Reuniao("6cced371a4c94f37821dff011e80893a".into()),
        )
    }

    #[test]
    fn o_clique_abre_o_endereco_da_reuniao() {
        let aviso = concluida("Planejamento");
        let endereco = ativacao::endereco_para(&aviso.destino);
        let xml = xml_do_aviso(&aviso, endereco.as_deref(), None);
        assert!(xml.starts_with(
            "<toast activationType=\"protocol\" \
             launch=\"voxvault://abrir/biblioteca/6cced371a4c94f37821dff011e80893a\""
        ));
        assert!(!xml.contains("<actions>"));
    }

    #[test]
    fn o_que_nao_leva_a_lugar_algum_nao_tem_endereco() {
        let aviso = Aviso::bandeja();
        let xml = xml_do_aviso(&aviso, ativacao::endereco_para(&aviso.destino).as_deref(), None);
        assert!(!xml.contains("activationType"), "{xml}");
        assert!(!xml.contains("launch="), "{xml}");
    }

    #[test]
    fn gravar_e_um_botao_com_o_seu_proprio_endereco() {
        let aviso = Aviso::exemplo(Categoria::ReuniaoDetectada);
        let botao = ativacao::endereco_para_gravar("0123456789abcdef0123456789abcdef");
        let xml = xml_do_aviso(&aviso, ativacao::endereco_para(&aviso.destino).as_deref(), Some(&botao));
        assert!(xml.contains(
            "<action content=\"Gravar\" activationType=\"protocol\" \
             arguments=\"voxvault://gravar/0123456789abcdef0123456789abcdef\"/>"
        ));
        assert!(xml.contains("duration=\"long\""));
    }

    #[test]
    fn um_titulo_qualquer_ainda_e_um_xml_que_o_windows_le() {
        use windows::Data::Xml::Dom::XmlDocument;
        use windows::core::HSTRING;

        let aviso = concluida("Q&A <interno> \"sócios\" d'água");
        let xml = xml_do_aviso(&aviso, ativacao::endereco_para(&aviso.destino).as_deref(), None);
        let documento = XmlDocument::new().unwrap();
        documento.LoadXml(&HSTRING::from(&xml)).expect(&xml);
        let textos = documento.GetElementsByTagName(&HSTRING::from("text")).unwrap();
        assert_eq!(
            textos.Item(1).unwrap().InnerText().unwrap().to_string(),
            "Q&A <interno> \"sócios\" d'água — pronta para ler e buscar."
        );
    }
}
