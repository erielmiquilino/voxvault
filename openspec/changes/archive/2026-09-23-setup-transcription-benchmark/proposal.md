## Why

Todo o VoxVault depende de uma única aposta ainda não verificada: a de que o Whisper rodando local nesta máquina transcreve reunião em pt-BR com qualidade suficiente para ser útil. Se essa aposta falhar, a saída é trocar por um provedor online — e essa troca precisa custar uma classe nova, não um redesenho do produto.

Esta fase existe para produzir essa resposta antes que qualquer código de captura, armazenamento, MCP ou UI seja escrito. É um portão de decisão, não uma entrega de produto.

## What Changes

- Estabelece o pacote Python `voxvault-core` com ambiente reproduzível em Python 3.12 gerenciado por `uv`.
- Adiciona um comando de diagnóstico que verifica, de forma legível, se GPU, CUDA, cuDNN, cuBLAS e ffmpeg estão utilizáveis — o ponto onde este stack mais falha no Windows.
- Define o contrato `TranscriptionEngine` como fronteira estável do sistema, com uma implementação local baseada em faster-whisper. Toda a arquitetura posterior depende desse contrato, nunca da implementação.
- Adiciona um harness de benchmark que transcreve o mesmo áudio com vários modelos e emite um relatório comparativo lado a lado, com tempo de processamento, pico de VRAM e os textos alinhados para leitura humana.

## Capabilities

### New Capabilities

- `environment-check`: verificação de pré-requisitos de execução (GPU, CUDA/cuDNN, ffmpeg, versão de Python) com diagnóstico acionável quando algo falta.
- `transcription-engine`: contrato de transcrição que recebe um arquivo de áudio e devolve segmentos com timestamps, mais a implementação local sobre faster-whisper.
- `benchmark-harness`: execução comparativa de múltiplos motores/modelos sobre a mesma entrada, com relatório de qualidade e desempenho.

### Modified Capabilities

Nenhuma. Projeto novo, sem specs existentes.

## Impact

- **Código novo**: pacote `voxvault-core` (Python 3.12), sem nenhum código pré-existente afetado.
- **Dependências novas**: `faster-whisper`, `ctranslate2`, `nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, `soxr`, `numpy`, `typer`, `rich`. Externo ao Python: `ffmpeg`.
- **Disco**: o cache de modelos (~3 GB por modelo) e todo dado gerado ficam em `D:\VoxVault\`. O drive `E:\`, onde vive o código, tem apenas 5,7 GB livres e não comporta os modelos.
- **Decisão que esta fase desbloqueia**: manter a transcrição local ou passar `TranscriptionEngine` para um provedor online. As fases seguintes são escritas para serem indiferentes a esse resultado.
