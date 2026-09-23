## Context

Motivação em `proposal.md`. O que existe hoje e amarra a abordagem:

- **Localização do núcleo** (`src-tauri/src/paths.rs`, `core_root`): variável `VOXVAULT_CORE_ROOT`, subida a partir do executável ou, em último caso, o `CARGO_MANIFEST_DIR` gravado na compilação. O executável do núcleo é `<núcleo>\.venv\Scripts\voxvault.exe`. Num build publicado, o último recurso apontaria para o disco do runner de CI.
- **Preparo** (`commands.rs`, `ambiente_preparar`): executa `uv sync` do `PATH`, sem extras, dentro do checkout. Não existe `uv.lock`; o ambiente desta máquina foi montado com `uv pip install -e ".[dev,engine,cuda]"`.
- **Tamanhos medidos**:

  | Componente | Tamanho |
  |---|---|
  | Ambiente completo | 2,3 GB |
  | ↳ bibliotecas CUDA (`nvidia/`) | 2,0 GB |
  | ↳ ctranslate2 | 60 MB |
  | ↳ onnxruntime | 41 MB |
  | ↳ numpy | 24 MB |
  | Interpretador gerenciado pelo uv | 70 MB |
  | Modelo `large-v3` | 2,9 GB |
  | Modelo `large-v3-turbo` | 1,6 GB |
  | Modelo `medium` | 1,5 GB |

  O GitHub Releases aceita no máximo 2 GB por arquivo.
- **CPU de referência** (Ryzen 5 3600, int8, 74 s de fala):

  | Modelo | Tempo real | Palavras de ~145 |
  |---|---|---|
  | `medium` | 1,29× | 138 |
  | `large-v3-turbo` | 1,37× | 143 |
  | `large-v3` | 0,96× | 124 |

- **GPU**: detectada pelo `nvidia-smi`, com exigência de 4700 MB + 900 de margem para o `large-v3` e 1600 + 900 para o turbo (`engine/capability.py`).
- **Mídia** (`engine/media.py`): `ffprobe` para a duração, `ffmpeg` para normalizar a importação e comprimir para FLAC, com busca nas pastas do winget. PyAV 18.1 e libsndfile 1.2.2 com escrita FLAC já estão no ambiente, trazidos pelo faster-whisper e pelo soundfile.
- **Rede do motor** (`engine/local_whisper.py`): `WhisperModel(nome, download_root=...)` sem `local_files_only`. O `faster_whisper.utils.download_model` resolve a revisão no Hugging Face **a cada carregamento**, mesmo com o modelo em disco.
- **Pasta de dados**: padrão embutido `D:\VoxVault`, no núcleo (`config.py`) e no aplicativo (`paths.rs`).
- **Build local**: `src-tauri/.cargo/config.toml`, versionado, manda os artefatos para `D:/VoxVault-build`.
- **Referência de publicação**: `erielmiquilino/ia-monitor`, com `release.yml` por tag, conferência da versão, testes antes do build, `softprops/action-gh-release` com `body_path`, e `ci.yml` com o job `testes`. A `main` dele tem PR obrigatório, check `testes`, admin sem trava, e sem force push nem exclusão.

## Goals / Non-Goals

**Goals:**

- Um instalador pequeno que funcione numa máquina Windows sem nada técnico instalado.
- Preparo reproduzível, com versões travadas, retomável e honesto sobre tamanhos.
- Nenhum acesso à rede fora do preparo e do download explícito de modelo.
- CI e release equivalentes ao `ia-monitor`, adaptados a um núcleo Python.

**Non-Goals:**

- Assinatura de código. O executável sai sem assinatura, e o aviso do SmartScreen fica documentado, como no `ia-monitor`.
- Versão portátil. O núcleo precisa de um ambiente preparado, e um executável solto não traz os recursos.
- Atualização automática ou aviso de versão nova. Foi decisão do usuário para a 0.1.0.
- Instalador offline completo: passaria de 4 GB, acima do limite de 2 GB por arquivo.
- macOS e Linux.

## Decisions

### 1. Três lugares, cada um com um dono

| Lugar | Conteúdo | Quem cria | Quem remove |
|---|---|---|---|
| Pasta de instalação — `%LOCALAPPDATA%\VoxVault` (NSIS `currentUser`) | executável, `recursos\nucleo\` (pyproject, `uv.lock`, README, LICENSE, `src\**\*.py`), `recursos\uv\uv.exe`, `recursos\preparo.json`, `THIRD-PARTY-NOTICES.md` | instalador | desinstalador, e cada atualização a substitui |
| Estado por usuário — `%USERPROFILE%\.voxvault` | `config.json`, `aplicativo.json`, `servico.json`, `servico.log` e `runtime\` (`ambiente\` = venv, `python\`, `cache\`, `hf\`, `preparado.json`) | aplicativo e núcleo | desinstalador (inteira); nunca a atualização |
| Pasta de dados — escolhida no preparo | `voxvault.db`, `recordings\`, `models\` | núcleo | ninguém; a desinstalação não toca |

O ambiente fica fora da pasta de instalação porque a atualização do NSIS substitui essa pasta: refazer 2,4 GB a cada versão seria inaceitável. Fica fora de `AppData` pela mesma razão que levou o estado para `.voxvault`: um processo iniciado de dentro de um aplicativo empacotado tem `AppData` redirecionado.

### 2. Resolução do núcleo e do executável

`paths::core_root()` segue esta ordem:

1. `VOXVAULT_CORE_ROOT`;
2. `resource_dir()\recursos\nucleo`;
3. só em builds de depuração (`cfg!(debug_assertions)`), a subida a partir do executável e do `CARGO_MANIFEST_DIR`.

Um build de release nunca mais depende do caminho de quem o compilou.

`paths::core_executable()` usa `%USERPROFILE%\.voxvault\runtime\ambiente\Scripts\voxvault.exe` quando esse arquivo existe. Senão, em depuração, `<núcleo>\.venv\Scripts\voxvault.exe`, que é o fluxo de desenvolvimento de hoje.

### 3. O preparo em etapas idempotentes, com uv embutido

| Etapa | Comando | Pulada quando |
|---|---|---|
| `interpretador` | `uv python install 3.12` | já instalado em `runtime\python` |
| `dependencias` | `uv sync --extra engine --extra mcp` | em dia (o `sync` confirma em segundos) |
| `gpu` | `uv sync --extra engine --extra mcp --extra cuda` | sem GPU adequada (decisão 4) |
| `configuracao` | grava `data_dir` no `config.json` | já gravado com o mesmo valor |
| `modelo` | `voxvault models download --json` | arquivos completos em `models\` |
| `verificacao` | `voxvault doctor --json` | nunca |

- **Opções comuns do `uv`:** `--project <recursos\nucleo> --frozen --no-dev --no-editable --python-preference only-managed`, com o ambiente `UV_PROJECT_ENVIRONMENT=runtime\ambiente`, `UV_PYTHON_INSTALL_DIR=runtime\python`, `UV_CACHE_DIR=runtime\cache` e `UV_NO_CONFIG=1`.
- **Ambiente herdado.** `VIRTUAL_ENV`, `PYTHONPATH` e `PYTHONHOME` são removidos antes de chamar o `uv`.
- **Limpeza.** Ao fim do `sync`, `uv cache prune` libera o que o ambiente não usa.
- **Por que `--no-editable`:** o ambiente não fica apontando para a pasta de instalação, que a atualização substitui. Reinstalar o pacote do núcleo depois de uma atualização custa segundos.
- **Carimbo.** Só a conclusão da `verificacao` grava `runtime\preparado.json`, com `versao_app`, `sha256_lock`, `gpu`, `modelo` e `concluido_em`. Na abertura, versão e lock iguais ao instalado significam pronto. Qualquer diferença roda as etapas de novo, e as concluídas passam em segundos, sem download. **Retomar é rodar de novo.**
- **Progresso.** As linhas de progresso do `uv` (stderr) são repassadas à tela.
- **Erros de rede.** Os do `uv` — resolução de nome, recusa de conexão, TLS — viram "sem conexão com a internet: o preparo precisa baixar X; conecte-se e clique em Retomar".

### 4. Hardware e escolha de modelo antes de existir Python

O aplicativo executa `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits`, sem janela e com limite de 10 s. É o mesmo critério do núcleo. Com os limites vindos de `preparo.json`, gerados a partir de `MODEL_VRAM_MB` e `VRAM_MARGIN_MB` para haver uma fonte só:

- memória ≥ 5600 MB → `large-v3`, com a etapa `gpu`;
- ≥ 2500 MB → `large-v3-turbo`, com a etapa `gpu`;
- menos, ou sem `nvidia-smi` → `large-v3-turbo` na CPU, sem a etapa `gpu`.

O núcleo aplica a mesma regra em tempo de execução (decisão 8); o aplicativo apenas a antecipa para saber o que baixar.

### 5. Tamanhos exatos, gerados no build

`voxvault-app/tools/gerar-manifesto-preparo.py`, rodado no build, lê o `uv.lock` e soma o campo `size` das rodas `win_amd64`/`cp312`/`abi3`/`none-any` efetivamente escolhidas. Faz isso separadamente para `engine+mcp` e para o acréscimo de `cuda`. Grava em `recursos\preparo.json` junto com:

- o tamanho do interpretador;
- o tamanho dos modelos (`large-v3` 3,09 GB, `large-v3-turbo` 1,62 GB, medidos do cache);
- os limites de VRAM.

A tela mostra esses números. O espaço exigido por volume é ambiente mais 20% no volume do perfil, e modelo mais 1 GB de folga para gravações no volume da pasta de dados.

### 6. Download de modelo explícito, e motor só do disco

- **Download.** Novo `voxvault models download [--model M] [--json]`: chama `huggingface_hub.snapshot_download` com o repositório do mapeamento do faster-whisper, `cache_dir=models_dir`, `allow_patterns` só com os arquivos do modelo e uma classe de progresso que emite linhas JSON `{"baixado": n, "total": t}` a cada 250 ms. Downloads interrompidos são retomados pela própria biblioteca.
- **Carregamento.** O motor passa a carregar com `WhisperModel(..., local_files_only=True)`, e todo processo do núcleo define `HF_HUB_OFFLINE=1` e `HF_HUB_DISABLE_TELEMETRY=1`, exceto o comando de download. Modelo ausente vira `MissingPrerequisiteError` com a ação "baixe o modelo em Configurações".
- **Troca de modelo.** Trocar o modelo em Configurações passa a disparar o download com progresso, reutilizando o componente da etapa `modelo`, antes de gravar a escolha.

*Por que:* sem isso cada transcrição consulta `huggingface.co`, e o requisito de rede não se sustentaria.

### 7. Mídia sem ffmpeg

`engine/media.py` é reescrito sobre PyAV e soundfile. Some a busca de executáveis do winget, e `av` passa a ser declarado no extra `engine`.

| Função | Implementação nova |
|---|---|
| `probe_duration_ms` | `av.open`: duração do contêiner ou, sem ela, soma dos quadros do fluxo de áudio |
| `normalized_audio` | decodificação por PyAV com `av.AudioResampler` para 16 kHz mono s16, em blocos, gravando WAV temporário com soundfile; sem carregar o arquivo inteiro |
| `compress_lossless` | soundfile WAV → FLAC `PCM_16`, `compression_level` 8 |
| `verify_lossless` | leitura dos dois por soundfile e comparação amostra a amostra |

O item "Decodificador de mídia" do diagnóstico passa a verificar a importação de `av` e a decodificação de um trecho sintético.

### 8. Padrão de modelo por hardware no núcleo

Em `_resolve_placement`, quando a origem do campo `model` é o padrão embutido, o modelo efetivo sai da tabela da spec, usando `required_vram_mb` e a memória total da GPU. Um modelo configurado explicitamente segue a matriz como hoje. O aviso de CPU passa a trazer o número medido: "sem GPU NVIDIA, a transcrição roda na CPU, cerca de 1,4× o tempo real num processador de 6 núcleos — uma reunião de 1 h leva por volta de 45 min".

### 9. Pasta de dados padrão com continuidade

- **Núcleo.** `config.py` troca a constante por `_default_data_dir()`, avaliada a cada carga. Se `D:\VoxVault\voxvault.db` existir, a pasta efetiva é `D:\VoxVault`, com origem `padrao anterior (dados existentes)`. Senão, `%USERPROFILE%\VoxVault`, com origem `padrao embutido`.
- **Aplicativo.** `paths.rs` espelha a regra, com um novo `DataDirSource::PadraoAnterior`.
- **Sem escrita.** Nada é gravado automaticamente. Quem já tem dados continua como está, e o preparo pré-seleciona essa pasta.

### 10. Instalador NSIS e desinstalação

- **`tauri.conf.json`:**
  - `bundle.windows.nsis.installMode = "currentUser"`;
  - `bundle.resources` mapeando `../../voxvault-core/src/**/*.py` e os arquivos do núcleo para `recursos/nucleo/`, e `recursos/uv/uv.exe`;
  - `bundle.windows.nsis.installerHooks = "windows/ganchos.nsh"`;
  - WebView2 pelo bootstrapper padrão.
- **`NSIS_HOOK_PREUNINSTALL`**, só fora do modo de atualização. A variável exata é conferida no `installer.nsi` gerado; é uma tarefa. Faz, em ordem:
  1. consulta `voxvault.exe serve --status --json`, uma opção nova que devolve `gravacao_ativa` e `fila_pendente`: com gravação ativa → `MessageBox` "Há uma gravação em andamento; encerre-a antes de desinstalar" + `Abort`. Senão, `voxvault.exe serve --stop --force`. A fila pendente não impede a desinstalação: a spec só a recusa durante uma gravação;
  2. lê o `data_dir` efetivo com `voxvault.exe config --json` via `nsExec::ExecToStack`;
  3. remove `$PROFILE\.voxvault` e o valor `VoxVault` em `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`;
  4. ao final, mostra "Suas gravações foram mantidas em <pasta>".
- **Atalho do menu Iniciar.** O AUMID dele precisa ser o identificador `com.erielmiquilino.voxvault`, do qual as notificações dependem. Isso é conferido no `installer.nsi` gerado; se o modelo do Tauri não o fizer, o `NSIS_HOOK_POSTINSTALL` o define.

### 11. Recursos de terceiros fixados

- **`voxvault-app/tools/recursos.json`:** fixa a versão do uv e a SHA-256 de `uv-x86_64-pc-windows-msvc.zip`. A versão é a estável mais recente no início da implementação, publicada pela Astral em `github.com/astral-sh/uv/releases`, e é a mesma em três lugares: o que gera o `uv.lock`, o `setup-uv` do CI e o `uv.exe` embutido. Trocar a versão é editar esse arquivo e regenerar o lock no mesmo commit.
- **`tools/preparar-recursos.ps1`:** baixa, confere a soma (divergência → erro que nomeia o componente) e extrai `uv.exe` em `src-tauri/recursos/uv/`, que é ignorado pelo git. Depois gera `preparo.json`. Roda no `beforeBuildCommand`, depois de `npm run build`.
- **`uv.lock`:** passa a ser versionado. `uv lock --check` entra no CI.

### 12. CI e release no GitHub Actions

- **`ci.yml`**, em `push` e `pull_request` na `main`, com concorrência por ref e cancelamento. Job `testes` em `windows-latest`:
  - `astral-sh/setup-uv` com a versão fixada;
  - `uv sync --frozen --extra dev --extra engine --extra mcp`, sem `cuda`;
  - `uv lock --check`;
  - `uv run ruff check src tests`;
  - `uv run pytest -m "not hardware and not gpu"`;
  - Node 22 com `npm ci` e `npm run check`;
  - Rust estável com `Swatinem/rust-cache`, `cargo test --locked` e `cargo clippy --locked -- -D warnings`.
- **`release.yml`**, em `push` de tags `v*` e `workflow_dispatch`, com `permissions: contents: write`:
  1. `tools/conferir-versao.ps1 <tag>`, que compara a tag com `tauri.conf.json`, `Cargo.toml`, `package.json`, `pyproject.toml` e `__version__` e nomeia o divergente;
  2. os mesmos passos do `ci.yml`;
  3. `npm run tauri build`;
  4. cópia para `dist\VoxVault-<v>-setup.exe` e `SHA256SUMS.txt` com `Get-FileHash`;
  5. publicação: no dispatch, `actions/upload-artifact`; na tag, `softprops/action-gh-release` com `name: VoxVault <v>`, `body_path: .github/release-notes.md` e `generate_release_notes: true`.
- **Pasta de build.** O `.cargo/config.toml` sai do índice do git e entra no `.gitignore`: continua valendo nesta máquina e o CI usa o `target` padrão.

### 13. Repositório público

- **`LICENSE`:** MIT, "Copyright (c) 2026 Eriel Miquilino".
- **`THIRD-PARTY-NOTICES.md`:** uv (MIT/Apache-2.0), CPython e python-build-standalone (PSF), faster-whisper e CTranslate2 (MIT), modelos Whisper convertidos (MIT), Tauri (MIT/Apache-2.0).
- **`README.md`**, reescrito para o público, em português:
  - o que é e por quê;
  - capturas;
  - recursos;
  - requisitos;
  - instalação e primeiro uso, com tamanhos;
  - uso: bandeja, atalho, detecção, importação, exclusão;
  - integração MCP com o Claude Desktop;
  - linha de comando;
  - privacidade e o que é acessado;
  - onde ficam os dados;
  - desinstalação;
  - arquitetura;
  - desenvolvimento;
  - publicação por tag;
  - contribuição, com a proteção da `main`;
  - licença.
- **Capturas** em `docs/imagens/`, feitas do aplicativo apontado para um diretório de demonstração gerado por `tools/dados-de-demonstracao.py`, com reuniões fictícias e sem nenhum dado real, via `Page.captureScreenshot` do depurador do WebView2.
- **Histórico:** vai como está, com autor `erielmiquilino@hotmail.com`. O e-mail da NDD nunca é usado.
- **Descrição e tópicos:** `windows`, `transcription`, `whisper`, `meeting-recorder`, `tauri`, `svelte`, `python`, `mcp`, `privacy`, `offline`.
- **Proteção da `main`:** a mesma do `ia-monitor`, aplicada por `gh api` depois de o check `testes` existir.

### 14. Publicação da 0.1.0, com confirmação a cada ação pública

A ordem é: revisão pré-publicação → criar o repositório e enviar o histórico → CI verde → proteção da `main` → ensaio por dispatch → teste do instalador do ensaio (decisão 15) → tag `v0.1.0` → conferência da release. As ações que tornam algo público ou alteram configuração no GitHub só são executadas depois de o usuário confirmar explicitamente, na hora de cada uma:

- `gh repo create`;
- `git push` da `main`;
- `gh api` de proteção;
- `git push` da tag.

A revisão pré-publicação procura:

- segredos, como `gho_`, `ghp_`, `sk-`, `AKIA` e chaves privadas;
- bancos, áudios e dados pessoais versionados;
- caminhos pessoais em arquivos que a interface mostra.

### 15. Verificação de "máquina limpa" sem mexer no perfil real

Todo caminho por usuário do VoxVault deriva de `USERPROFILE`, e os do uv são definidos explicitamente. Então o aplicativo instalado, executado com `USERPROFILE` apontando para uma pasta vazia e `PATH` sem Python nem ffmpeg, reproduz um primeiro uso sem tocar na instalação real do usuário. O caminho de CPU é exercitado com `VOXVAULT_FORCAR_CPU=1`, lido só pela detecção de hardware do preparo e documentado como chave de diagnóstico. Uma transcrição com `HTTPS_PROXY=http://127.0.0.1:9` prova que nenhuma rede é usada depois do preparo.

## Risks / Trade-offs

- [O executável sem assinatura dispara o SmartScreen] → documentado nas notas e no README com o caminho "Mais informações → Executar assim mesmo". A assinatura fica fora de escopo, como no `ia-monitor`.
- [O primeiro uso baixa gigabytes] → os tamanhos aparecem antes de começar, o download é retomável, e o caminho de CPU evita os 2 GB de CUDA.
- [Antivírus bloqueando o `uv.exe` ou o interpretador baixado] → a falha do `uv` é mostrada com a causa, e o README orienta a exceção.
- [O nome da variável de modo de atualização no NSIS do Tauri] → uma tarefa confere o `installer.nsi` gerado antes de confiar nela; o teste de atualização de 0.1.0 para uma 0.1.1 de ensaio prova que o ambiente sobrevive.
- [Testes do núcleo que dependem de `D:\VoxVault` como padrão] → a troca do padrão (decisão 9) atualiza esses testes, e o CI numa máquina sem `D:\VoxVault` prova a independência.
- [Um PR de terceiros executando código no runner] → o `ci.yml` não usa segredos e tem só permissão de leitura; o `release.yml` só roda por tag ou dispatch do dono.
- [PyAV decodificando um vídeo longo] → decodificação em blocos com reamostragem incremental, sem carregar o arquivo inteiro; um teste com 30 minutos sintéticos mede memória e tempo.

## Migration Plan

1. **Nesta máquina.** Sem `data_dir` configurado e com `D:\VoxVault\voxvault.db`, a pasta continua a mesma (decisão 9). O ambiente de desenvolvimento em `voxvault-core\.venv` continua valendo em builds de depuração. O primeiro uso do aplicativo instalado prepara `%USERPROFILE%\.voxvault\runtime`, sem tocar no `.venv`.
2. **Modelos.** Os modelos de `D:\VoxVault\models` são reaproveitados, porque a etapa `modelo` os encontra completos.
3. **Rollback.** Desinstalar a 0.1.0 remove só o que o instalador e o preparo criaram, e a pasta de dados fica intacta para uma versão futura.
4. **Arquivamento.** `desktop-shell` vem de `add-desktop-app` e `transcription-engine` de `setup-transcription-benchmark`; as duas precisam estar arquivadas antes desta.
