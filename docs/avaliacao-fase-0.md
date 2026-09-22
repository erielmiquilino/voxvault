# Avaliação da Fase 0 — o Whisper local serve?

A Fase 0 existe para responder uma pergunta, e só uma: **transcrever localmente
nesta máquina dá qualidade suficiente em pt-BR, ou precisamos de um provedor
online?** Este documento reúne a evidência medida. A decisão é do usuário.

## Ambiente medido

| item | valor |
|---|---|
| GPU | NVIDIA GeForce RTX 4060 Ti, 8188 MB |
| Driver | 595.71 |
| Python | 3.12.12 (venv gerenciada por uv) |
| Motor | faster-whisper 1.2.1 sobre CTranslate2 4.8.2 |
| Decodificador | ffmpeg 9.0.2 |

## Desempenho

Medido com `voxvault bench`, cada configuração em processo próprio para que a
memória de GPU seja liberada entre elas.

| modelo | carregamento | decodificação | fator tempo real | pico de GPU |
|---|---|---|---|---|
| `large-v3` | 12,4 s | 2,9 s | **22,1x** | 4221 MB |
| `large-v3-turbo` | 7,0 s | 1,3 s | **50,5x** | 2269 MB |
| `medium` | 6,6 s | 4,6 s | 13,3x | — |

O carregamento é custo fixo, pago uma vez pelo serviço residente. O fator de
tempo real usa só a decodificação, que é o que cresce com a duração da reunião.

**Consequência prática:** uma reunião de uma hora é transcrita pela `large-v3`
em **menos de três minutos**. O modelo cabe com folga nos 8 GB da placa.

## Qualidade

Referência conhecida: cinco frases de reunião em pt-BR, com jargão técnico e
estrangeirismos, sintetizadas pelas vozes pt-BR do Windows, separadas por
silêncios de seis segundos.

### `large-v3` — áudio limpo

Cinco de cinco frases corretas, **palavra por palavra**, com acentuação e
pontuação. Jargão preservado: *Postgres*, *Kubernetes*, *pull request*,
*sprint*, *deploy*. Normalizou "quinze" para "15", o que é desejável.

### `large-v3` — áudio degradado

Três degradações, aplicadas sobre a mesma referência:

| condição | resultado |
|---|---|
| Ruído rosa somado (SNR ~15 dB) | 5/5 corretas |
| Banda telefônica (300–3400 Hz) + compressão | 5/5 corretas |
| Ruído **e** banda estreita juntos | 5/5 corretas |

### Alucinação em silêncio — o risco principal

Whisper preenche silêncio com texto aprendido de legendas, e em pt-BR o
artefato clássico é uma linha de créditos de comunidade de legendagem. Texto
inventado é indistinguível de fala real para quem lê a transcrição depois.

Com `vad_filter` ligado e `condition_on_previous_text` desligado, **nenhum dos
silêncios de seis segundos produziu segmento** em nenhuma das execuções.

### Modelos menores

- `large-v3-turbo`: conteúdo correto, mas perde pontuação final e funde
  segmentos. Aceitável para leitura, pior para navegação por instante.
- `medium`: **erra e alucina**. Transcreveu "da sprint" como "das print" e
  inventou um "E aí" ao final, que não existe no áudio. Descartado.

## Veredito

Para o uso que motivou o projeto — guardar o que foi dito numa reunião de
trabalho em pt-BR — a transcrição local **atende**, e com margem confortável de
desempenho. Não há motivo para buscar provedor online.

O contrato `TranscriptionEngine` mantém essa decisão reversível: trocar por um
provedor remoto é escrever uma classe nova, sem tocar em nenhum consumidor.

## O que esta evidência não cobre

Isto precisa ser dito, porque a medição acima é boa demais para ser a história
inteira. O áudio de referência é **sintetizado**, e fala sintetizada é mais
limpa e mais uniforme do que fala humana: não tem hesitação, não tem gente
falando por cima, não tem sotaque, não tem alguém longe do microfone.

As degradações testaram robustez **de canal** — ruído e banda —, não robustez
a **falante**. O veredito definitivo vem da primeira reunião real gravada pela
própria ferramenta, que é exatamente o que a Fase 1 passa a produzir. A
bancada continua disponível para reavaliar quando esse material existir.

## Como reproduzir

```bash
pwsh voxvault-core/tools/gerar-audio-referencia.ps1
voxvault bench "D:\VoxVault\bench\ref-mic.wav" --models "large-v3,large-v3-turbo,medium"
```
