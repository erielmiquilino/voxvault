//! The command surface the interface calls.
//!
//! Two conventions run through all of it.
//!
//! **Failures are data, not prose.** Every command returns [`Falha`], which
//! carries a natural-language cause and, when the obstacle is a missing
//! machine-readable output in the core, the exact command and flag that would
//! remove it. The interface can then disable the action and say why, instead of
//! letting the user find out at the click -- which the design calls out by name
//! as the pattern that makes a tool irritating.
//!
//! **Nothing here touches audio.** Not a device, not a decoder, not a sample.
//! The app is a client of the resident service, and every recording command in
//! this file is a request forwarded to it.

use std::path::{Path, PathBuf};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State};

use crate::avisos;
use crate::cli::{self, CoreError};
use crate::janela;
use crate::library;
use crate::paths::{self, DataDirSource};
use crate::prefs;
use crate::preparo;
use crate::residente::Residente;
use crate::service::{self, ServiceClient};
use crate::system;

#[derive(Debug, Clone, Serialize)]
pub struct Falha {
    pub mensagem: String,
    pub acao: Option<String>,
}

impl Falha {
    pub fn nova(mensagem: impl Into<String>) -> Self {
        Self {
            mensagem: mensagem.into(),
            acao: None,
        }
    }

    pub fn com_acao(mensagem: impl Into<String>, acao: impl Into<String>) -> Self {
        Self {
            mensagem: mensagem.into(),
            acao: Some(acao.into()),
        }
    }
}

impl From<CoreError> for Falha {
    fn from(err: CoreError) -> Self {
        Falha {
            acao: match &err {
                CoreError::NaoPreparado { .. } => Some(
                    "Prepare o ambiente de execução na tela inicial do aplicativo."
                        .to_string(),
                ),
                _ => None,
            },
            mensagem: err.mensagem(),
        }
    }
}

type Resposta<T> = Result<T, Falha>;

// -- environment -----------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct Ambiente {
    pub preparado: bool,
    pub situacao: preparo::Situacao,
    pub raiz_do_nucleo: Option<String>,
    pub executavel: Option<String>,
    pub detalhe: String,
    pub acao: Option<String>,
}

#[tauri::command]
pub fn ambiente_estado() -> Ambiente {
    let raiz = paths::core_root().map(display);
    let executavel = paths::core_executable().map(display);
    let situacao = preparo::situacao_atual();
    let (preparado, detalhe, acao) = match &situacao {
        preparo::Situacao::Pronto | preparo::Situacao::Desenvolvimento => (
            executavel.is_some(),
            "Ambiente de execução do núcleo pronto.".to_string(),
            None,
        ),
        preparo::Situacao::Primeiro { retomada } => (
            false,
            if *retomada {
                "O preparo anterior não terminou. Retomá-lo aproveita tudo o que já foi baixado."
                    .to_string()
            } else {
                "Antes do primeiro uso, o VoxVault prepara o seu ambiente de execução.".to_string()
            },
            Some(if *retomada { "Retomar o preparo." } else { "Preparar o ambiente." }.to_string()),
        ),
        preparo::Situacao::Desatualizado { motivo } => (
            false,
            format!("{motivo} O ambiente vai ser atualizado, sem baixar o que já está em dia."),
            None,
        ),
        preparo::Situacao::SemRecursos => (
            false,
            "A instalação está incompleta: faltam os recursos do núcleo.".to_string(),
            Some("Reinstale o VoxVault a partir do instalador da release.".to_string()),
        ),
    };
    Ambiente {
        preparado,
        situacao,
        raiz_do_nucleo: raiz,
        executavel,
        detalhe,
        acao,
    }
}

impl From<preparo::FalhaDoPreparo> for Falha {
    fn from(falha: preparo::FalhaDoPreparo) -> Self {
        Falha::com_acao(falha.mensagem, falha.acao)
    }
}

/// Hardware, data directory, what would be downloaded and whether it fits --
/// everything the preparation screen shows before anything is fetched.
#[tauri::command]
pub async fn preparo_plano(pasta: Option<String>) -> Resposta<preparo::Plano> {
    tauri::async_runtime::spawn_blocking(move || preparo::planejar(pasta))
        .await
        .map_err(|err| Falha::nova(format!("O plano do preparo foi interrompido: {err}")))?
        .map_err(Falha::from)
}

/// Run the preparation. Progress goes out as `preparo://etapa` and
/// `preparo://linha`; the answer comes when the verification has passed.
#[tauri::command]
pub async fn preparo_iniciar(app: AppHandle, pasta: Option<String>) -> Resposta<String> {
    let resultado = tauri::async_runtime::spawn_blocking(move || preparo::preparar(&app, pasta))
        .await
        .map_err(|err| Falha::nova(format!("O preparo foi interrompido: {err}")))?;
    resultado.map_err(Falha::from)?;
    Ok("Ambiente de execução preparado.".to_string())
}

/// Download a model before a settings change adopts it, with the same
/// progress as the preparation's own step (`preparo://etapa`, `modelo`).
#[tauri::command]
pub async fn modelo_baixar(app: AppHandle, modelo: String) -> Resposta<String> {
    let resultado = tauri::async_runtime::spawn_blocking(move || {
        preparo::baixar_modelo(&app, "modelo", &paths::runtime_dir(), &modelo)
    })
    .await
    .map_err(|err| Falha::nova(format!("O download foi interrompido: {err}")))?;
    resultado.map_err(Falha::from)?;
    Ok("Modelo baixado.".to_string())
}

// -- resident service ------------------------------------------------------

#[tauri::command]
pub fn servico_estado(cliente: State<'_, ServiceClient>) -> service::Snapshot {
    cliente.snapshot()
}

#[tauri::command]
pub fn servico_rearmar(cliente: State<'_, ServiceClient>) -> service::Snapshot {
    cliente.rearmar();
    cliente.passo();
    cliente.snapshot()
}

// -- recording -------------------------------------------------------------
//
// All five go to the resident service. There is no fallback that drives the
// blocking `voxvault record` command from here: that command holds the capture
// for the lifetime of a process it owns, so driving it from the app would make
// the app the owner of the devices -- the one thing the lifecycle requirement
// forbids.

#[tauri::command]
pub fn gravacao_estado(cliente: State<'_, ServiceClient>) -> Resposta<serde_json::Value> {
    service::call(&cliente, "GET", "/gravacao", None).map_err(Falha::nova)
}

/// Recording requests, off the main thread: ending a recording waits for the
/// audio to be compressed and verified, and the window must stay responsive
/// meanwhile. A pass right after each one moves the tray icon at once.
async fn pedir_a_gravacao(
    app: AppHandle,
    caminho: &'static str,
    corpo: Option<String>,
) -> Resposta<serde_json::Value> {
    tauri::async_runtime::spawn_blocking(move || {
        let resposta = {
            let cliente = app.state::<ServiceClient>();
            service::call(&cliente, "POST", caminho, corpo.as_deref())
        };
        crate::residente::ciclo(&app);
        resposta
    })
    .await
    .map_err(|err| Falha::nova(format!("O pedido ao serviço foi interrompido: {err}")))?
    .map_err(Falha::nova)
}

#[tauri::command]
pub async fn gravacao_iniciar(app: AppHandle, titulo: String) -> Resposta<serde_json::Value> {
    let corpo = serde_json::json!({ "titulo": titulo }).to_string();
    pedir_a_gravacao(app, "/gravacao/iniciar", Some(corpo)).await
}

#[tauri::command]
pub async fn gravacao_pausar(app: AppHandle) -> Resposta<serde_json::Value> {
    pedir_a_gravacao(app, "/gravacao/pausar", None).await
}

#[tauri::command]
pub async fn gravacao_retomar(app: AppHandle) -> Resposta<serde_json::Value> {
    pedir_a_gravacao(app, "/gravacao/retomar", None).await
}

#[tauri::command]
pub async fn gravacao_encerrar(app: AppHandle) -> Resposta<serde_json::Value> {
    pedir_a_gravacao(app, "/gravacao/encerrar", None).await
}

// -- library ---------------------------------------------------------------

#[tauri::command]
pub fn reunioes_listar(limite: Option<u32>) -> Resposta<Vec<library::Resumo>> {
    let payload = cli::list(limite.unwrap_or(200))?;
    Ok(library::listar_do_nucleo(&payload))
}

/// The per-meeting content, read from the directory the core reported.
///
/// The directory comes from the caller because the list already carries the
/// core's answer for it. Rebuilding the path from the data directory here
/// would be a second convention for where a meeting lives.
///
/// Before reading, the export is checked against the revision the core says is
/// active. They diverge whenever a meeting was re-transcribed and the exports
/// were not regenerated, and reading a stale export would show text from a
/// revision that is no longer the one the core would serve -- silently, and
/// most visibly right after the reprocessing somebody asked for. Regenerating
/// is what `voxvault export` exists to do, so it is asked to.
#[tauri::command]
pub fn reuniao_detalhar(
    uid: String,
    diretorio: String,
    revisao_ativa: Option<String>,
) -> Resposta<library::Detalhe> {
    if let Some(ativa) = revisao_ativa.filter(|r| !r.is_empty()) {
        if library::revisao_exportada(&diretorio).as_deref() != Some(ativa.as_str()) {
            // A failure here is not fatal: the stale export is still readable,
            // and the interface says which revision it is showing.
            let _ = cli::export(&uid);
        }
    }
    library::detalhar(&diretorio).ok_or_else(|| {
        Falha::com_acao(
            format!("O diretório da reunião não existe mais: {diretorio}"),
            "Atualize a lista; a reunião pode ter sido removida fora do aplicativo.",
        )
    })
}

/// The core's own answer for one meeting, via `show --json`.
///
/// Preferred over the exported file when it works: it comes from the active
/// revision in the store, so it is right even when the export on disk is stale.
#[tauri::command]
pub fn reuniao_do_nucleo(uid: String) -> Resposta<serde_json::Value> {
    cli::show(&uid).map_err(Falha::from)
}

#[tauri::command]
pub fn reuniao_reprocessar(uid: String) -> Resposta<String> {
    cli::reprocess(&uid).map_err(Falha::from)
}

#[tauri::command]
pub fn reuniao_exportar(uid: String) -> Resposta<Vec<String>> {
    cli::export(&uid).map_err(Falha::from)
}

#[tauri::command]
pub fn reuniao_renomear(uid: String, titulo: String) -> Resposta<String> {
    cli::rename(&uid, &titulo).map_err(Falha::from)
}

/// Describe what removing the audio would cost, without removing anything.
///
/// The core's dry run is what the confirmation shows, so the sentence the user
/// reads is the core's own account of what it is about to do -- not a
/// paraphrase written here that could drift from it.
#[tauri::command]
pub fn reuniao_remover_audio_previa(uid: String) -> Resposta<String> {
    cli::remove_audio(&uid, false).map_err(Falha::from)
}

#[tauri::command]
pub fn reuniao_remover_audio(uid: String) -> Resposta<String> {
    cli::remove_audio(&uid, true).map_err(Falha::from)
}

/// What deleting these meetings would remove, without removing anything.
///
/// The sums in the answer are the core's, and they are what the confirmation
/// shows: the space the person agrees to free is the space the core frees.
#[tauri::command]
pub async fn reunioes_excluir_previa(uids: Vec<String>) -> Resposta<cli::Exclusao> {
    excluir(uids, false).await
}

/// Delete meetings for good. Each one is deleted or refused on its own, and
/// the answer says which, with the reason for every refusal.
#[tauri::command]
pub async fn reunioes_excluir(uids: Vec<String>) -> Resposta<cli::Exclusao> {
    excluir(uids, true).await
}

/// Off the main thread: a batch takes one interpreter start plus a rename and
/// a transaction per meeting, and the window must not freeze meanwhile.
async fn excluir(uids: Vec<String>, confirmar: bool) -> Resposta<cli::Exclusao> {
    if uids.is_empty() {
        return Err(Falha::nova("Nenhuma reunião foi indicada para exclusão."));
    }
    tauri::async_runtime::spawn_blocking(move || cli::delete(&uids, confirmar))
        .await
        .map_err(|err| Falha::nova(format!("A exclusão foi interrompida: {err}")))?
        .map_err(Falha::from)
}

#[tauri::command]
pub fn reuniao_abrir_pasta(diretorio: String) -> Resposta<()> {
    system::reveal(Path::new(&diretorio)).map_err(Falha::nova)
}

#[tauri::command]
pub fn abrir_caminho(caminho: String) -> Resposta<()> {
    system::reveal(Path::new(&caminho)).map_err(Falha::nova)
}

/// Full-text search over the history, with the scope the user chose.
///
/// The core answers transcript hits with an instant and a speaker, and note
/// hits with neither -- a note has no position in the timeline, and inventing
/// one would present interpretation as evidence. The interface renders the two
/// differently for exactly that reason.
#[tauri::command]
pub fn busca(termo: String, escopo: String, limite: Option<u32>) -> Resposta<serde_json::Value> {
    let escopo = match escopo.as_str() {
        "transcricoes" | "notas" | "ambos" => escopo,
        _ => "ambos".to_string(),
    };
    cli::search(&termo, &escopo, limite.unwrap_or(50)).map_err(Falha::from)
}

// Notes are read from the meeting's structured export -- see `library.rs` --
// and written through the command line, which regenerates that export on every
// write. The app therefore never keeps its own copy of a note.

#[tauri::command]
pub fn nota_criar(uid: String, tipo: String, conteudo: String) -> Resposta<String> {
    cli::notes_add(&uid, &tipo, &conteudo).map_err(Falha::from)
}

#[tauri::command]
pub fn nota_alterar(id: String, conteudo: String) -> Resposta<String> {
    cli::notes_update(&id, &conteudo).map_err(Falha::from)
}

#[tauri::command]
pub fn nota_remover(id: String) -> Resposta<String> {
    cli::notes_remove(&id).map_err(Falha::from)
}

// -- import ----------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct ResultadoImportacao {
    pub arquivo: String,
    pub ok: bool,
    pub detalhe: String,
}

/// Import several files, reporting each one on its own.
///
/// One bad file must not take the batch down with it: the requirement is that
/// the others import normally and the invalid one is presented individually
/// with its reason.
#[tauri::command]
pub async fn importar(app: AppHandle, arquivos: Vec<String>) -> Vec<ResultadoImportacao> {
    tauri::async_runtime::spawn_blocking(move || {
        let total = arquivos.len();
        let mut resultados = Vec::with_capacity(total);
        for (indice, arquivo) in arquivos.into_iter().enumerate() {
            let _ = app.emit(
                "importacao://progresso",
                serde_json::json!({
                    "indice": indice,
                    "total": total,
                    "arquivo": arquivo,
                }),
            );
            let resultado = match cli::import(&arquivo, "") {
                Ok(saida) => ResultadoImportacao {
                    arquivo: arquivo.clone(),
                    ok: true,
                    detalhe: saida.trim().to_string(),
                },
                Err(err) => ResultadoImportacao {
                    arquivo: arquivo.clone(),
                    ok: false,
                    detalhe: err.mensagem(),
                },
            };
            let _ = app.emit("importacao://resultado", &resultado);
            resultados.push(resultado);
        }
        resultados
    })
    .await
    .unwrap_or_default()
}

// -- settings --------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct DiretorioDeDados {
    pub caminho: String,
    pub fonte: DataDirSource,
    pub fonte_legivel: String,
    pub alteravel: bool,
    pub existe: bool,
    pub livre_bytes: Option<u64>,
    pub total_bytes: Option<u64>,
    /// Roughly what an hour of meeting costs, from the core's own diagnostic
    /// wording: about 220 MB while recording, about 121 MB once compressed.
    pub mb_por_hora_gravando: u32,
    pub mb_por_hora_comprimido: u32,
    pub aviso: Option<String>,
}

/// Below this, the interface says so visibly. Matches the core's `min_free_mb`
/// default of 500 MB with a margin, because the useful warning is the one that
/// arrives before recording stops, not at the moment it does.
const LIMIAR_ESPACO_MB: u64 = 2048;

#[tauri::command]
pub fn diretorio_de_dados() -> DiretorioDeDados {
    let (caminho, fonte) = paths::effective_data_dir();
    let espaco = system::disk_space(&caminho);
    let livre_mb = espaco.map(|(livre, _)| livre / 1_048_576);
    let aviso = match livre_mb {
        Some(mb) if mb < LIMIAR_ESPACO_MB => Some(format!(
            "Restam {} MB neste volume. Uma hora de gravação ocupa cerca de \
             220 MB enquanto grava e cerca de 121 MB depois de comprimida, \
             então cabem aproximadamente {} minutos de reunião.",
            mb,
            (mb * 60) / 220
        )),
        _ => None,
    };
    DiretorioDeDados {
        existe: caminho.is_dir(),
        caminho: display(caminho),
        fonte_legivel: fonte.label().to_string(),
        alteravel: fonte.changeable(),
        fonte,
        livre_bytes: espaco.map(|(livre, _)| livre),
        total_bytes: espaco.map(|(_, total)| total),
        mb_por_hora_gravando: 220,
        mb_por_hora_comprimido: 121,
        aviso,
    }
}

#[tauri::command]
pub fn diretorio_de_dados_validar(caminho: String) -> Resposta<DiretorioDeDados> {
    let alvo = PathBuf::from(&caminho);
    system::writable(&alvo).map_err(|detalhe| {
        Falha::com_acao(
            format!("O caminho escolhido não serve como diretório de dados: {detalhe}"),
            "Escolha um caminho em que o seu usuário possa escrever.",
        )
    })?;
    let espaco = system::disk_space(&alvo);
    Ok(DiretorioDeDados {
        caminho: display(alvo.clone()),
        fonte: DataDirSource::ConfigFile,
        fonte_legivel: DataDirSource::ConfigFile.label().to_string(),
        alteravel: true,
        existe: alvo.is_dir(),
        livre_bytes: espaco.map(|(livre, _)| livre),
        total_bytes: espaco.map(|(_, total)| total),
        mb_por_hora_gravando: 220,
        mb_por_hora_comprimido: 121,
        aviso: None,
    })
}

/// Change the data directory, refusing while the service holds work.
///
/// The refusal is the point: pending work refers to files under the previous
/// directory, so accepting the change would leave a queue pointing at a place
/// the service no longer resolves.
#[tauri::command]
pub fn diretorio_de_dados_alterar(
    cliente: State<'_, ServiceClient>,
    caminho: String,
) -> Resposta<String> {
    let (_, fonte) = paths::effective_data_dir();
    if !fonte.changeable() {
        return Err(Falha::com_acao(
            format!(
                "O diretório de dados efetivo vem da {}, que tem precedência \
                 sobre o arquivo de configuração. Gravar a alteração aqui não \
                 surtiria efeito: o aplicativo continuaria operando sobre o \
                 armazenamento antigo sem nenhum sinal.",
                fonte.label()
            ),
            "Remova a variável de ambiente VOXVAULT_DATA_DIR do seu ambiente e \
             reabra o aplicativo para que a configuração volte a valer.",
        ));
    }
    if cliente.ocupado() {
        return Err(Falha::com_acao(
            "Há gravação ativa, transcrição em andamento ou sessões pendentes na \
             fila. O trabalho pendente se refere a arquivos do diretório atual.",
            "Espere a fila esvaziar e tente de novo. Nada na fila é perdido.",
        ));
    }
    system::writable(Path::new(&caminho)).map_err(Falha::nova)?;
    cli::config_set(&[format!("data_dir={caminho}")]).map_err(Falha::from)?;
    Ok(format!(
        "Diretório de dados gravado como {caminho} na configuração compartilhada.\n\n\
         O conteúdo anterior permaneceu onde estava: nada foi movido nem apagado.\n\n\
         Os servidores MCP em execução continuam ligados ao diretório anterior até \
         que a sessão do cliente seja reiniciada. Reinicie o seu cliente MCP."
    ))
}

#[tauri::command]
pub fn configuracao_gravar(atribuicoes: Vec<String>) -> Resposta<String> {
    cli::config_set(&atribuicoes).map_err(Falha::from)
}

/// The effective configuration with the provenance of every value.
///
/// The provenance is what the settings screen needs in order to refuse an edit
/// it could not honour: a value imposed by the environment outranks the file
/// this screen writes.
#[tauri::command]
pub fn configuracao_ler() -> Resposta<serde_json::Value> {
    cli::config_read().map_err(Falha::from)
}

#[tauri::command]
pub fn dispositivos() -> Resposta<serde_json::Value> {
    cli::devices().map_err(Falha::from)
}

#[tauri::command]
pub fn diagnostico_do_nucleo() -> Resposta<serde_json::Value> {
    cli::doctor().map_err(Falha::from)
}

/// The diagnostics the app can answer by itself, without the core.
///
/// Kept separate from `doctor` on purpose: these are facts about the app's own
/// situation -- is the core environment there, is the service reachable, is the
/// data directory writable -- and the app would be lying if it reported them as
/// coming from the core's diagnostic.
#[derive(Debug, Clone, Serialize)]
pub struct ItemDeDiagnostico {
    pub nome: String,
    pub ok: bool,
    pub detalhe: String,
    pub acao: Option<String>,
}

#[tauri::command]
pub fn diagnostico_do_aplicativo(cliente: State<'_, ServiceClient>) -> Vec<ItemDeDiagnostico> {
    let mut itens = Vec::new();

    let ambiente = ambiente_estado();
    itens.push(ItemDeDiagnostico {
        nome: "Ambiente de execução do núcleo".to_string(),
        ok: ambiente.preparado,
        detalhe: ambiente.detalhe,
        acao: ambiente.acao,
    });

    let servico = cliente.snapshot();
    itens.push(ItemDeDiagnostico {
        nome: "Serviço residente".to_string(),
        ok: matches!(servico.estado, service::ServiceState::Conectado),
        detalhe: servico.detalhe,
        acao: servico.acao,
    });

    let dados = diretorio_de_dados();
    let gravavel = system::writable(Path::new(&dados.caminho));
    itens.push(ItemDeDiagnostico {
        nome: "Diretório de dados".to_string(),
        ok: gravavel.is_ok() && dados.aviso.is_none(),
        detalhe: match (&gravavel, &dados.aviso) {
            (Err(erro), _) => format!("{} — {erro}", dados.caminho),
            (Ok(()), Some(aviso)) => format!("{} — {aviso}", dados.caminho),
            (Ok(()), None) => match dados.livre_bytes {
                Some(livre) => format!(
                    "{} gravável, {:.1} GB livres (origem: {}).",
                    dados.caminho,
                    livre as f64 / 1_073_741_824.0,
                    dados.fonte_legivel
                ),
                None => format!("{} gravável.", dados.caminho),
            },
        },
        acao: gravavel.err().map(|_| {
            "Escolha outro diretório de dados nas configurações.".to_string()
        }),
    });

    itens
}

/// The core's own report on the MCP registration, shown verbatim.
///
/// It names each agent client, whether the server is registered there, and the
/// snippet to add. The app does not compose that snippet: how the server is
/// launched is the core's business, and a second copy here would drift and then
/// fail at the client rather than here.
#[tauri::command]
pub fn mcp_estado() -> Resposta<String> {
    cli::mcp_status().map_err(Falha::from)
}

#[tauri::command]
pub fn mcp_registrar() -> Resposta<String> {
    cli::mcp_apply().map_err(Falha::from)
}

// -- the resident app --------------------------------------------------------
//
// The window can be destroyed and built again at any moment, so what must
// survive it lives in the process: the route to reopen on, and the
// preferences. The interface reports its route on every navigation and asks
// for it before drawing.

#[tauri::command]
pub fn app_registrar_rota(app: AppHandle, rota: String) {
    *app.state::<Residente>().rota.lock().unwrap() = rota;
}

#[tauri::command]
pub fn app_rota_inicial(app: AppHandle) -> String {
    app.state::<Residente>().rota.lock().unwrap().clone()
}

/// The restored route is on screen: how long reopening took.
#[tauri::command]
pub fn app_janela_pronta(app: AppHandle, ms_na_interface: f64) -> janela::MedidaDeAbertura {
    janela::registrar_pronta(&app, ms_na_interface)
}

#[tauri::command]
pub fn app_ultima_abertura(app: AppHandle) -> Option<janela::MedidaDeAbertura> {
    *app.state::<Residente>().ultima_abertura.lock().unwrap()
}

/// Everything the "Aplicativo" section of the settings shows.
#[derive(Debug, Clone, Serialize)]
pub struct Aplicativo {
    pub preferencias: prefs::Preferencias,
    /// Why the preferences file was set aside, when it was.
    pub preferencias_ilegiveis: Option<String>,
    /// Read from the registry, which is the only truth about it.
    pub inicio_com_o_windows: Option<bool>,
    pub inicio_com_o_windows_erro: Option<String>,
    pub atalho: crate::atalho::EstadoDoAtalho,
}

fn aplicativo(app: &AppHandle) -> Aplicativo {
    use tauri_plugin_autostart::ManagerExt;

    let estado = app.state::<Residente>();
    let (inicio, erro) = match app.autolaunch().is_enabled() {
        Ok(ligado) => (Some(ligado), None),
        Err(erro) => (None, Some(erro.to_string())),
    };
    let preferencias = estado.preferencias.lock().unwrap().clone();
    let ilegiveis = estado.preferencias_ilegiveis.lock().unwrap().clone();
    let atalho = estado.atalho.lock().unwrap().clone();
    Aplicativo {
        preferencias,
        preferencias_ilegiveis: ilegiveis,
        inicio_com_o_windows: inicio,
        inicio_com_o_windows_erro: erro,
        atalho,
    }
}

#[tauri::command]
pub fn aplicativo_ler(app: AppHandle) -> Aplicativo {
    aplicativo(&app)
}

#[tauri::command]
pub fn notificacoes_definir(app: AppHandle, chaves: prefs::Notificacoes) -> Resposta<Aplicativo> {
    let estado = app.state::<Residente>();
    {
        let mut preferencias = estado.preferencias.lock().unwrap();
        preferencias.notificacoes = chaves;
        prefs::gravar(&prefs::caminho(), &preferencias).map_err(|erro| {
            Falha::nova(format!("As preferências não puderam ser gravadas: {erro}"))
        })?;
    }
    *estado.preferencias_ilegiveis.lock().unwrap() = None;
    Ok(aplicativo(&app))
}

/// Register the new shortcut before releasing the old one; on a conflict the
/// old one stays in force and the cause comes back.
#[tauri::command]
pub fn atalho_trocar(app: AppHandle, atalho: String) -> Resposta<Aplicativo> {
    crate::atalho::trocar(&app, atalho.trim()).map_err(Falha::nova)?;
    Ok(aplicativo(&app))
}

#[tauri::command]
pub fn inicio_com_o_windows_definir(app: AppHandle, ligado: bool) -> Resposta<Aplicativo> {
    use tauri_plugin_autostart::ManagerExt;

    let resultado = if ligado {
        app.autolaunch().enable()
    } else {
        app.autolaunch().disable()
    };
    resultado.map_err(|erro| {
        Falha::nova(format!(
            "Não foi possível {} o início com o Windows: {erro}",
            if ligado { "ligar" } else { "desligar" }
        ))
    })?;
    Ok(aplicativo(&app))
}

/// Show one sample of a category, to check that notifications reach this
/// machine's screen. Honours the category switch like any other.
#[tauri::command]
pub fn notificacao_de_teste(app: AppHandle, categoria: String) -> Resposta<()> {
    let categoria = avisos::Categoria::de_nome(&categoria)
        .ok_or_else(|| Falha::nova(format!("Categoria de notificação desconhecida: {categoria}")))?;
    avisos::mostrar(&app, avisos::Aviso::exemplo(categoria));
    Ok(())
}

fn display(path: PathBuf) -> String {
    path.to_string_lossy().into_owned()
}
