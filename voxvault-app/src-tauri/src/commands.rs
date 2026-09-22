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
use tauri::{AppHandle, Emitter, State};

use crate::cli::{self, CoreError};
use crate::library;
use crate::paths::{self, DataDirSource};
use crate::service::{self, ServiceClient};
use crate::system;

/// A missing machine-readable surface in the core, named precisely.
#[derive(Debug, Clone, Serialize)]
pub struct Pendencia {
    pub comando: String,
    pub sinalizador: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Falha {
    pub mensagem: String,
    pub acao: Option<String>,
    pub pendencia: Option<Pendencia>,
}

impl Falha {
    pub fn nova(mensagem: impl Into<String>) -> Self {
        Self {
            mensagem: mensagem.into(),
            acao: None,
            pendencia: None,
        }
    }

    pub fn com_acao(mensagem: impl Into<String>, acao: impl Into<String>) -> Self {
        Self {
            mensagem: mensagem.into(),
            acao: Some(acao.into()),
            pendencia: None,
        }
    }
}

impl From<CoreError> for Falha {
    fn from(err: CoreError) -> Self {
        let pendencia = match &err {
            CoreError::SemJson {
                comando,
                sinalizador,
                ..
            } => Some(Pendencia {
                comando: comando.clone(),
                sinalizador: sinalizador.clone(),
            }),
            _ => None,
        };
        Falha {
            mensagem: err.mensagem(),
            acao: match &err {
                CoreError::NaoPreparado { .. } => Some(
                    "Prepare o ambiente de execução na tela inicial do aplicativo."
                        .to_string(),
                ),
                _ => None,
            },
            pendencia,
        }
    }
}

type Resposta<T> = Result<T, Falha>;

// -- environment -----------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct Ambiente {
    pub preparado: bool,
    pub raiz_do_nucleo: Option<String>,
    pub executavel: Option<String>,
    pub detalhe: String,
    pub acao: Option<String>,
}

#[tauri::command]
pub fn ambiente_estado() -> Ambiente {
    let raiz = paths::core_root();
    let executavel = paths::core_executable();
    match (&raiz, &executavel) {
        (Some(_), Some(_)) => Ambiente {
            preparado: true,
            raiz_do_nucleo: raiz.map(display),
            executavel: executavel.map(display),
            detalhe: "Ambiente de execução do núcleo pronto.".to_string(),
            acao: None,
        },
        (Some(raiz_encontrada), None) => Ambiente {
            preparado: false,
            raiz_do_nucleo: Some(display(raiz_encontrada.clone())),
            executavel: None,
            detalhe: format!(
                "O núcleo foi encontrado em {}, mas seu ambiente de execução \
                 ainda não existe. Ele precisa ser preparado uma única vez: o \
                 interpretador e as bibliotecas de GPU não vêm dentro do \
                 aplicativo, justamente para que ele continue pequeno.",
                raiz_encontrada.display()
            ),
            acao: Some("Preparar o ambiente agora.".to_string()),
        },
        _ => Ambiente {
            preparado: false,
            raiz_do_nucleo: None,
            executavel: None,
            detalhe: "O diretório do núcleo (voxvault-core) não foi encontrado a \
                      partir da localização do aplicativo."
                .to_string(),
            acao: Some(
                "Defina a variável de ambiente VOXVAULT_CORE_ROOT apontando para \
                 o diretório do núcleo e reabra o aplicativo."
                    .to_string(),
            ),
        },
    }
}

/// Prepare the core's managed environment, reporting progress as it goes.
///
/// `uv` is invoked rather than reimplemented: it is already on this machine, it
/// downloads its own interpreter, and it is what the core's own instructions
/// use. Its output is forwarded line by line so the wait is visible instead of
/// being a window that looks hung.
#[tauri::command]
pub async fn ambiente_preparar(app: AppHandle) -> Resposta<String> {
    let raiz = paths::core_root().ok_or_else(|| {
        Falha::com_acao(
            "O diretório do núcleo não foi encontrado, então não há o que preparar.",
            "Defina VOXVAULT_CORE_ROOT e reabra o aplicativo.",
        )
    })?;

    let resultado = tauri::async_runtime::spawn_blocking(move || {
        use std::io::{BufRead, BufReader};
        use std::process::{Command, Stdio};

        let mut command = Command::new("uv");
        command
            .arg("sync")
            .current_dir(&raiz)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000);
        }

        let mut child = command.spawn().map_err(|err| {
            format!(
                "Não foi possível executar `uv sync` em {}: {err}. \
                 O `uv` precisa estar instalado e no PATH.",
                raiz.display()
            )
        })?;

        if let Some(stderr) = child.stderr.take() {
            // `uv` reports progress on stderr; each line becomes one visible
            // step rather than a spinner that says nothing.
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                let _ = app.emit("ambiente://progresso", line);
            }
        }
        if let Some(stdout) = child.stdout.take() {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                let _ = app.emit("ambiente://progresso", line);
            }
        }

        let status = child
            .wait()
            .map_err(|err| format!("O preparo terminou de forma indeterminada: {err}"))?;
        if !status.success() {
            return Err(format!(
                "`uv sync` terminou com código {}. O ambiente não ficou utilizável.",
                status.code().unwrap_or(-1)
            ));
        }
        Ok(())
    })
    .await
    .map_err(|err| Falha::nova(format!("O preparo foi interrompido: {err}")))?;

    resultado.map_err(|mensagem: String| {
        Falha::com_acao(
            mensagem,
            "Rode `uv sync` no diretório do núcleo num terminal para ver a causa \
             completa, corrija-a e tente de novo.",
        )
    })?;

    // Preparation is only done when the core answers, not when the installer
    // exits zero. The gate has to be a working command, or the main interface
    // would open with commands that merely look functional.
    cli::probe(std::time::Duration::from_secs(30))?;
    Ok("Ambiente de execução preparado.".to_string())
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

#[tauri::command]
pub fn gravacao_iniciar(
    cliente: State<'_, ServiceClient>,
    titulo: String,
) -> Resposta<serde_json::Value> {
    let corpo = serde_json::json!({ "titulo": titulo }).to_string();
    service::call(&cliente, "POST", "/gravacao/iniciar", Some(&corpo)).map_err(Falha::nova)
}

#[tauri::command]
pub fn gravacao_pausar(cliente: State<'_, ServiceClient>) -> Resposta<serde_json::Value> {
    service::call(&cliente, "POST", "/gravacao/pausar", None).map_err(Falha::nova)
}

#[tauri::command]
pub fn gravacao_retomar(cliente: State<'_, ServiceClient>) -> Resposta<serde_json::Value> {
    service::call(&cliente, "POST", "/gravacao/retomar", None).map_err(Falha::nova)
}

#[tauri::command]
pub fn gravacao_encerrar(cliente: State<'_, ServiceClient>) -> Resposta<serde_json::Value> {
    service::call(&cliente, "POST", "/gravacao/encerrar", None).map_err(Falha::nova)
}

// -- library ---------------------------------------------------------------

#[tauri::command]
pub fn reunioes_listar() -> Vec<library::Resumo> {
    let (data_dir, _) = paths::effective_data_dir();
    library::listar(&data_dir)
}

#[tauri::command]
pub fn reuniao_detalhar(uid: String) -> Resposta<library::Detalhe> {
    let (data_dir, _) = paths::effective_data_dir();
    library::detalhar(&data_dir, &uid).ok_or_else(|| {
        Falha::nova(format!(
            "A reunião {uid} não foi encontrada no diretório de dados."
        ))
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
pub fn reuniao_renomear(_uid: String, _titulo: String) -> Resposta<()> {
    Err(Falha::from(CoreError::sem_json(
        "rename",
        "voxvault rename <uid> --title <titulo>",
    )))
}

#[tauri::command]
pub fn reuniao_remover_audio(_uid: String) -> Resposta<()> {
    Err(Falha::from(CoreError::sem_json(
        "remove-audio",
        "voxvault remove-audio <uid>",
    )))
}

#[tauri::command]
pub fn reuniao_abrir_pasta(uid: String) -> Resposta<()> {
    let (data_dir, _) = paths::effective_data_dir();
    system::reveal(&library::meeting_dir(&data_dir, &uid)).map_err(Falha::nova)
}

#[tauri::command]
pub fn abrir_caminho(caminho: String) -> Resposta<()> {
    system::reveal(Path::new(&caminho)).map_err(Falha::nova)
}

#[tauri::command]
pub fn busca(_termo: String, _limite: u32) -> Resposta<serde_json::Value> {
    Err(Falha::from(CoreError::sem_json("search", "search --json")))
}

#[tauri::command]
pub fn notas_listar(_uid: String) -> Resposta<serde_json::Value> {
    Err(Falha::com_acao(
        "As notas de reunião ainda não existem no núcleo: não há tabela, comando \
         nem arquivo que as guarde. A área de notas aparece vazia por isso, e não \
         porque esta reunião não tenha notas.",
        "Depende da capacidade `meeting-notes`, prevista para a fase 2 do núcleo.",
    ))
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

#[tauri::command]
pub fn configuracao_ler() -> Resposta<serde_json::Value> {
    Err(Falha::from(CoreError::sem_json("config", "config --json")))
}

#[tauri::command]
pub fn dispositivos() -> Resposta<serde_json::Value> {
    Err(Falha::from(CoreError::sem_json("devices", "devices --json")))
}

#[tauri::command]
pub fn diagnostico_do_nucleo() -> Resposta<serde_json::Value> {
    Err(Falha::from(CoreError::sem_json("doctor", "doctor --json")))
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

/// The snippet a user pastes into their agent client to register the MCP server.
#[tauri::command]
pub fn trecho_mcp() -> String {
    let executavel = paths::core_executable()
        .map(display)
        .unwrap_or_else(|| r"E:\projetosAleatorios\VoxVault\voxvault-core\.venv\Scripts\voxvault.exe".to_string());
    serde_json::to_string_pretty(&serde_json::json!({
        "mcpServers": {
            "voxvault": {
                "command": executavel,
                "args": ["mcp"]
            }
        }
    }))
    .unwrap_or_default()
}

// -- closing ---------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct EstadoDeFechamento {
    pub gravando: bool,
    pub fila_pendente: u32,
    pub duracao_ms: i64,
}

#[tauri::command]
pub fn estado_de_fechamento(cliente: State<'_, ServiceClient>) -> EstadoDeFechamento {
    let snapshot = cliente.snapshot();
    let saude = snapshot.saude.unwrap_or_default();
    EstadoDeFechamento {
        gravando: saude.gravacao_ativa,
        fila_pendente: saude.fila_pendente,
        duracao_ms: 0,
    }
}

fn display(path: PathBuf) -> String {
    path.to_string_lossy().into_owned()
}
