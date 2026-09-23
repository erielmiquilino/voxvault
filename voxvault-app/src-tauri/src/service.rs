//! Client of the resident service. Never its owner.
//!
//! The core already runs as a resident process because the command line needs
//! somebody to hold the capture between start and stop. This app connects to
//! that same process. Three consequences are load-bearing and are enforced here
//! rather than left to convention:
//!
//! 1. **Never a second instance.** If the rendezvous point names a live
//!    service, the app attaches to it. A recording started from the command
//!    line therefore shows up as active when the window opens, which is
//!    correct: it is the same recording, of the same owner.
//! 2. **Never kills it.** Not on close, not on failure, not ever -- there is no
//!    call to terminate the service anywhere in this file. A meeting closed in
//!    a hurry has to finish being transcribed with the window shut, and the
//!    service ends itself by its own idle policy. An app that killed the
//!    service would silently throw away a queue.
//! 3. **A bounded retry budget.** Three consecutive failures to bring it up
//!    within 60 seconds stop the attempts and surface the recorded cause.
//!    Retrying forever turns a broken environment into a machine that is busy
//!    and never says why.

use std::collections::VecDeque;
use std::fs::OpenOptions;
use std::net::{SocketAddr, ToSocketAddrs};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use crate::http;
use crate::paths;
use crate::system;

/// Consecutive start failures tolerated inside [`FAILURE_WINDOW`].
const FAILURE_BUDGET: usize = 3;
const FAILURE_WINDOW: Duration = Duration::from_secs(60);
/// A single request to a service that is up answers in microseconds on
/// loopback; anything past this is a hang, not slowness.
const REQUEST_TIMEOUT: Duration = Duration::from_millis(1500);

/// How long one request may take, by what it asks for.
///
/// Queries answer from memory and keep the short budget. Starting a recording
/// opens two audio devices, which is normally a few hundred milliseconds but
/// waits behind the warm-up a freshly started service is still doing. Ending
/// one compresses and verifies the audio before answering -- seconds for an
/// hour of meeting -- and giving up on it early would report a failure for a
/// recording that was in fact ended and queued. The command line allows the
/// same three minutes for it.
pub fn prazo_para(path: &str) -> Duration {
    match path.split('?').next().unwrap_or(path) {
        "/gravacao/encerrar" => Duration::from_secs(180),
        "/gravacao/iniciar" => Duration::from_secs(15),
        "/gravacao/pausar" | "/gravacao/retomar" => Duration::from_secs(10),
        _ => REQUEST_TIMEOUT,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ServiceState {
    /// Not looked for yet.
    Desconhecido,
    /// Looking for the rendezvous point, or waiting for a start to publish it.
    Procurando,
    /// Attached and answering.
    Conectado,
    /// Not reachable right now; the app will try again.
    Indisponivel,
    /// Retry budget spent. The app stopped trying and is showing the cause.
    Falho,
}

/// What the resident service publishes at the user-scope rendezvous point when
/// it starts: the loopback address it chose and the secret for that start.
///
/// Keys are pt-BR to match every other file the core writes for a person to be
/// able to open.
#[derive(Debug, Clone, Deserialize)]
pub struct Rendezvous {
    #[serde(alias = "endereco", alias = "endpoint")]
    pub endereco: String,
    #[serde(alias = "segredo", alias = "secret")]
    pub segredo: String,
    #[serde(alias = "pid", default)]
    pub pid: u32,
    #[serde(alias = "data_dir", alias = "diretorio_de_dados", default)]
    pub data_dir: String,
}

/// Liveness and busy-state, as the service reports it.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct Health {
    #[serde(default, alias = "gravacao_ativa")]
    pub gravacao_ativa: bool,
    #[serde(default, alias = "fila_pendente")]
    pub fila_pendente: u32,
    #[serde(default, alias = "transcrevendo")]
    pub transcrevendo: bool,
    #[serde(default, alias = "data_dir", alias = "diretorio_de_dados")]
    pub data_dir: String,
    #[serde(default, alias = "versao")]
    pub versao: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Snapshot {
    pub estado: ServiceState,
    pub endereco: Option<String>,
    /// Data directory the service is actually pointed at, which is not
    /// necessarily the one this app resolved -- a divergence the settings
    /// screen has to be able to show rather than average away.
    pub data_dir_do_servico: Option<String>,
    pub saude: Option<Health>,
    /// The service's own view of the recording in progress, fetched in the same
    /// pass as the health probe while one is running.
    ///
    /// It is passed through untouched rather than modelled here: the shape
    /// belongs to the service, and a struct in this file would be a second
    /// definition of it, free to fall behind.
    pub gravacao: Option<serde_json::Value>,
    /// Whether a meeting looks like it has started, and on what evidence.
    /// Only asked for when nothing is being recorded -- during a recording the
    /// answer is already known and the question would be pure cost.
    pub deteccao: Option<serde_json::Value>,
    /// Natural-language cause, in pt-BR, for whatever state this is.
    pub detalhe: String,
    /// Corrective action, when there is one.
    pub acao: Option<String>,
    pub tentativas_recentes: usize,
    pub pode_tentar_de_novo: bool,
    pub caminho_do_log: Option<String>,
    /// Process id the service published. Needed to measure the whole tree: a
    /// measurement that leaves the service out would report a flattering and
    /// false number.
    pub pid_do_servico: Option<u32>,
}

struct Inner {
    estado: ServiceState,
    rendezvous: Option<Rendezvous>,
    endereco: Option<SocketAddr>,
    saude: Option<Health>,
    gravacao: Option<serde_json::Value>,
    deteccao: Option<serde_json::Value>,
    detalhe: String,
    acao: Option<String>,
    falhas: VecDeque<Instant>,
    /// Set once the budget is spent; cleared only by an explicit retry.
    desistiu: bool,
    /// True once a connection has been seen, so a later loss is a crash and
    /// not a service that was simply never up.
    ja_conectou: bool,
}

impl Default for Inner {
    fn default() -> Self {
        Self {
            estado: ServiceState::Desconhecido,
            rendezvous: None,
            endereco: None,
            saude: None,
            gravacao: None,
            deteccao: None,
            detalhe: "Ainda não verificado.".to_string(),
            acao: None,
            falhas: VecDeque::new(),
            desistiu: false,
            ja_conectou: false,
        }
    }
}

#[derive(Clone, Default)]
pub struct ServiceClient {
    inner: Arc<Mutex<Inner>>,
}

/// Outcome of one supervision pass, so the caller can decide what to tell the
/// user without holding the lock.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Transition {
    Unchanged,
    Connected,
    /// Was connected, now is not: the service died under us.
    Crashed,
    GaveUp,
}

impl ServiceClient {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn snapshot(&self) -> Snapshot {
        let inner = self.inner.lock().unwrap();
        Snapshot {
            estado: inner.estado,
            endereco: inner.endereco.map(|addr| addr.to_string()),
            data_dir_do_servico: inner
                .saude
                .as_ref()
                .map(|h| h.data_dir.clone())
                .filter(|dir| !dir.is_empty())
                .or_else(|| {
                    inner
                        .rendezvous
                        .as_ref()
                        .map(|r| r.data_dir.clone())
                        .filter(|dir| !dir.is_empty())
                }),
            saude: inner.saude.clone(),
            gravacao: inner.gravacao.clone(),
            deteccao: inner.deteccao.clone(),
            detalhe: inner.detalhe.clone(),
            acao: inner.acao.clone(),
            tentativas_recentes: inner.falhas.len(),
            pode_tentar_de_novo: inner.desistiu,
            caminho_do_log: log_path().to_str().map(str::to_string),
            pid_do_servico: inner
                .rendezvous
                .as_ref()
                .map(|r| r.pid)
                .filter(|pid| *pid > 0),
        }
    }

    /// Whether the service is holding work that must not be interrupted.
    ///
    /// Answered conservatively: when the state is unknown the answer is "busy",
    /// because the cost of being wrong in one direction is a lost meeting and in
    /// the other direction is a process that lingers for its idle timeout.
    pub fn ocupado(&self) -> bool {
        let inner = self.inner.lock().unwrap();
        match (&inner.estado, &inner.saude) {
            (ServiceState::Conectado, Some(saude)) => {
                saude.gravacao_ativa || saude.fila_pendente > 0 || saude.transcrevendo
            }
            (ServiceState::Conectado, None) => true,
            _ => false,
        }
    }

    pub fn gravando(&self) -> bool {
        let inner = self.inner.lock().unwrap();
        inner
            .saude
            .as_ref()
            .map(|saude| saude.gravacao_ativa)
            .unwrap_or(false)
    }

    /// Clear the spent budget so supervision resumes. Only a person asks for
    /// this; nothing retries on its own after the budget is gone.
    pub fn rearmar(&self) {
        let mut inner = self.inner.lock().unwrap();
        inner.falhas.clear();
        inner.desistiu = false;
        inner.estado = ServiceState::Procurando;
        inner.detalhe = "Tentando alcançar o serviço local...".to_string();
        inner.acao = None;
    }

    /// One supervision pass: attach to a published service, or try to start one.
    pub fn passo(&self) -> Transition {
        if self.inner.lock().unwrap().desistiu {
            return Transition::Unchanged;
        }

        match read_rendezvous() {
            Some(rendezvous) => match resolve(&rendezvous.endereco) {
                Some(addr) => match probe(addr, &rendezvous.segredo) {
                    Ok(saude) => {
                        // Fetched in the same pass, and only while something is
                        // being recorded. Asking for it every ten seconds with
                        // the machine idle would be a request per minute that
                        // nobody reads.
                        let buscar = |caminho: &str| {
                            http::request(
                                addr,
                                "GET",
                                caminho,
                                &rendezvous.segredo,
                                None,
                                REQUEST_TIMEOUT,
                            )
                            .ok()
                            .and_then(|resposta| {
                                serde_json::from_str::<serde_json::Value>(&resposta.body).ok()
                            })
                        };
                        let gravacao = saude
                            .gravacao_ativa
                            .then(|| buscar("/gravacao"))
                            .flatten();
                        // Asked for only while idle. During a recording the
                        // answer is already known, and asking would be a
                        // request per tick that nobody could act on.
                        let deteccao = (!saude.gravacao_ativa)
                            .then(|| buscar("/deteccao"))
                            .flatten();
                        self.marcar_conectado(rendezvous, addr, saude, gravacao, deteccao)
                    }
                    Err(err) => self.marcar_perdido(err.mensagem(), rendezvous.pid),
                },
                None => self.marcar_perdido(
                    format!(
                        "O endereço publicado pelo serviço não pôde ser \
                         interpretado: {}.",
                        rendezvous.endereco
                    ),
                    rendezvous.pid,
                ),
            },
            None => self.tentar_iniciar(),
        }
    }

    fn marcar_conectado(
        &self,
        rendezvous: Rendezvous,
        addr: SocketAddr,
        saude: Health,
        gravacao: Option<serde_json::Value>,
        deteccao: Option<serde_json::Value>,
    ) -> Transition {
        let mut inner = self.inner.lock().unwrap();
        let era_conectado = inner.estado == ServiceState::Conectado;
        inner.estado = ServiceState::Conectado;
        inner.rendezvous = Some(rendezvous);
        inner.endereco = Some(addr);
        inner.saude = Some(saude);
        inner.gravacao = gravacao;
        inner.deteccao = deteccao;
        inner.detalhe = "Serviço local em execução.".to_string();
        inner.acao = None;
        inner.falhas.clear();
        inner.ja_conectou = true;
        if era_conectado {
            Transition::Unchanged
        } else {
            Transition::Connected
        }
    }

    /// The rendezvous point names a service that did not answer this pass.
    ///
    /// Whether that means the service is gone is decided by the process, not by
    /// the request: a single timed-out probe against a service that is merely
    /// busy says nothing. The rendezvous file belongs to the service, and this
    /// client removes it only once the process behind it is provably gone --
    /// deleting it under a live service would leave it running and
    /// undiscoverable, and the next start attempt would be refused by its own
    /// exclusivity claim with nothing left to point at it.
    fn marcar_perdido(&self, causa: String, pid: u32) -> Transition {
        let vivo = system::process_is_alive(pid);
        let caiu = {
            let mut inner = self.inner.lock().unwrap();
            let caiu = inner.estado == ServiceState::Conectado;
            inner.estado = ServiceState::Indisponivel;
            inner.saude = None;
            inner.gravacao = None;
            inner.deteccao = None;
            inner.detalhe = if vivo {
                format!(
                    "{causa}\n\nO processo do serviço (pid {pid}) continua em \
                     execução, então ele está ocupado ou lento, não caído."
                )
            } else {
                causa
            };
            inner.acao = Some(
                "O aplicativo vai tentar novamente; nenhuma gravação é perdida \
                 por isso."
                    .to_string(),
            );
            caiu
        };
        if !vivo {
            // The process is gone. Its rendezvous now points at nothing, and a
            // file pointing at a dead process is worse than no file.
            let _ = std::fs::remove_file(paths::rendezvous_file());
        }
        if caiu {
            Transition::Crashed
        } else {
            Transition::Unchanged
        }
    }

    fn tentar_iniciar(&self) -> Transition {
        if crate::preparo::em_andamento() {
            // The environment is being rebuilt: a service started from it now
            // would lock the very files the preparation has to replace.
            let mut inner = self.inner.lock().unwrap();
            inner.detalhe = "O ambiente está sendo preparado; o serviço inicia ao final.".to_string();
            return Transition::Unchanged;
        }
        if paths::core_executable().is_none() {
            // Not prepared yet: there is nothing to start, and that is the
            // preparation screen's news to give, not a failure of the service.
            // Counted as one, it spent the retry budget and put an alarm over
            // the very screen that was already saying what to do.
            let mut inner = self.inner.lock().unwrap();
            inner.detalhe = "O ambiente de execução ainda não foi preparado.".to_string();
            return Transition::Unchanged;
        }
        {
            let mut inner = self.inner.lock().unwrap();
            if inner.estado != ServiceState::Procurando {
                inner.estado = ServiceState::Procurando;
                inner.detalhe = "Iniciando o serviço local...".to_string();
            }
        }

        match spawn_service() {
            Ok(()) => {
                // The service publishes the rendezvous point once it is
                // listening; the next pass will find it. Nothing is asserted
                // here about it having worked.
                Transition::Unchanged
            }
            Err(causa) => self.registrar_falha(causa),
        }
    }

    fn registrar_falha(&self, causa: String) -> Transition {
        let mut inner = self.inner.lock().unwrap();
        let agora = Instant::now();
        while let Some(primeira) = inner.falhas.front() {
            if agora.duration_since(*primeira) > FAILURE_WINDOW {
                inner.falhas.pop_front();
            } else {
                break;
            }
        }
        inner.falhas.push_back(agora);
        inner.saude = None;
        inner.gravacao = None;
        inner.deteccao = None;

        if inner.falhas.len() >= FAILURE_BUDGET {
            inner.desistiu = true;
            inner.estado = ServiceState::Falho;
            inner.detalhe = format!(
                "O serviço local falhou ao iniciar {FAILURE_BUDGET} vezes \
                 seguidas em menos de {} segundos. As tentativas foram \
                 interrompidas.\n\nCausa registrada: {causa}",
                FAILURE_WINDOW.as_secs()
            );
            inner.acao = Some(
                "Corrija a causa acima e use \"Tentar novamente\". \
                 A gravação e a transcrição permanecem indisponíveis até lá."
                    .to_string(),
            );
            Transition::GaveUp
        } else {
            inner.estado = ServiceState::Indisponivel;
            inner.detalhe = causa;
            inner.acao = Some(format!(
                "Tentativa {} de {FAILURE_BUDGET}.",
                inner.falhas.len()
            ));
            Transition::Unchanged
        }
    }
}

// -- rendezvous and transport ---------------------------------------------

fn read_rendezvous() -> Option<Rendezvous> {
    let text = std::fs::read_to_string(paths::rendezvous_file()).ok()?;
    let rendezvous: Rendezvous = serde_json::from_str(&text).ok()?;
    (!rendezvous.endereco.is_empty() && !rendezvous.segredo.is_empty()).then_some(rendezvous)
}

fn resolve(endereco: &str) -> Option<SocketAddr> {
    let trimmed = endereco
        .trim()
        .trim_start_matches("http://")
        .trim_start_matches("https://")
        .trim_end_matches('/');
    let addr = trimmed.to_socket_addrs().ok()?.next()?;
    // A service reachable from outside the machine is a different product with
    // a different threat model. The app refuses to speak to one.
    addr.ip().is_loopback().then_some(addr)
}

fn probe(addr: SocketAddr, segredo: &str) -> Result<Health, http::HttpError> {
    let response = http::request(addr, "GET", "/saude", segredo, None, REQUEST_TIMEOUT)?;
    serde_json::from_str(&response.body).map_err(|err| {
        http::HttpError::Protocol(format!("saúde ilegível ({err}): {}", response.body))
    })
}

/// A failed request, with whether it failed by running out of time -- in
/// which case the service may still be doing what it was asked.
#[derive(Debug, Clone)]
pub struct ErroDaChamada {
    pub mensagem: String,
    pub prazo_esgotado: bool,
}

/// Issue a request to the attached service. Used by the recording commands.
pub fn call(
    client: &ServiceClient,
    method: &str,
    path: &str,
    body: Option<&str>,
) -> Result<serde_json::Value, String> {
    call_detalhado(client, method, path, body).map_err(|erro| erro.mensagem)
}

pub fn call_detalhado(
    client: &ServiceClient,
    method: &str,
    path: &str,
    body: Option<&str>,
) -> Result<serde_json::Value, ErroDaChamada> {
    let falha = |mensagem: String| ErroDaChamada {
        mensagem,
        prazo_esgotado: false,
    };
    let (addr, segredo) = {
        let inner = client.inner.lock().unwrap();
        match (inner.endereco, inner.rendezvous.as_ref()) {
            (Some(addr), Some(rendezvous)) if inner.estado == ServiceState::Conectado => {
                (addr, rendezvous.segredo.clone())
            }
            _ => {
                return Err(falha(format!(
                    "Esta ação exige o serviço residente do núcleo, que não está \
                     disponível.\n\n{}",
                    inner.detalhe
                )))
            }
        }
    };
    let response = http::request(addr, method, path, &segredo, body, prazo_para(path))
        .map_err(|err| ErroDaChamada {
            prazo_esgotado: matches!(err, http::HttpError::Prazo(_)),
            mensagem: err.mensagem(),
        })?;
    // 204 and an empty body both mean "done, nothing to report" -- a recording
    // that stopped cleanly has no payload to hand back.
    if response.status == 204 || response.body.trim().is_empty() {
        return Ok(serde_json::Value::Null);
    }
    serde_json::from_str(&response.body)
        .map_err(|err| falha(format!("Resposta ilegível do serviço local: {err}")))
}

pub fn log_path() -> PathBuf {
    paths::user_config_dir().join("servico.log")
}

/// Start the resident service, independent of this window.
///
/// Independent on purpose: a service tied to this window would be torn down
/// with it, which is precisely the failure the lifecycle requirement forbids --
/// the queue has to finish with the window closed.
///
/// Output goes to a log file rather than a pipe. A pipe nobody drains blocks
/// the service once it fills, and the log is also what the app reads back to
/// name the cause when a start fails.
fn spawn_service() -> Result<(), String> {
    let exe = paths::core_executable().ok_or_else(|| {
        "O ambiente de execução do núcleo não está preparado, então não há \
         serviço para iniciar."
            .to_string()
    })?;

    let log = log_path();
    if let Some(parent) = log.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let sink = || {
        OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log)
            .map(Stdio::from)
            .unwrap_or_else(|_| Stdio::null())
    };

    let mut command = Command::new(&exe);
    command
        .arg("serve")
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        .env("NO_PROXY", paths::sem_proxy_no_loopback())
        .env("no_proxy", paths::sem_proxy_no_loopback())
        .stdin(Stdio::null())
        .stdout(sink())
        .stderr(sink());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // A console of its own, hidden -- not none. With DETACHED_PROCESS the
        // core's launcher has no console to hand down, so the interpreter it
        // starts gets a new, visible one: an empty terminal window beside the
        // app, and closing it kills the service, recording included.
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        command.creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP);
    }

    let mut child = command
        .spawn()
        .map_err(|err| format!("Não foi possível iniciar o serviço local: {err}"))?;

    // A service that exits immediately has not started; it has failed. Give it
    // a short moment and check, so the failure is reported now instead of being
    // discovered as an address that never appears.
    std::thread::sleep(Duration::from_millis(600));
    match child.try_wait() {
        Ok(Some(status)) => Err(format!(
            "O comando `voxvault serve` terminou imediatamente com código {}.\n\n{}",
            status.code().unwrap_or(-1),
            tail_of_log()
        )),
        Ok(None) => Ok(()),
        Err(err) => Err(format!("Estado do serviço local indeterminado: {err}")),
    }
}

/// Last lines of the service log, which is where the real cause of a failed
/// start is written.
fn tail_of_log() -> String {
    let Ok(text) = std::fs::read_to_string(log_path()) else {
        return "Nenhuma saída foi registrada.".to_string();
    };
    let tail: Vec<&str> = text
        .lines()
        .filter(|line| !line.trim().is_empty())
        .rev()
        .take(8)
        .collect();
    if tail.is_empty() {
        return "Nenhuma saída foi registrada.".to_string();
    }
    tail.into_iter().rev().collect::<Vec<_>>().join("\n")
}

#[cfg(test)]
mod testes {
    use super::*;

    #[test]
    fn encerrar_espera_a_compressao_e_consultas_nao() {
        assert!(prazo_para("/gravacao/encerrar") >= Duration::from_secs(120));
        assert!(prazo_para("/gravacao/iniciar") > REQUEST_TIMEOUT);
        assert_eq!(prazo_para("/eventos?desde=3"), REQUEST_TIMEOUT);
        assert_eq!(prazo_para("/saude"), REQUEST_TIMEOUT);
    }
}
