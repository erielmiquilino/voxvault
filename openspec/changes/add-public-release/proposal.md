## Why

O VoxVault funciona nesta máquina porque foi construído nela. O aplicativo acha o núcleo pelo caminho em que foi compilado; o ambiente Python foi montado à mão, sem lockfile; o ffmpeg veio do winget; e a pasta de dados padrão é `D:\VoxVault`. Nada disso existe no computador de quem baixa. Publicar a versão 0.1.0 exige um instalador que traga o núcleo e prepare o resto sozinho, um build e uma publicação reproduzíveis, e um repositório público apresentável.

## What Changes

- **Instalador por usuário**, sem direitos de administrador e sem pré-requisitos técnicos, com o núcleo e o gerenciador de ambiente (uv) embutidos. Fica na ordem de 20 MB.
- **Preparo no primeiro uso.** O aplicativo detecta GPU NVIDIA, pergunta a pasta de dados sugerindo `%USERPROFILE%\VoxVault` e mostra o espaço livre. Antes de começar, mostra o que vai baixar e quanto:
  - com GPU, ≈2,4 GB de ambiente e 2,9 GB de modelo;
  - sem GPU, ≈370 MB e 1,6 GB.

  Depois instala Python e dependências num ambiente isolado e baixa o modelo, com progresso por etapa e retomada após falha.
- **Modelo padrão por hardware:** large-v3 na GPU que o comporta; large-v3-turbo na GPU com pouca memória e na CPU. Na CPU de referência, medido: 1,37× tempo real, contra 0,96× do large-v3, com a transcrição mais completa das três testadas.
- **O ffmpeg deixa de ser dependência externa:** decodificação e compressão passam a usar bibliotecas que o ambiente já instala (PyAV e libsndfile).
- **A pasta de dados padrão deixa de ser `D:\VoxVault`.** Quem já tem dados lá continua usando essa pasta.
- **Atualizar** preserva o ambiente e os modelos, e só baixa o que mudou. **Desinstalar** remove aplicativo, ambiente, início automático e arquivos do serviço, e nunca o conteúdo da pasta de dados.
- **Rede somente no preparo** e no download do modelo. Nenhuma telemetria, relatório de falha ou verificação de atualização.
- **Repositório público** `erielmiquilino/voxvault`: histórico completo como está, autor `erielmiquilino@hotmail.com`, licença MIT, README em português com capturas de dados de demonstração, notas de release, e `main` protegida como no `ia-monitor`.
- **CI e release no GitHub Actions:**
  - o CI roda em todo push e PR e expõe o check `testes`;
  - a release roda por tag `vX.Y.Z`, confere a versão em todos os componentes e roda os testes antes do build;
  - publica `VoxVault-X.Y.Z-setup.exe` com `SHA256SUMS.txt`;
  - um ensaio por disparo manual gera os artefatos sem publicar.
- **Publicação da 0.1.0.** Criar o repositório, enviar o histórico, proteger a branch e empurrar a tag são ações públicas. Cada uma só acontece depois da confirmação explícita do usuário, no momento de executá-la.

## Capabilities

### New Capabilities

- `app-distribution`: instalação, ambiente de execução isolado, atualização, desinstalação, dependências externas, uso de rede e pasta de dados padrão.
- `release-pipeline`: verificação contínua de cada mudança e publicação de versões baixáveis e verificáveis.

### Modified Capabilities

- `desktop-shell`: "Preparo do ambiente de execução na primeira abertura" passa a especificar detecção de hardware, escolha da pasta de dados, tamanhos antes de começar, progresso por etapa, retomada e download do modelo.
- `transcription-engine`: "Configuração padrão do motor" passa a depender do hardware.

## Impact

- **Aplicativo (Rust):**
  - resolução do núcleo pelos recursos instalados;
  - preparo reescrito em torno do uv embutido, com o ambiente em `%USERPROFILE%\.voxvault\runtime`;
  - detecção de GPU sem Python;
  - carimbo de preparo;
  - o caminho de compilação como alternativa só em builds de depuração;
  - novo padrão da pasta de dados;
  - ganchos de desinstalação do NSIS.
- **Aplicativo (interface):** tela de Preparo reescrita, com hardware, pasta, tamanhos, etapas e retomar.
- **Núcleo:**
  - `engine/media.py` sem ffmpeg;
  - novo `voxvault models download`;
  - modelo padrão por hardware;
  - padrão da pasta de dados;
  - `uv.lock` versionado;
  - `av` declarado explicitamente.
- **Repositório:** `LICENSE`, `README.md`, `THIRD-PARTY-NOTICES.md`, `.github/` com os workflows e as notas de release, capturas em `docs/imagens/`, e o `.cargo/config.toml` local fora do versionamento.
- **Externo, com confirmação a cada ação:** repositório público, proteção da `main`, tag e release `v0.1.0`.
- **Ordem:** implementar por último, porque o instalador empacota a exclusão e a bandeja. Arquivar depois de `add-desktop-app` e `setup-transcription-benchmark`.
