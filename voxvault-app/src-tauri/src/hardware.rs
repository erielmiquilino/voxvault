//! What the machine has, asked before any Python exists.
//!
//! The preparation has to know whether to download two gigabytes of CUDA
//! libraries, and which model, before the core it would ask is installed. So
//! the app asks the driver itself, the way the core does at run time --
//! `nvidia-smi`, first GPU, total memory -- and picks the model with the
//! thresholds `preparo.json` carries, generated from the core's own matrix.
//! One source for the numbers, two places that apply them, and the same answer.

use std::io::Read;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use serde::Serialize;

/// Diagnostic switch: behave as a machine without a GPU, so the CPU path can
/// be exercised end to end on a machine that does have one. The core reads it
/// too, so the environment prepared and the transcription agree.
pub const FORCAR_CPU: &str = "VOXVAULT_FORCAR_CPU";

/// Past this, the driver is not answering and the machine counts as having no
/// usable GPU: a preparation must not hang on a wedged driver.
pub const PRAZO: Duration = Duration::from_secs(10);

pub const MODELO_GPU: &str = "large-v3";
pub const MODELO_GPU_PEQUENA: &str = "large-v3-turbo";
pub const MODELO_CPU: &str = "large-v3-turbo";

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Gpu {
    pub nome: String,
    pub memoria_mb: u32,
}

#[derive(Debug, Clone, Serialize)]
pub struct Hardware {
    pub gpu: Option<Gpu>,
    pub cpu_forcada: bool,
    /// What was found, in words for the preparation screen.
    pub detalhe: String,
}

/// The model the defaults pick here, where it runs, and whether the GPU
/// components are part of the preparation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Escolha {
    pub modelo: String,
    pub dispositivo: String,
    pub etapa_gpu: bool,
}

/// Memory each default model needs, margin included: `preparo.json`'s
/// `vram_minima_mb`.
#[derive(Debug, Clone, Copy)]
pub struct Limites {
    pub large_v3_mb: u32,
    pub turbo_mb: u32,
}

/// The first GPU of `nvidia-smi --query-gpu=name,memory.total
/// --format=csv,noheader,nounits`, the same one the core looks at.
pub fn analisar(saida: &str) -> Option<Gpu> {
    let linha = saida.lines().map(str::trim).find(|linha| !linha.is_empty())?;
    let (nome, memoria) = linha.rsplit_once(',')?;
    let nome = nome.trim();
    let memoria = memoria.trim().parse::<f64>().ok()?;
    if nome.is_empty() || !memoria.is_finite() || memoria <= 0.0 {
        return None;
    }
    Some(Gpu {
        nome: nome.to_string(),
        memoria_mb: memoria as u32,
    })
}

pub fn escolher(gpu: Option<&Gpu>, limites: Limites) -> Escolha {
    let memoria = gpu.map(|g| g.memoria_mb).unwrap_or(0);
    let (modelo, dispositivo) = if memoria >= limites.large_v3_mb {
        (MODELO_GPU, "cuda")
    } else if memoria >= limites.turbo_mb {
        (MODELO_GPU_PEQUENA, "cuda")
    } else {
        (MODELO_CPU, "cpu")
    };
    Escolha {
        modelo: modelo.to_string(),
        dispositivo: dispositivo.to_string(),
        etapa_gpu: dispositivo == "cuda",
    }
}

fn cpu_forcada() -> bool {
    std::env::var(FORCAR_CPU).map(|v| v.trim() == "1").unwrap_or(false)
}

pub fn detectar() -> Hardware {
    if cpu_forcada() {
        return Hardware {
            gpu: None,
            cpu_forcada: true,
            detalhe: format!(
                "Chave de diagnóstico {FORCAR_CPU}=1 ligada: a máquina é tratada \
                 como se não tivesse GPU."
            ),
        };
    }
    match consultar_driver() {
        Ok(saida) => match analisar(&saida) {
            Some(gpu) => Hardware {
                detalhe: format!("{} com {} MB de memória.", gpu.nome, gpu.memoria_mb),
                gpu: Some(gpu),
                cpu_forcada: false,
            },
            None => Hardware {
                gpu: None,
                cpu_forcada: false,
                detalhe: "O driver da NVIDIA respondeu sem nenhuma GPU utilizável.".to_string(),
            },
        },
        Err(motivo) => Hardware {
            gpu: None,
            cpu_forcada: false,
            detalhe: motivo,
        },
    }
}

/// Runs `nvidia-smi` without a window and gives up after [`PRAZO`].
fn consultar_driver() -> Result<String, String> {
    let mut comando = Command::new("nvidia-smi");
    comando
        .args(["--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        comando.creation_flags(0x0800_0000);
    }
    let mut filho = comando
        .spawn()
        .map_err(|_| "Nenhuma GPU NVIDIA encontrada (o nvidia-smi não existe aqui).".to_string())?;
    let inicio = Instant::now();
    loop {
        match filho.try_wait() {
            Ok(Some(status)) => {
                let mut saida = String::new();
                if let Some(mut stdout) = filho.stdout.take() {
                    let _ = stdout.read_to_string(&mut saida);
                }
                return if status.success() {
                    Ok(saida)
                } else {
                    Err("O driver da NVIDIA não respondeu à consulta de GPU.".to_string())
                };
            }
            Ok(None) if inicio.elapsed() < PRAZO => std::thread::sleep(Duration::from_millis(50)),
            _ => {
                let _ = filho.kill();
                let _ = filho.wait();
                return Err(format!(
                    "O driver da NVIDIA não respondeu em {} s; a preparação segue como \
                     numa máquina sem GPU.",
                    PRAZO.as_secs()
                ));
            }
        }
    }
}

#[cfg(test)]
mod testes {
    use super::*;

    const LIMITES: Limites = Limites {
        large_v3_mb: 5600,
        turbo_mb: 2500,
    };

    fn gpu(memoria_mb: u32) -> Gpu {
        Gpu {
            nome: "GPU".to_string(),
            memoria_mb,
        }
    }

    #[test]
    fn le_a_saida_real_desta_maquina() {
        let saida = "NVIDIA GeForce RTX 4060 Ti, 8188\r\n";
        assert_eq!(
            analisar(saida),
            Some(Gpu {
                nome: "NVIDIA GeForce RTX 4060 Ti".to_string(),
                memoria_mb: 8188
            })
        );
    }

    #[test]
    fn com_duas_gpus_vale_a_primeira_como_no_nucleo() {
        let saida = "NVIDIA GeForce GTX 1650, 4096\r\nNVIDIA RTX A6000, 49140\r\n";
        assert_eq!(analisar(saida).unwrap().memoria_mb, 4096);
    }

    #[test]
    fn saidas_sem_gpu_nao_viram_gpu() {
        for saida in [
            "",
            "\r\n",
            "No devices were found\r\n",
            "NVIDIA GeForce RTX 3060, [N/A]\r\n",
            "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.",
            ", 8192",
        ] {
            assert_eq!(analisar(saida), None, "{saida:?}");
        }
    }

    #[test]
    fn um_nome_com_virgula_continua_inteiro() {
        let gpu = analisar("NVIDIA A100-SXM4, 80GB edition, 81920").unwrap();
        assert_eq!(gpu.nome, "NVIDIA A100-SXM4, 80GB edition");
        assert_eq!(gpu.memoria_mb, 81920);
    }

    #[test]
    fn a_tabela_da_spec() {
        // 8 GB: the best model, on the GPU.
        assert_eq!(
            escolher(Some(&gpu(8188)), LIMITES),
            Escolha { modelo: "large-v3".into(), dispositivo: "cuda".into(), etapa_gpu: true }
        );
        // 4 GB: the turbo still fits on the GPU.
        assert_eq!(
            escolher(Some(&gpu(4096)), LIMITES),
            Escolha { modelo: "large-v3-turbo".into(), dispositivo: "cuda".into(), etapa_gpu: true }
        );
        // 2 GB, or none at all: the turbo on the CPU, and no CUDA download.
        let cpu = Escolha {
            modelo: "large-v3-turbo".into(),
            dispositivo: "cpu".into(),
            etapa_gpu: false,
        };
        assert_eq!(escolher(Some(&gpu(2048)), LIMITES), cpu);
        assert_eq!(escolher(None, LIMITES), cpu);
    }

    #[test]
    fn os_limites_sao_inclusivos() {
        assert_eq!(escolher(Some(&gpu(5600)), LIMITES).modelo, "large-v3");
        assert_eq!(escolher(Some(&gpu(5599)), LIMITES).modelo, "large-v3-turbo");
        assert!(escolher(Some(&gpu(2500)), LIMITES).etapa_gpu);
        assert!(!escolher(Some(&gpu(2499)), LIMITES).etapa_gpu);
    }

    #[test]
    fn a_chave_de_diagnostico_escolhe_a_cpu() {
        // Set only inside this test binary's own environment.
        std::env::set_var(FORCAR_CPU, "1");
        let hardware = detectar();
        std::env::remove_var(FORCAR_CPU);

        assert!(hardware.cpu_forcada);
        assert_eq!(hardware.gpu, None);
        assert_eq!(escolher(hardware.gpu.as_ref(), LIMITES).dispositivo, "cpu");
    }
}
