## 1. Núcleo: dependências travadas

- [x] 1.1 Criar `voxvault-app/tools/recursos.json` fixando a versão estável mais recente do uv e a SHA-256 do zip Windows x64 (design, decisão 11), declarar `av` no extra `engine`, gerar `voxvault-core/uv.lock` com essa versão do uv e versioná-lo; verificar que `uv lock --check` passa e que `uv sync --frozen --extra dev --extra engine --extra mcp` num ambiente novo reproduz a suíte passando.

## 2. Núcleo: mídia sem ffmpeg

- [x] 2.1 Reescrever `probe_duration_ms` e `normalized_audio` sobre PyAV, com decodificação e reamostragem em blocos para 16 kHz mono (design, decisão 7); verificar com testes que geram `.opus`, `.m4a`, `.mp4` e `.wav` sintéticos com o próprio PyAV e conferem duração e amostras normalizadas, e com um teste `slow` de 30 minutos sintéticos que mede a memória de pico.
- [x] 2.2 Reescrever `compress_lossless` e `verify_lossless` sobre soundfile (FLAC `PCM_16`, nível 8, comparação amostra a amostra); verificar com os testes de finalização existentes passando e com um teste que corrompe uma amostra do FLAC e confirma a reprovação.
- [x] 2.3 Remover a busca por ffmpeg e ffprobe (winget, `C:\ffmpeg`, `PATH`) e trocar o item "Decodificador de mídia" do diagnóstico pela verificação de PyAV; verificar que `grep -ri ffmpeg voxvault-core/src` não encontra uso e que a suíte passa com o ffmpeg fora do `PATH`.

## 3. Núcleo: rede e modelos

- [x] 3.1 Carregar o modelo com `local_files_only=True` e definir `HF_HUB_OFFLINE=1` e `HF_HUB_DISABLE_TELEMETRY=1` em todo processo do núcleo, exceto no download; modelo ausente vira `MissingPrerequisiteError` com a ação de baixar; verificar com um teste que transcreve com `HTTPS_PROXY=http://127.0.0.1:9` e `HTTP_PROXY` iguais e conclui, e com um teste de modelo ausente que recebe o erro tipado sem tentar rede.
- [x] 3.2 Adicionar `voxvault models download [--model M] [--json]` com progresso em linhas JSON a cada 250 ms e retomada (design, decisão 6); verificar com um teste que simula `snapshot_download` e confere o formato das linhas, e baixando de verdade o `large-v3-turbo` num diretório vazio, interrompendo no meio e retomando sem reiniciar do zero.
- [x] 3.3 Aplicar o padrão de modelo por hardware quando o campo `model` vem do padrão embutido (decisão 8) e atualizar o aviso de CPU com o número medido; verificar com testes da tabela da spec (GPU de 8 GB → `large-v3`; GPU de 4 GB → turbo `float16`; GPU de 2 GB ou sem GPU → turbo `int8` em CPU; modelo explícito prevalece) usando a sondagem de GPU simulada.

## 4. Núcleo: pasta de dados e estado do serviço

- [x] 4.1 Trocar o padrão da pasta de dados por `_default_data_dir()` com a continuidade de `D:\VoxVault` (decisão 9), e atualizar os testes que supunham `D:\VoxVault`; verificar com testes dos dois ramos, com o caminho anterior redirecionado para uma pasta temporária, e com o diagnóstico exibindo a origem `padrao anterior (dados existentes)`.
- [x] 4.2 Adicionar `--json` a `voxvault serve --status`, com `em_execucao`, `gravacao_ativa`, `fila_pendente` e `diretorio_de_dados`; verificar com testes de CLI com e sem serviço em execução.

## 5. Aplicativo: núcleo instalado e preparo

- [x] 5.1 Reordenar `core_root()` e `core_executable()` (decisão 2), com a subida pelo `CARGO_MANIFEST_DIR` só em `debug_assertions`; verificar com testes Rust de cada ramo e confirmando que o binário de release não contém mais o caminho de compilação (`strings`).
- [x] 5.2 Espelhar em `paths.rs` a pasta de dados padrão com `DataDirSource::PadraoAnterior`; verificar com testes Rust dos dois ramos.
- [x] 5.3 Implementar a detecção de hardware por `nvidia-smi` sem janela e com limite de 10 s, a escolha de modelo pelos limites de `preparo.json` e a chave de diagnóstico `VOXVAULT_FORCAR_CPU=1`; verificar com testes Rust que analisam saídas reais e ausentes do `nvidia-smi` e com a chave ligada escolhendo CPU.
- [x] 5.4 Reescrever `ambiente_preparar` nas etapas idempotentes da decisão 3, com o uv embutido, as variáveis `UV_*`, a limpeza de `VIRTUAL_ENV`, `PYTHONPATH` e `PYTHONHOME`, o `uv cache prune`, o carimbo `preparado.json` e a tradução dos erros de rede; verificar executando o preparo completo num `USERPROFILE` temporário e depois repetindo-o, que termina em segundos sem downloads.
- [x] 5.5 Detectar ambiente desatualizado pela comparação do carimbo com a versão e o lock instalados, e rodar só as etapas necessárias; verificar alterando `sha256_lock` no carimbo: o aplicativo roda as etapas sem baixar o modelo.

## 6. Aplicativo: interface do preparo e dos modelos

- [x] 6.1 Reescrever a tela de Preparo: hardware detectado, pasta de dados preenchida com a sugestão ou com `D:\VoxVault` pré-existente e o espaço livre, o que será baixado com tamanhos de `preparo.json` e total, a recusa por espaço insuficiente por volume, o progresso por etapa com bytes e o botão Retomar; verificar pelo depurador do WebView2 num `USERPROFILE` temporário, nos caminhos GPU e CPU (`VOXVAULT_FORCAR_CPU=1`) e com um volume simulado sem espaço.
- [x] 6.2 Fazer a troca de modelo em Configurações disparar o download com progresso antes de gravar a escolha; verificar trocando para `medium` num diretório sem ele: o progresso aparece e a configuração só muda ao concluir.

## 7. Instalador

- [x] 7.1 Configurar `bundle.resources`, `installMode: "currentUser"` e `installerHooks` no `tauri.conf.json`; verificar que o instalador gerado tem no máximo 40 MB e que a pasta instalada contém `recursos\nucleo` só com `.py`, pyproject, lock, README e LICENSE, além de `recursos\uv\uv.exe` e `preparo.json`.
- [x] 7.2 Conferir no `installer.nsi` gerado a variável de modo de atualização e o AUMID do atalho do menu Iniciar; implementar `windows/ganchos.nsh` (decisão 10), definindo o AUMID no pós-instalação se o modelo do Tauri não o fizer; verificar que uma notificação do aplicativo instalado aparece com o nome VoxVault.
- [ ] 7.3 Verificar a desinstalação: com uma gravação ativa é recusada com a mensagem; sem gravação, remove `%USERPROFILE%\.voxvault` e o valor de `Run`, mantém a pasta de dados intacta e informa onde ela está.
- [x] 7.4 Verificar a atualização: instalar a 0.1.0, preparar, instalar uma 0.1.1 de ensaio gerada localmente, e confirmar que o ambiente, o modelo e o início automático sobrevivem e que o aplicativo abre sem baixar nada.

## 8. CI e release

- [x] 8.1 Criar `tools/preparar-recursos.ps1` (download do uv fixado em `recursos.json`, conferência da soma, extração, geração de `preparo.json` pelo `gerar-manifesto-preparo.py`) ligado ao `beforeBuildCommand`; verificar que uma soma adulterada interrompe o build nomeando o uv e que a correta produz os recursos.
- [x] 8.2 Criar `tools/conferir-versao.ps1`; verificar que `v0.1.0` passa e que `v0.2.0` falha nomeando os cinco componentes divergentes.
- [x] 8.3 Criar `.github/workflows/ci.yml` com o job `testes` da decisão 12 e corrigir o que o `clippy -D warnings` e o `pytest` fora desta máquina revelarem; verificar com o workflow verde num fork ou branch de teste antes da publicação.
- [x] 8.4 Criar `.github/workflows/release.yml` com conferência, testes, build, `SHA256SUMS.txt`, ensaio por dispatch e publicação por tag; verificar com um dispatch que produz os artefatos sem criar release.
- [x] 8.5 Tirar `voxvault-app/src-tauri/.cargo/config.toml` do índice e incluí-lo no `.gitignore`, mantendo o arquivo local; verificar que `git ls-files` não o lista e que o build local continua indo para `D:/VoxVault-build`.

## 9. Repositório

- [x] 9.1 Criar `LICENSE` (MIT, Eriel Miquilino, 2026) e `THIRD-PARTY-NOTICES.md` (decisão 13), incluído também nos recursos do instalador; verificar que os dois estão na raiz e na pasta instalada.
- [x] 9.2 Criar `tools/dados-de-demonstracao.py` com reuniões fictícias, gerar as capturas de Gravação, Biblioteca e Reunião em `docs/imagens/` pelo `Page.captureScreenshot` do depurador do WebView2, e montar `docs/imagens/bandeja.png` com os quatro ícones de estado lado a lado a partir dos PNG de `add-resident-tray`; verificar que nenhuma imagem contém dado real.
- [x] 9.3 Reescrever `README.md` com as seções da decisão 13, os tamanhos medidos, os destinos de rede do preparo (`github.com` para o interpretador, `pypi.org`/`files.pythonhosted.org` para os pacotes, `huggingface.co` para o modelo) e o aviso do SmartScreen; verificar que todos os comandos e caminhos citados existem na versão final.
- [x] 9.4 Criar `.github/release-notes.md` com o texto permanente exigido pela spec; verificar que cobre arquivo a baixar, aviso de assinatura, downloads do primeiro uso com tamanhos, requisitos e o que é acessado na máquina.
- [x] 9.5 Fazer a revisão pré-publicação da decisão 14 (segredos, bancos, áudios, dados pessoais, e-mail de autoria em todo o histórico igual a `erielmiquilino@hotmail.com`); verificar com o relatório da revisão sem achados.

## 10. Verificação ponta a ponta

- [ ] 10.1 Máquina limpa simulada, GPU: instalar o artefato do ensaio e executar com `USERPROFILE` vazio e `PATH` sem Python e ffmpeg (decisão 15); verificar preparo completo, gravação, transcrição, importação `.opus`, exclusão e bandeja funcionando.
- [x] 10.2 Máquina limpa simulada, CPU: repetir com `VOXVAULT_FORCAR_CPU=1`; verificar que nada de CUDA é baixado, que o modelo é o turbo e que a transcrição registra CPU e `int8`.
- [ ] 10.3 Sem rede depois do preparo: repetir gravação, transcrição, busca e consulta pelo servidor MCP com `HTTPS_PROXY` e `HTTP_PROXY` apontando para uma porta morta; verificar que tudo funciona.
- [ ] 10.4 Atualizar `docs/estado-da-implementacao.md` com a distribuição e as verificações; verificar que cita cada item desta seção com o resultado.

## 11. Publicação da 0.1.0 (cada ação só com confirmação explícita do usuário no momento)

- [x] 11.1 Com confirmação, criar `erielmiquilino/voxvault` público com descrição e tópicos, e enviar a `main` com o histórico completo; verificar pela API que o repositório existe, público, com todos os commits.
- [x] 11.2 Esperar o CI verde na `main` e, com confirmação, aplicar a proteção igual à do `ia-monitor`; verificar lendo a proteção pela API: PR obrigatório, check `testes`, admin sem trava, sem force push nem exclusão.
- [ ] 11.3 Rodar o ensaio da release por dispatch e repetir o teste do instalador com o artefato dele; verificar os critérios de 10.1.
- [ ] 11.4 Com confirmação, criar e enviar a tag `v0.1.0`; verificar que a release "VoxVault 0.1.0" existe com `VoxVault-0.1.0-setup.exe` e `SHA256SUMS.txt`, que a soma confere com o arquivo baixado e que as notas trazem o texto permanente.
