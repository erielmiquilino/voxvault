//! Load generator that stands in for the resident service during a cost
//! measurement. Inert unless `VOXVAULT_MEDICAO=1`.
//!
//! The ceiling in the specification is about a 60-minute recording, and during
//! a recording the only continuous work this application does is render two
//! level meters and a clock. Until the core exposes its resident service, that
//! load cannot be produced by recording anything, so it is produced here
//! instead -- at exactly the rate the requirement caps it at, 20 updates per
//! second per track, which is the worst case the interface is permitted to
//! face.
//!
//! This is deliberately not a mock of the recording. It emits the same events
//! the service will emit, over the same channel, into the same unmodified
//! interface code. What it cannot reproduce is the cost of the capture itself,
//! which belongs to the service and not to this process. A measurement taken
//! with it therefore covers the application's share of the budget and must be
//! reported as such, not as the whole figure.

use std::time::{Duration, Instant};

use tauri::{AppHandle, Emitter};

const VARIAVEL: &str = "VOXVAULT_MEDICAO";
/// The cap the requirement sets, per track.
const ATUALIZACOES_POR_SEGUNDO: u64 = 20;

pub fn iniciar(app: AppHandle) {
    if std::env::var(VARIAVEL).as_deref() != Ok("1") {
        return;
    }

    std::thread::Builder::new()
        .name("voxvault-medicao".into())
        .spawn(move || {
            let periodo = Duration::from_millis(1000 / ATUALIZACOES_POR_SEGUNDO);
            let inicio = Instant::now();

            let _ = app.emit(
                "gravacao://estado",
                serde_json::json!({ "estado": "gravando", "decorrido_ms": 0 }),
            );

            let mut proximo_aviso = Duration::from_secs(300);
            let mut proxima_trilha = Duration::ZERO;
            loop {
                let decorrido = inicio.elapsed();
                let t = decorrido.as_secs_f32();

                // Re-asserted once a second. The real service's polled view is
                // the authority on whether a track is capturing, and it will
                // overwrite this; without the re-assert the meters would settle
                // at a constant and stop exercising the path being measured.
                if decorrido >= proxima_trilha {
                    for trilha in ["mic", "system"] {
                        let _ = app.emit(
                            "gravacao://trilha",
                            serde_json::json!({ "trilha": trilha, "capturando": true }),
                        );
                    }
                    proxima_trilha += Duration::from_secs(1);
                }

                // Two independent shapes so the meters do not move in lockstep,
                // which would let a renderer coalesce them into one update.
                for (trilha, freq, fase) in [("mic", 0.9f32, 0.0f32), ("system", 1.3f32, 2.1f32)] {
                    let nivel = ((t * freq + fase).sin() * 0.45 + 0.5).clamp(0.0, 1.0);
                    let _ = app.emit(
                        "gravacao://nivel",
                        serde_json::json!({ "trilha": trilha, "nivel": nivel }),
                    );
                }

                if decorrido >= proximo_aviso {
                    let _ = app.emit(
                        "gravacao://aviso",
                        serde_json::json!({
                            "texto": "Medição: aviso sintético, para exercitar a \
                                      apresentação de avisos durante a gravação.",
                            "instante_ms": decorrido.as_millis() as u64,
                        }),
                    );
                    proximo_aviso += Duration::from_secs(300);
                }

                std::thread::sleep(periodo);
            }
        })
        .expect("não foi possível iniciar o gerador de carga de medição");
}
