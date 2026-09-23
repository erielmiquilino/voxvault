# Componentes de terceiros

O VoxVault é distribuído sob a licença MIT (veja `LICENSE`). Ele traz ou baixa os
componentes abaixo, cada um sob a licença do próprio autor.

## Embutido no instalador

| Componente | Uso | Licença |
|---|---|---|
| [uv](https://github.com/astral-sh/uv) (Astral) | prepara o ambiente de execução do núcleo no primeiro uso | MIT ou Apache-2.0, à escolha |
| [Tauri](https://tauri.app) e seus plugins | janela, bandeja, atalho global, diálogos e início com o Windows | MIT ou Apache-2.0, à escolha |
| [tauri-winrt-notification](https://github.com/tauri-apps/winrt-notification) | notificações do Windows com botão de ação | MIT ou Apache-2.0, à escolha |
| [Svelte](https://svelte.dev) | interface | MIT |

## Baixado no primeiro uso

| Componente | Uso | Licença |
|---|---|---|
| [CPython](https://www.python.org) por [python-build-standalone](https://github.com/astral-sh/python-build-standalone) | interpretador próprio do núcleo | Python Software Foundation License |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (SYSTRAN) | motor de transcrição | MIT |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) (OpenNMT) | execução do modelo | MIT |
| Modelos Whisper convertidos (`Systran/faster-whisper-large-v3`, `mobiuslabsgmbh/faster-whisper-large-v3-turbo`), a partir do [Whisper](https://github.com/openai/whisper) da OpenAI | transcrição | MIT |
| [PyAV](https://github.com/PyAV-Org/PyAV), com bibliotecas do FFmpeg | leitura de áudio e vídeo importados | BSD-3-Clause (PyAV); LGPL-2.1+ (bibliotecas do FFmpeg nas rodas oficiais) |
| [soundfile](https://github.com/bastibe/python-soundfile) e libsndfile | gravação e compressão sem perdas | BSD-3-Clause; LGPL-2.1+ |
| [NumPy](https://numpy.org), [soxr](https://github.com/dofuuz/python-soxr) | processamento de áudio | BSD-3-Clause; LGPL-2.1+ |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | servidor MCP | MIT |
| Bibliotecas CUDA da NVIDIA (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`), só com GPU NVIDIA | aceleração por GPU | NVIDIA Software License Agreement |

A lista completa e as versões exatas das dependências do núcleo estão em
`voxvault-core/uv.lock`, que é o que o preparo instala.
