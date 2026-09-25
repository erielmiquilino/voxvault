//! The first-use preparation: from an installed app to a core that runs.
//!
//! The installer carries the core's sources, `uv.exe` and `preparo.json`; the
//! interpreter, the dependencies, the GPU libraries and the model are fetched
//! here, once, into `%USERPROFILE%\.voxvault\runtime` and the data directory.
//! Each step is idempotent, so resuming an interrupted preparation is running
//! it again: what finished passes in seconds, without a download.
//!
//! | Step           | What runs                                        | Skipped when                  |
//! |----------------|--------------------------------------------------|-------------------------------|
//! | `interpretador`| `uv python install 3.12 --no-bin --no-registry`  | already in `runtime\python`   |
//! | `dependencias` | `uv sync --extra engine --extra mcp`, cache first | never (in date in seconds)    |
//! | `gpu`          | `uv sync ... --extra cuda`                       | no GPU that holds a model     |
//! | `configuracao` | `voxvault config data_dir=...`                   | the same value is configured  |
//! | `modelo`       | `voxvault models download --json`                | every file already on disk    |
//! | `verificacao`  | `voxvault doctor --json`                         | never                         |
//!
//! Only a verification that passes writes `runtime\preparado.json`. At every
//! start, that stamp against the installed version and lock says whether the
//! environment is ready, and any difference prepares it again.

use std::collections::BTreeMap;
use std::io::{BufRead, BufReader, Read};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};

use crate::hardware::{self, Escolha, Hardware, Limites};
use crate::paths::{self, DataDirSource};
use crate::system;

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const VERSAO_DO_APP: &str = env!("CARGO_PKG_VERSION");
/// The interpreter line the core targets; uv picks the patch it knows.
const PYTHON: &str = "3.12";
const EVENTO_ETAPA: &str = "preparo://etapa";
const EVENTO_LINHA: &str = "preparo://linha";
/// How often the bytes a step has fetched so far are measured on disk.
const INTERVALO_DE_MEDIDA: Duration = Duration::from_secs(1);

/// Diagnostic items whose failure means the environment does not work. The
/// others -- capture devices, the MCP registration -- describe this machine's
/// situation, not the preparation, and have their own screens.
const ITENS_ESSENCIAIS: &[&str] = &["python", "bibliotecas", "data_dir", "midia", "inferencia", "modelo"];

// -- what the build measured ------------------------------------------------------

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Parte {
    #[serde(default)]
    pub pacotes: u32,
    pub bytes: u64,
    #[serde(default)]
    pub bytes_em_disco: u64,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Interpretador {
    pub versao: String,
    pub bytes: u64,
    pub bytes_em_disco: u64,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Modelo {
    pub repositorio: String,
    pub bytes: u64,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Folga {
    pub ambiente: f64,
    pub dados_bytes: u64,
}

/// `recursos\preparo.json`, written by `tools/gerar-manifesto-preparo.py`.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Manifesto {
    pub sha256_lock: String,
    pub interpretador: Interpretador,
    pub dependencias: Parte,
    pub gpu: Parte,
    pub modelos: BTreeMap<String, Modelo>,
    pub vram_minima_mb: BTreeMap<String, u32>,
    pub folga: Folga,
}

impl Manifesto {
    pub fn limites(&self) -> Limites {
        Limites {
            large_v3_mb: self.vram_minima_mb.get("large-v3").copied().unwrap_or(5600),
            turbo_mb: self.vram_minima_mb.get("large-v3-turbo").copied().unwrap_or(2500),
        }
    }
}

pub fn ler_manifesto() -> Option<Manifesto> {
    let arquivo = paths::recursos_dir()?.join("preparo.json");
    serde_json::from_str(&std::fs::read_to_string(arquivo).ok()?).ok()
}

// -- the stamp --------------------------------------------------------------------

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct Carimbo {
    pub versao_app: String,
    pub sha256_lock: String,
    pub gpu: bool,
    pub modelo: String,
    pub concluido_em: String,
}

fn arquivo_do_carimbo(runtime: &Path) -> PathBuf {
    runtime.join("preparado.json")
}

pub fn ler_carimbo(runtime: &Path) -> Option<Carimbo> {
    serde_json::from_str(&std::fs::read_to_string(arquivo_do_carimbo(runtime)).ok()?).ok()
}

fn gravar_carimbo(runtime: &Path, carimbo: &Carimbo) -> std::io::Result<()> {
    let texto = serde_json::to_string_pretty(carimbo).map_err(std::io::Error::other)?;
    let destino = arquivo_do_carimbo(runtime);
    let temporario = destino.with_extension("json.tmp");
    std::fs::write(&temporario, texto)?;
    std::fs::rename(&temporario, destino)
}

/// Where this installation stands, decided without running anything.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "tipo", rename_all = "snake_case")]
pub enum Situacao {
    /// Prepared for this version and this lock.
    Pronto,
    /// A development build on the checkout's own environment.
    Desenvolvimento,
    /// Never prepared: the full screen, sizes first, and a confirmation.
    /// `retomada` when an earlier attempt left something behind.
    Primeiro { retomada: bool },
    /// Prepared for something else: prepared again, without asking.
    Desatualizado { motivo: String },
    /// Installed without the resources it prepares from.
    SemRecursos,
}

pub struct Estado<'a> {
    pub manifesto: Option<&'a Manifesto>,
    pub carimbo: Option<&'a Carimbo>,
    /// `runtime\ambiente\Scripts\voxvault.exe` exists.
    pub executavel_preparado: bool,
    /// A debug build that found the checkout's `.venv`.
    pub desenvolvimento: bool,
    /// Something of an earlier attempt is in `runtime`.
    pub runtime_existe: bool,
    pub versao: &'a str,
}

pub fn avaliar(estado: Estado<'_>) -> Situacao {
    if estado.desenvolvimento && !estado.executavel_preparado {
        return Situacao::Desenvolvimento;
    }
    let Some(manifesto) = estado.manifesto else {
        return if estado.desenvolvimento {
            Situacao::Desenvolvimento
        } else {
            Situacao::SemRecursos
        };
    };
    let Some(carimbo) = estado.carimbo else {
        return Situacao::Primeiro {
            retomada: estado.runtime_existe,
        };
    };
    let motivo = if carimbo.versao_app != estado.versao {
        format!(
            "O ambiente foi preparado para a versão {} e esta é a {}.",
            carimbo.versao_app, estado.versao
        )
    } else if carimbo.sha256_lock != manifesto.sha256_lock {
        "As dependências travadas mudaram desde o último preparo.".to_string()
    } else if !estado.executavel_preparado {
        "O ambiente preparado não está completo.".to_string()
    } else {
        return Situacao::Pronto;
    };
    Situacao::Desatualizado { motivo }
}

pub fn situacao_atual() -> Situacao {
    let runtime = paths::runtime_dir();
    let manifesto = ler_manifesto();
    let carimbo = ler_carimbo(&runtime);
    let preparado = paths::ambiente_dir().join("Scripts").join("voxvault.exe").is_file();
    let desenvolvimento = cfg!(debug_assertions)
        && paths::core_executable().is_some_and(|exe| !exe.starts_with(paths::ambiente_dir()));
    avaliar(Estado {
        manifesto: manifesto.as_ref(),
        carimbo: carimbo.as_ref(),
        executavel_preparado: preparado,
        desenvolvimento,
        runtime_existe: runtime.join("ambiente").exists() || runtime.join("python").exists(),
        versao: VERSAO_DO_APP,
    })
}

// -- the plan shown before anything is downloaded ------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct ItemDoPlano {
    pub etapa: String,
    pub rotulo: String,
    /// What goes over the network.
    pub bytes: u64,
    /// What it takes once in place.
    pub bytes_em_disco: u64,
    /// Already there: nothing to download for it.
    pub presente: bool,
    pub volume: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Volume {
    pub raiz: String,
    pub livre: Option<u64>,
    pub necessario: u64,
    pub falta: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct Plano {
    pub hardware: Hardware,
    pub escolha: Escolha,
    pub pasta: String,
    pub pasta_origem: DataDirSource,
    pub pasta_origem_legivel: String,
    pub itens: Vec<ItemDoPlano>,
    pub total_bytes: u64,
    pub volumes: Vec<Volume>,
    pub pode_iniciar: bool,
    pub recusa: Option<String>,
    pub situacao: Situacao,
}

/// `C:` for `C:\Users\...`: space is asked per volume.
pub fn raiz_do_volume(caminho: &Path) -> String {
    match caminho.components().next() {
        Some(Component::Prefix(prefixo)) => prefixo.as_os_str().to_string_lossy().to_uppercase(),
        _ => caminho.display().to_string(),
    }
}

/// Binary gigabytes, the unit Windows itself shows free space in.
pub fn gb(bytes: u64) -> String {
    format!("{:.1} GB", bytes as f64 / 1_073_741_824.0).replace('.', ",")
}

/// What is needed where, and whether it fits -- the pure core of the plan.
pub struct Entrada<'a> {
    pub manifesto: &'a Manifesto,
    pub escolha: &'a Escolha,
    pub runtime: &'a Path,
    pub pasta: &'a Path,
    pub interpretador_presente: bool,
    pub modelo_presente: bool,
}

pub fn itens_e_volumes(
    entrada: Entrada<'_>,
    livre: impl Fn(&Path) -> Option<u64>,
) -> (Vec<ItemDoPlano>, Vec<Volume>, Option<String>) {
    let m = entrada.manifesto;
    let perfil = raiz_do_volume(entrada.runtime);
    let dados = raiz_do_volume(entrada.pasta);
    let modelo = m.modelos.get(&entrada.escolha.modelo);

    let mut itens = vec![
        ItemDoPlano {
            etapa: "interpretador".into(),
            rotulo: format!("Interpretador Python {}", m.interpretador.versao),
            bytes: m.interpretador.bytes,
            bytes_em_disco: m.interpretador.bytes_em_disco,
            presente: entrada.interpretador_presente,
            volume: perfil.clone(),
        },
        ItemDoPlano {
            etapa: "dependencias".into(),
            rotulo: format!("Dependências ({} pacotes)", m.dependencias.pacotes),
            bytes: m.dependencias.bytes,
            bytes_em_disco: m.dependencias.bytes_em_disco,
            presente: false,
            volume: perfil.clone(),
        },
    ];
    if entrada.escolha.etapa_gpu {
        itens.push(ItemDoPlano {
            etapa: "gpu".into(),
            rotulo: "Aceleração por GPU (CUDA)".into(),
            bytes: m.gpu.bytes,
            bytes_em_disco: m.gpu.bytes_em_disco,
            presente: false,
            volume: perfil.clone(),
        });
    }
    itens.push(ItemDoPlano {
        etapa: "modelo".into(),
        rotulo: format!("Modelo de transcrição {}", entrada.escolha.modelo),
        bytes: modelo.map(|x| x.bytes).unwrap_or(0),
        bytes_em_disco: modelo.map(|x| x.bytes).unwrap_or(0),
        presente: entrada.modelo_presente,
        volume: dados.clone(),
    });

    // The environment, with a fifth more for uv's work; the model with a
    // gigabyte beside it for the first recordings.
    let ambiente: u64 = itens
        .iter()
        .filter(|i| i.volume == perfil && i.etapa != "modelo" && !i.presente)
        .map(|i| i.bytes_em_disco)
        .sum();
    let ambiente = (ambiente as f64 * (1.0 + m.folga.ambiente)) as u64;
    let modelo_bytes = if entrada.modelo_presente {
        0
    } else {
        modelo.map(|x| x.bytes).unwrap_or(0)
    };
    let dados_necessario = modelo_bytes + m.folga.dados_bytes;

    let mut volumes: Vec<Volume> = Vec::new();
    let mut somar = |raiz: &str, caminho: &Path, bytes: u64| {
        if let Some(v) = volumes.iter_mut().find(|v| v.raiz == raiz) {
            v.necessario += bytes;
        } else {
            volumes.push(Volume {
                raiz: raiz.to_string(),
                livre: livre(caminho),
                necessario: bytes,
                falta: 0,
            });
        }
    };
    somar(&perfil, entrada.runtime, ambiente);
    somar(&dados, entrada.pasta, dados_necessario);

    let mut faltas = Vec::new();
    for v in &mut volumes {
        if let Some(livre) = v.livre {
            v.falta = v.necessario.saturating_sub(livre);
            if v.falta > 0 {
                faltas.push(format!(
                    "faltam {} no volume {} (são necessários {} e há {} livres)",
                    gb(v.falta),
                    v.raiz,
                    gb(v.necessario),
                    gb(livre)
                ));
            }
        }
    }
    let recusa = (!faltas.is_empty()).then(|| {
        format!(
            "Não há espaço para preparar o VoxVault: {}. Libere espaço ou escolha \
             outra pasta de dados.",
            faltas.join("; ")
        )
    });
    (itens, volumes, recusa)
}

/// A model folder the engine would load, checked the way the core checks it:
/// the branch the download recorded, and every file in its snapshot.
pub fn modelo_presente(models_dir: &Path, repositorio: &str) -> bool {
    let pasta = models_dir.join(format!("models--{}", repositorio.replace('/', "--")));
    let Ok(commit) = std::fs::read_to_string(pasta.join("refs").join("main")) else {
        return false;
    };
    let snapshot = pasta.join("snapshots").join(commit.trim());
    let essenciais = ["config.json", "model.bin", "tokenizer.json"]
        .iter()
        .all(|nome| snapshot.join(nome).is_file());
    let vocabulario = ["vocabulary.json", "vocabulary.txt"]
        .iter()
        .any(|nome| snapshot.join(nome).is_file());
    essenciais && vocabulario
}

fn interpretador_presente(runtime: &Path) -> bool {
    std::fs::read_dir(runtime.join("python"))
        .map(|entradas| {
            entradas.flatten().any(|e| {
                let nome = e.file_name().to_string_lossy().to_string();
                nome.starts_with(&format!("cpython-{PYTHON}")) && e.path().join("python.exe").is_file()
            })
        })
        .unwrap_or(false)
}

/// The escolha a re-preparation keeps: the one the stamp recorded, so an
/// update never switches models or pulls in CUDA behind the user's back.
fn escolha_do_carimbo(carimbo: &Carimbo) -> Escolha {
    Escolha {
        modelo: carimbo.modelo.clone(),
        dispositivo: if carimbo.gpu { "cuda" } else { "cpu" }.to_string(),
        etapa_gpu: carimbo.gpu,
    }
}

pub fn planejar(pasta: Option<String>) -> Result<Plano, FalhaDoPreparo> {
    let manifesto = ler_manifesto().ok_or_else(sem_recursos)?;
    let runtime = paths::runtime_dir();
    let hardware = hardware::detectar();
    let escolha = match ler_carimbo(&runtime) {
        Some(carimbo) => escolha_do_carimbo(&carimbo),
        None => hardware::escolher(hardware.gpu.as_ref(), manifesto.limites()),
    };
    let (padrao, origem) = paths::effective_data_dir();
    let (pasta, origem) = match pasta.map(|p| p.trim().to_string()).filter(|p| !p.is_empty()) {
        Some(escolhida) => (PathBuf::from(escolhida), DataDirSource::ConfigFile),
        None => (padrao, origem),
    };
    let presente = manifesto
        .modelos
        .get(&escolha.modelo)
        .is_some_and(|m| modelo_presente(&pasta.join("models"), &m.repositorio));
    let (itens, volumes, recusa) = itens_e_volumes(
        Entrada {
            manifesto: &manifesto,
            escolha: &escolha,
            runtime: &runtime,
            pasta: &pasta,
            interpretador_presente: interpretador_presente(&runtime),
            modelo_presente: presente,
        },
        |caminho| system::disk_space(caminho).map(|(livre, _)| livre),
    );
    let total_bytes = itens.iter().filter(|i| !i.presente).map(|i| i.bytes).sum();
    Ok(Plano {
        hardware,
        escolha,
        pasta: pasta.display().to_string(),
        pasta_origem_legivel: origem.label().to_string(),
        pasta_origem: origem,
        itens,
        total_bytes,
        volumes,
        pode_iniciar: recusa.is_none(),
        recusa,
        situacao: situacao_atual(),
    })
}

// -- running it ------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct FalhaDoPreparo {
    pub etapa: String,
    pub mensagem: String,
    pub acao: String,
}

fn sem_recursos() -> FalhaDoPreparo {
    FalhaDoPreparo {
        etapa: "recursos".into(),
        mensagem: "A instalação está incompleta: faltam os recursos do núcleo \
                   (recursos\\preparo.json)."
            .into(),
        acao: "Reinstale o VoxVault a partir do instalador da release.".into(),
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct Progresso {
    pub etapa: String,
    /// `em_andamento`, `concluida`, `pulada` or `falhou`.
    pub estado: String,
    pub detalhe: String,
    pub baixado: Option<u64>,
    pub total: Option<u64>,
}

static EM_ANDAMENTO: AtomicBool = AtomicBool::new(false);

/// While a preparation runs, nothing may start the service from the
/// environment being rebuilt under it.
pub fn em_andamento() -> bool {
    EM_ANDAMENTO.load(Ordering::SeqCst)
}

struct Vez;

impl Drop for Vez {
    fn drop(&mut self) {
        EM_ANDAMENTO.store(false, Ordering::SeqCst);
    }
}

fn avisar(app: &AppHandle, etapa: &str, estado: &str, detalhe: impl Into<String>) {
    let _ = app.emit(
        EVENTO_ETAPA,
        Progresso {
            etapa: etapa.into(),
            estado: estado.into(),
            detalhe: detalhe.into(),
            baixado: None,
            total: None,
        },
    );
}

fn tamanho_da_pasta(pasta: &Path) -> u64 {
    let mut total = 0;
    let mut pendentes = vec![pasta.to_path_buf()];
    while let Some(atual) = pendentes.pop() {
        let Ok(entradas) = std::fs::read_dir(&atual) else { continue };
        for entrada in entradas.flatten() {
            match entrada.file_type() {
                Ok(tipo) if tipo.is_dir() => pendentes.push(entrada.path()),
                Ok(tipo) if tipo.is_file() => {
                    total += entrada.metadata().map(|m| m.len()).unwrap_or(0);
                }
                _ => {}
            }
        }
    }
    total
}

/// The environment every uv call runs in: every location explicit, nothing
/// inherited from a Python the user may have configured, and interpreters only
/// ever installed by the step that says so.
fn comando_uv(uv: &Path, runtime: &Path) -> Command {
    let mut comando = Command::new(uv);
    comando
        .env("UV_PROJECT_ENVIRONMENT", runtime.join("ambiente"))
        .env("UV_PYTHON_INSTALL_DIR", runtime.join("python"))
        .env("UV_CACHE_DIR", runtime.join("cache"))
        .env("UV_NO_CONFIG", "1")
        .env("UV_PYTHON_DOWNLOADS", "manual")
        .env("NO_COLOR", "1")
        .env_remove("VIRTUAL_ENV")
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        comando.creation_flags(CREATE_NO_WINDOW);
    }
    comando
}

fn comando_do_nucleo(runtime: &Path) -> Result<Command, FalhaDoPreparo> {
    let exe = paths::core_executable().ok_or_else(|| FalhaDoPreparo {
        etapa: "dependencias".into(),
        mensagem: "O ambiente foi montado, mas o executável do núcleo não apareceu nele.".into(),
        acao: "Clique em Retomar para montá-lo de novo.".into(),
    })?;
    let mut comando = Command::new(exe);
    comando
        .env("HF_HOME", runtime.join("hf"))
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        .env("NO_PROXY", paths::sem_proxy_no_loopback())
        .env("no_proxy", paths::sem_proxy_no_loopback())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        comando.creation_flags(CREATE_NO_WINDOW);
    }
    Ok(comando)
}

/// Run one step's process, forwarding its lines, and -- when `medir` names a
/// folder -- how many bytes it has put there so far against `total`.
fn executar(
    app: &AppHandle,
    etapa: &str,
    mut comando: Command,
    medir: Option<(PathBuf, u64)>,
    mut ao_ler_linha: impl FnMut(&str),
) -> Result<(bool, String), String> {
    let mut filho = comando.spawn().map_err(|err| err.to_string())?;
    let saida_de_erro = Arc::new(Mutex::new(String::new()));

    let leitor_de_erro = filho.stderr.take().map(|stderr| {
        let app = app.clone();
        let etapa = etapa.to_string();
        let acumulado = Arc::clone(&saida_de_erro);
        std::thread::spawn(move || {
            for linha in BufReader::new(stderr).lines().map_while(Result::ok) {
                if let Ok(mut texto) = acumulado.lock() {
                    // The tail is what explains a failure; the head of a long
                    // resolution is noise, and holding it all is memory.
                    if texto.len() > 32_000 {
                        let corte = texto.len() - 16_000;
                        let corte = (corte..texto.len()).find(|i| texto.is_char_boundary(*i)).unwrap_or(0);
                        texto.drain(..corte);
                    }
                    texto.push_str(&linha);
                    texto.push('\n');
                }
                let _ = app.emit(EVENTO_LINHA, serde_json::json!({ "etapa": etapa, "linha": linha }));
            }
        })
    });

    let parar = Arc::new(AtomicBool::new(false));
    let medidor = medir.map(|(pasta, total)| {
        let app = app.clone();
        let etapa = etapa.to_string();
        let parar = Arc::clone(&parar);
        let inicio = tamanho_da_pasta(&pasta);
        std::thread::spawn(move || {
            while !parar.load(Ordering::SeqCst) {
                std::thread::sleep(INTERVALO_DE_MEDIDA);
                let feito = tamanho_da_pasta(&pasta).saturating_sub(inicio).min(total);
                let _ = app.emit(
                    EVENTO_ETAPA,
                    Progresso {
                        etapa: etapa.clone(),
                        estado: "em_andamento".into(),
                        detalhe: String::new(),
                        baixado: Some(feito),
                        total: Some(total),
                    },
                );
            }
        })
    });

    if let Some(stdout) = filho.stdout.take() {
        for linha in BufReader::new(stdout).lines().map_while(Result::ok) {
            ao_ler_linha(&linha);
            let _ = app.emit(EVENTO_LINHA, serde_json::json!({ "etapa": etapa, "linha": linha }));
        }
    }
    let status = filho.wait().map_err(|err| err.to_string())?;
    parar.store(true, Ordering::SeqCst);
    if let Some(t) = leitor_de_erro {
        let _ = t.join();
    }
    if let Some(t) = medidor {
        let _ = t.join();
    }
    let texto = saida_de_erro.lock().map(|t| t.clone()).unwrap_or_default();
    Ok((status.success(), texto))
}

/// A failed uv call in words, and what to do about it.
pub fn causa(etapa: &str, o_que: &str, saida: &str) -> FalhaDoPreparo {
    let texto = saida.to_lowercase();
    let tem = |termos: &[&str]| termos.iter().any(|t| texto.contains(t));
    let (mensagem, acao) = if tem(&[
        "dns error",
        "failed to lookup address",
        "no such host is known",
        "tcp connect error",
        "connection refused",
        "connection reset",
        "error sending request",
        "failed to fetch",
        "timed out",
        "network is unreachable",
        "certificate",
        "tls handshake",
        "os error 11001",
        "os error 10060",
        "os error 10061",
        "sem conexao com a internet",
    ]) {
        (
            format!("Sem conexão com a internet: o preparo precisa baixar {o_que}."),
            "Conecte-se e clique em Retomar: o que já foi baixado é aproveitado.".to_string(),
        )
    } else if tem(&["os error 225", "os error 226", "contains a virus", "contém um vírus"]) {
        (
            format!("O antivírus bloqueou um arquivo do preparo ({o_que})."),
            "Se você confia no VoxVault, libere a pasta %USERPROFILE%\\.voxvault no \
             antivírus e clique em Retomar."
                .to_string(),
        )
    } else if tem(&["os error 112", "not enough space", "no space left"]) {
        (
            "Faltou espaço em disco durante o preparo.".to_string(),
            "Libere espaço e clique em Retomar.".to_string(),
        )
    } else if tem(&["os error 5)", "access is denied", "acesso negado"]) {
        (
            format!(
                "Um arquivo do preparo não pôde ser gravado ({o_que}): acesso negado, \
                 em geral um antivírus ou outro programa segurando a pasta."
            ),
            "Feche outras janelas do VoxVault e clique em Retomar.".to_string(),
        )
    } else {
        let ultima = saida
            .lines()
            .map(str::trim)
            .rfind(|l| !l.is_empty())
            .unwrap_or("sem detalhes");
        (
            format!("A etapa de {o_que} falhou: {ultima}"),
            "Clique em Retomar. Se a falha se repetir, os detalhes estão logo abaixo.".to_string(),
        )
    };
    FalhaDoPreparo {
        etapa: etapa.into(),
        mensagem,
        acao,
    }
}

fn agora_iso() -> String {
    let segundos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let (dias, resto) = (segundos.div_euclid(86_400), segundos.rem_euclid(86_400));
    // Civil date from days since 1970-01-01 (Howard Hinnant's algorithm).
    let z = dias + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let dia = doy - (153 * mp + 2) / 5 + 1;
    let mes = if mp < 10 { mp + 3 } else { mp - 9 };
    let ano = yoe + era * 400 + i64::from(mes <= 2);
    format!(
        "{ano:04}-{mes:02}-{dia:02}T{:02}:{:02}:{:02}Z",
        resto / 3600,
        (resto % 3600) / 60,
        resto % 60
    )
}

/// Run every step, in order. `pasta` is the data directory; `None` keeps the
/// effective one, as an automatic re-preparation does.
pub fn preparar(app: &AppHandle, pasta: Option<String>) -> Result<(), FalhaDoPreparo> {
    if EM_ANDAMENTO.swap(true, Ordering::SeqCst) {
        return Err(FalhaDoPreparo {
            etapa: "preparo".into(),
            mensagem: "Um preparo já está em andamento.".into(),
            acao: "Espere ele terminar.".into(),
        });
    }
    let _vez = Vez;

    let manifesto = ler_manifesto().ok_or_else(sem_recursos)?;
    let recursos = paths::recursos_dir().ok_or_else(sem_recursos)?;
    let nucleo = paths::core_root().ok_or_else(sem_recursos)?;
    let uv = recursos.join("uv").join("uv.exe");
    if !uv.is_file() {
        return Err(sem_recursos());
    }
    let runtime = paths::runtime_dir();
    std::fs::create_dir_all(&runtime).map_err(|err| FalhaDoPreparo {
        etapa: "preparo".into(),
        mensagem: format!("Não foi possível criar {}: {err}", runtime.display()),
        acao: "Verifique as permissões da sua pasta de usuário.".into(),
    })?;

    let carimbo_anterior = ler_carimbo(&runtime);
    let escolha = match &carimbo_anterior {
        Some(carimbo) => escolha_do_carimbo(carimbo),
        None => {
            let hardware = hardware::detectar();
            hardware::escolher(hardware.gpu.as_ref(), manifesto.limites())
        }
    };
    let pasta = pasta
        .map(|p| p.trim().to_string())
        .filter(|p| !p.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| paths::effective_data_dir().0);

    // interpretador
    if interpretador_presente(&runtime) {
        avisar(app, "interpretador", "pulada", "O interpretador já está instalado.");
    } else {
        avisar(app, "interpretador", "em_andamento", "Baixando o interpretador Python.");
        let mut comando = comando_uv(&uv, &runtime);
        comando.args(["python", "install", PYTHON, "--no-bin", "--no-registry"]);
        let (ok, saida) = executar(app, "interpretador", comando, None, |_| {})
            .map_err(|err| causa("interpretador", "o interpretador Python", &err))?;
        if !ok {
            return Err(causa("interpretador", "o interpretador Python", &saida));
        }
        avisar(app, "interpretador", "concluida", "");
    }

    // A service running from this environment holds its launcher and its
    // native libraries open, and the sync could not replace them. It is asked
    // to end the way any client asks -- it refuses while recording or with
    // work queued -- and the supervisor does not bring it back meanwhile.
    parar_o_servico_do_ambiente(app, &runtime)?;

    // dependencias e gpu
    let sincronizar = |etapa: &str, o_que: &str, extras: &[&str], exato: bool, total: u64| {
        avisar(app, etapa, "em_andamento", format!("Instalando {o_que}."));
        let montar = |offline: bool| {
            let mut comando = comando_uv(&uv, &runtime);
            comando.arg("sync").arg("--project").arg(&nucleo).args([
                "--frozen",
                "--no-dev",
                "--no-editable",
                "--python-preference",
                "only-managed",
                "--python",
                PYTHON,
            ]);
            for extra in extras {
                comando.args(["--extra", extra]);
            }
            if !exato {
                // Keeps what a later step adds: without it, the CUDA libraries
                // would be removed here only to be linked back one step later.
                comando.arg("--inexact");
            }
            if etapa == "dependencias" {
                // uv decides whether a local project changed by its
                // pyproject.toml, and the installer keeps every file's original
                // date: an update whose core changed but whose pyproject did not
                // would keep the old core in the environment. Rebuilding it is
                // pure Python, a few seconds.
                comando.args(["--reinstall-package", "voxvault-core"]);
            }
            if offline {
                comando.arg("--offline");
            }
            comando
        };
        // From the cache first. An update whose lock did not change has every
        // package there already -- the build backend included -- and uv would
        // still ask the index to confirm it, failing on a machine that is
        // offline for no reason. Only what the cache lacks goes to the network.
        let (ok, _) = executar(app, etapa, montar(true), Some((runtime.join("cache"), total)), |_| {})
            .map_err(|err| causa(etapa, o_que, &err))?;
        if !ok {
            let (ok, saida) = executar(app, etapa, montar(false), Some((runtime.join("cache"), total)), |_| {})
                .map_err(|err| causa(etapa, o_que, &err))?;
            if !ok {
                return Err(causa(etapa, o_que, &saida));
            }
        }
        avisar(app, etapa, "concluida", "");
        Ok(())
    };
    sincronizar(
        "dependencias",
        "as dependências do núcleo",
        &["engine", "mcp"],
        !escolha.etapa_gpu,
        manifesto.dependencias.bytes_em_disco,
    )?;
    if escolha.etapa_gpu {
        sincronizar(
            "gpu",
            "os componentes de aceleração por GPU",
            &["engine", "mcp", "cuda"],
            true,
            manifesto.gpu.bytes_em_disco,
        )?;
    } else {
        avisar(app, "gpu", "pulada", "Sem GPU que comporte um modelo: nada de CUDA.");
    }
    // What the environment no longer uses; what it uses stays, linked.
    let mut limpeza = comando_uv(&uv, &runtime);
    limpeza.args(["cache", "prune"]);
    let _ = executar(app, "dependencias", limpeza, None, |_| {});

    // configuracao
    let (atual, origem) = paths::effective_data_dir();
    if origem == DataDirSource::ConfigFile && atual == pasta {
        avisar(app, "configuracao", "pulada", "A pasta de dados já está configurada.");
    } else {
        system::writable(&pasta).map_err(|detalhe| FalhaDoPreparo {
            etapa: "configuracao".into(),
            mensagem: format!("A pasta de dados escolhida não serve: {detalhe}"),
            acao: "Escolha uma pasta em que o seu usuário possa escrever.".into(),
        })?;
        let mut comando = comando_do_nucleo(&runtime)?;
        comando.arg("config").arg(format!("data_dir={}", pasta.display()));
        let (ok, saida) = executar(app, "configuracao", comando, None, |_| {})
            .map_err(|err| causa("configuracao", "a configuração", &err))?;
        if !ok {
            return Err(causa("configuracao", "a configuração", &saida));
        }
        avisar(app, "configuracao", "concluida", pasta.display().to_string());
    }

    // modelo
    let repositorio = manifesto
        .modelos
        .get(&escolha.modelo)
        .map(|m| m.repositorio.clone())
        .unwrap_or_default();
    if !repositorio.is_empty() && modelo_presente(&pasta.join("models"), &repositorio) {
        avisar(app, "modelo", "pulada", format!("O modelo {} já está na pasta de dados.", escolha.modelo));
    } else {
        baixar_modelo(app, "modelo", &runtime, &escolha.modelo)?;
    }

    // verificacao
    avisar(app, "verificacao", "em_andamento", "Conferindo o ambiente.");
    verificar(app, &runtime)?;
    gravar_carimbo(
        &runtime,
        &Carimbo {
            versao_app: VERSAO_DO_APP.to_string(),
            sha256_lock: manifesto.sha256_lock.clone(),
            gpu: escolha.etapa_gpu,
            modelo: escolha.modelo.clone(),
            concluido_em: agora_iso(),
        },
    )
    .map_err(|err| FalhaDoPreparo {
        etapa: "verificacao".into(),
        mensagem: format!("O ambiente está pronto, mas o registro do preparo não foi gravado: {err}"),
        acao: "Clique em Retomar.".into(),
    })?;
    // `voxvault` in the terminal: a convenience, never a reason to fail.
    if let Err(erro) = crate::caminho::integrar(&runtime.join("ambiente").join("Scripts")) {
        eprintln!("voxvault fora do PATH: {erro}");
    }
    avisar(app, "verificacao", "concluida", "");
    Ok(())
}

/// `voxvault models download --json`, its lines turned into progress. Shared
/// by the preparation and by a model change in the settings.
pub fn baixar_modelo(app: &AppHandle, etapa: &str, runtime: &Path, modelo: &str) -> Result<(), FalhaDoPreparo> {
    avisar(app, etapa, "em_andamento", format!("Baixando o modelo {modelo}."));
    let mut comando = comando_do_nucleo(runtime)?;
    comando.args(["models", "download", "--model", modelo, "--json"]);
    let mut erro: Option<String> = None;
    let mut concluido = false;
    let app_linhas = app.clone();
    let etapa_linhas = etapa.to_string();
    let (ok, saida) = executar(app, etapa, comando, None, |linha| {
        let Ok(valor) = serde_json::from_str::<serde_json::Value>(linha) else { return };
        if let (Some(feito), Some(total)) = (valor["baixado"].as_u64(), valor["total"].as_u64()) {
            let _ = app_linhas.emit(
                EVENTO_ETAPA,
                Progresso {
                    etapa: etapa_linhas.clone(),
                    estado: "em_andamento".into(),
                    detalhe: String::new(),
                    baixado: Some(feito),
                    total: Some(total),
                },
            );
        } else if let Some(texto) = valor["erro"].as_str() {
            erro = Some(texto.to_string());
        } else if valor["concluido"].as_bool() == Some(true) {
            concluido = true;
        }
    })
    .map_err(|err| causa(etapa, &format!("o modelo {modelo}"), &err))?;
    if !ok || !concluido {
        let detalhe = erro.unwrap_or(saida);
        return Err(causa(etapa, &format!("o modelo {modelo}"), &detalhe));
    }
    avisar(app, etapa, "concluida", "");
    Ok(())
}

fn parar_o_servico_do_ambiente(app: &AppHandle, runtime: &Path) -> Result<(), FalhaDoPreparo> {
    let exe = runtime.join("ambiente").join("Scripts").join("voxvault.exe");
    if !exe.is_file() {
        return Ok(());
    }
    let mut comando = Command::new(&exe);
    comando
        .args(["serve", "--stop"])
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        // The environment being replaced may carry an older core, one that
        // still sent its loopback requests to the machine's proxy.
        .env("NO_PROXY", paths::sem_proxy_no_loopback())
        .env("no_proxy", paths::sem_proxy_no_loopback())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        comando.creation_flags(CREATE_NO_WINDOW);
    }
    let (ok, saida) = executar(app, "dependencias", comando, None, |_| {})
        .map_err(|err| causa("dependencias", "o serviço local", &err))?;
    if !ok {
        let detalhe = saida.lines().next().unwrap_or("").trim().to_string();
        // The service refusing is the only answer that points at --force: it
        // is protecting a recording or a queue. Anything else is a stop that
        // did not happen, and saying "it is recording" would be a lie.
        return Err(if saida.contains("--force") {
            FalhaDoPreparo {
                etapa: "dependencias".into(),
                mensagem: format!(
                    "O serviço local está gravando ou transcrevendo, e o ambiente só é \
                     atualizado com ele parado. {detalhe}"
                ),
                acao: "Encerre a gravação pela bandeja ou espere a fila esvaziar, e clique em Retomar."
                    .into(),
            }
        } else {
            FalhaDoPreparo {
                etapa: "dependencias".into(),
                mensagem: format!(
                    "O serviço local não pôde ser parado para atualizar o ambiente: {detalhe}"
                ),
                acao: "Clique em Retomar. Se a falha se repetir, reinicie o computador e abra o VoxVault."
                    .into(),
            }
        });
    }
    // It answers before it is gone; the files are free only once it is.
    let prazo = std::time::Instant::now() + Duration::from_secs(20);
    while std::time::Instant::now() < prazo {
        let vivo = std::fs::read_to_string(paths::rendezvous_file())
            .ok()
            .and_then(|texto| serde_json::from_str::<serde_json::Value>(&texto).ok())
            .and_then(|valor| valor["pid"].as_u64())
            .is_some_and(|pid| system::process_is_alive(pid as u32));
        if !vivo {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    Ok(())
}

fn verificar(app: &AppHandle, runtime: &Path) -> Result<(), FalhaDoPreparo> {
    let mut comando = comando_do_nucleo(runtime)?;
    comando.args(["doctor", "--json"]);
    let mut filho = comando.spawn().map_err(|err| FalhaDoPreparo {
        etapa: "verificacao".into(),
        mensagem: format!("O núcleo não pôde ser executado: {err}"),
        acao: "Clique em Retomar.".into(),
    })?;
    let mut texto = String::new();
    if let Some(mut stdout) = filho.stdout.take() {
        let _ = stdout.read_to_string(&mut texto);
    }
    let _ = filho.wait();
    let _ = app.emit(EVENTO_LINHA, serde_json::json!({ "etapa": "verificacao", "linha": "doctor --json" }));
    let relatorio: serde_json::Value = serde_json::from_str(&texto).map_err(|_| FalhaDoPreparo {
        etapa: "verificacao".into(),
        mensagem: "O diagnóstico do núcleo não respondeu: o ambiente não está utilizável.".into(),
        acao: "Clique em Retomar.".into(),
    })?;
    let falhas: Vec<String> = relatorio["itens"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|item| {
            item["estado"] == "falha"
                && ITENS_ESSENCIAIS.contains(&item["chave"].as_str().unwrap_or(""))
        })
        .map(|item| {
            format!(
                "{}: {}",
                item["rotulo"].as_str().unwrap_or("item"),
                item["detalhe"].as_str().unwrap_or("")
            )
        })
        .collect();
    if falhas.is_empty() {
        return Ok(());
    }
    Err(FalhaDoPreparo {
        etapa: "verificacao".into(),
        mensagem: format!("O ambiente foi montado, mas a verificação falhou — {}", falhas.join(" | ")),
        acao: "Clique em Retomar. Se a falha se repetir, abra o diagnóstico nas Configurações.".into(),
    })
}

#[cfg(test)]
mod testes {
    use super::*;

    fn manifesto() -> Manifesto {
        serde_json::from_str(include_str!("../tests/fixtures/preparo.json")).unwrap()
    }

    fn carimbo(versao: &str, lock: &str) -> Carimbo {
        Carimbo {
            versao_app: versao.into(),
            sha256_lock: lock.into(),
            gpu: true,
            modelo: "large-v3".into(),
            concluido_em: "2026-09-23T12:00:00Z".into(),
        }
    }

    fn estado<'a>(m: Option<&'a Manifesto>, c: Option<&'a Carimbo>) -> Estado<'a> {
        Estado {
            manifesto: m,
            carimbo: c,
            executavel_preparado: true,
            desenvolvimento: false,
            runtime_existe: true,
            versao: "0.1.0",
        }
    }

    #[test]
    fn carimbo_igual_a_versao_e_ao_lock_e_pronto() {
        let m = manifesto();
        let c = carimbo("0.1.0", &m.sha256_lock);
        assert_eq!(avaliar(estado(Some(&m), Some(&c))), Situacao::Pronto);
    }

    #[test]
    fn outro_lock_prepara_de_novo_sem_perguntar() {
        let m = manifesto();
        let c = carimbo("0.1.0", "outro-lock");
        assert!(matches!(
            avaliar(estado(Some(&m), Some(&c))),
            Situacao::Desatualizado { motivo } if motivo.contains("dependências")
        ));
    }

    #[test]
    fn outra_versao_prepara_de_novo_sem_perguntar() {
        let m = manifesto();
        let c = carimbo("0.0.9", &m.sha256_lock);
        assert!(matches!(
            avaliar(estado(Some(&m), Some(&c))),
            Situacao::Desatualizado { motivo } if motivo.contains("0.0.9")
        ));
    }

    #[test]
    fn um_ambiente_apagado_depois_do_carimbo_e_refeito() {
        let m = manifesto();
        let c = carimbo("0.1.0", &m.sha256_lock);
        let mut e = estado(Some(&m), Some(&c));
        e.executavel_preparado = false;
        assert!(matches!(avaliar(e), Situacao::Desatualizado { .. }));
    }

    #[test]
    fn sem_carimbo_e_o_primeiro_preparo_com_ou_sem_retomada() {
        let m = manifesto();
        let mut e = estado(Some(&m), None);
        e.executavel_preparado = false;
        e.runtime_existe = false;
        assert_eq!(avaliar(e), Situacao::Primeiro { retomada: false });

        let mut e = estado(Some(&m), None);
        e.runtime_existe = true;
        assert_eq!(avaliar(e), Situacao::Primeiro { retomada: true });
    }

    #[test]
    fn a_depuracao_no_ambiente_do_checkout_nao_prepara_nada() {
        let mut e = estado(None, None);
        e.executavel_preparado = false;
        e.desenvolvimento = true;
        assert_eq!(avaliar(e), Situacao::Desenvolvimento);
    }

    #[test]
    fn instalado_sem_recursos_diz_que_a_instalacao_esta_incompleta() {
        let mut e = estado(None, None);
        e.executavel_preparado = false;
        assert_eq!(avaliar(e), Situacao::SemRecursos);
    }

    fn escolha(gpu: bool) -> Escolha {
        Escolha {
            modelo: if gpu { "large-v3" } else { "large-v3-turbo" }.into(),
            dispositivo: if gpu { "cuda" } else { "cpu" }.into(),
            etapa_gpu: gpu,
        }
    }

    fn plano(
        gpu: bool,
        runtime: &str,
        pasta: &str,
        livre: u64,
        modelo_presente: bool,
    ) -> (Vec<ItemDoPlano>, Vec<Volume>, Option<String>) {
        let m = manifesto();
        let e = escolha(gpu);
        itens_e_volumes(
            Entrada {
                manifesto: &m,
                escolha: &e,
                runtime: Path::new(runtime),
                pasta: Path::new(pasta),
                interpretador_presente: false,
                modelo_presente,
            },
            |_| Some(livre),
        )
    }

    #[test]
    fn sem_gpu_nada_de_cuda_e_o_modelo_da_cpu() {
        let (itens, _, _) = plano(false, r"C:\Users\x\.voxvault\runtime", r"C:\Users\x\VoxVault", u64::MAX, false);
        assert!(itens.iter().all(|i| i.etapa != "gpu"));
        let modelo = itens.iter().find(|i| i.etapa == "modelo").unwrap();
        assert_eq!(modelo.rotulo, "Modelo de transcrição large-v3-turbo");
        assert_eq!(modelo.bytes, 1_621_667_008);
    }

    #[test]
    fn com_gpu_os_componentes_de_cuda_entram_com_o_tamanho_do_lock() {
        let (itens, _, _) = plano(true, r"C:\p\.voxvault\runtime", r"D:\VoxVault", u64::MAX, false);
        let gpu = itens.iter().find(|i| i.etapa == "gpu").unwrap();
        assert_eq!(gpu.bytes, 1_376_036_682);
        assert_eq!(gpu.volume, "C:");
        assert_eq!(itens.iter().find(|i| i.etapa == "modelo").unwrap().volume, "D:");
    }

    #[test]
    fn cada_volume_pede_o_que_vai_nele() {
        let (_, volumes, recusa) = plano(true, r"C:\p\.voxvault\runtime", r"D:\VoxVault", u64::MAX, false);
        assert_eq!(recusa, None);
        let c = volumes.iter().find(|v| v.raiz == "C:").unwrap();
        let d = volumes.iter().find(|v| v.raiz == "D:").unwrap();
        // Interpreter, dependencies and CUDA once installed, plus a fifth.
        let ambiente = 63_321_361u64 + 288_575_601 + 2_105_336_123;
        assert_eq!(c.necessario, (ambiente as f64 * 1.2) as u64);
        // The model and a gigabyte for the first recordings.
        assert_eq!(d.necessario, 3_090_836_727 + 1_000_000_000);
    }

    #[test]
    fn um_volume_so_soma_as_duas_exigencias() {
        let (_, volumes, _) = plano(false, r"C:\p\.voxvault\runtime", r"C:\p\VoxVault", u64::MAX, false);
        assert_eq!(volumes.len(), 1);
    }

    #[test]
    fn sem_espaco_a_recusa_diz_quanto_falta_e_onde() {
        let (_, volumes, recusa) = plano(false, r"C:\p\.voxvault\runtime", r"D:\VoxVault", 1_000_000_000, false);
        let recusa = recusa.expect("tinha de recusar");
        assert!(recusa.contains("volume D:"), "{recusa}");
        assert!(recusa.contains("faltam"), "{recusa}");
        let d = volumes.iter().find(|v| v.raiz == "D:").unwrap();
        assert_eq!(d.falta, 1_621_667_008);
    }

    #[test]
    fn um_modelo_ja_presente_nao_pede_espaco_nem_download() {
        let (itens, volumes, _) = plano(false, r"C:\p\.voxvault\runtime", r"D:\VoxVault", u64::MAX, true);
        assert!(itens.iter().find(|i| i.etapa == "modelo").unwrap().presente);
        assert_eq!(volumes.iter().find(|v| v.raiz == "D:").unwrap().necessario, 1_000_000_000);
    }

    #[test]
    fn erros_de_rede_do_uv_viram_falta_de_conexao() {
        let saida = "error: Failed to fetch: `https://pypi.org/simple/numpy/`\n  \
                     Caused by: Request failed after 3 retries\n  \
                     Caused by: error sending request for url\n  \
                     Caused by: dns error: No such host is known. (os error 11001)";
        let falha = causa("dependencias", "as dependências do núcleo", saida);
        assert_eq!(
            falha.mensagem,
            "Sem conexão com a internet: o preparo precisa baixar as dependências do núcleo."
        );
        assert!(falha.acao.contains("Retomar"));
    }

    #[test]
    fn um_bloqueio_de_antivirus_e_dito_como_tal() {
        let saida = "error: Failed to install: onnxruntime\n  Caused by: Operation did not \
                     complete successfully because the file contains a virus or potentially \
                     unwanted software. (os error 225)";
        let falha = causa("dependencias", "as dependências do núcleo", saida);
        assert!(falha.mensagem.contains("antivírus"), "{}", falha.mensagem);
    }

    #[test]
    fn outras_falhas_trazem_a_ultima_linha() {
        let falha = causa("gpu", "a GPU", "Resolved 53 packages\nerror: something odd\n");
        assert_eq!(falha.mensagem, "A etapa de a GPU falhou: error: something odd");
    }

    #[test]
    fn o_modelo_so_esta_presente_com_todos_os_arquivos() {
        let base = std::env::temp_dir().join(format!("voxvault-preparo-{}", std::process::id()));
        let repo = base.join("models--Systran--faster-whisper-tiny");
        let snapshot = repo.join("snapshots").join("abc");
        std::fs::create_dir_all(&snapshot).unwrap();
        std::fs::create_dir_all(repo.join("refs")).unwrap();
        std::fs::write(repo.join("refs").join("main"), "abc").unwrap();
        for nome in ["config.json", "tokenizer.json", "vocabulary.txt"] {
            std::fs::write(snapshot.join(nome), b"{}").unwrap();
        }
        assert!(!modelo_presente(&base, "Systran/faster-whisper-tiny"), "sem model.bin");

        std::fs::write(snapshot.join("model.bin"), b"pesos").unwrap();
        assert!(modelo_presente(&base, "Systran/faster-whisper-tiny"));
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn a_raiz_do_volume() {
        assert_eq!(raiz_do_volume(Path::new(r"C:\Users\x\VoxVault")), "C:");
        assert_eq!(raiz_do_volume(Path::new(r"d:\dados")), "D:");
    }

    #[test]
    fn a_data_do_carimbo_e_iso() {
        let data = agora_iso();
        assert_eq!(data.len(), 20, "{data}");
        assert!(data.starts_with("20") && data.ends_with('Z'));
    }
}
