## Why

É aqui que o VoxVault passa a resolver o problema que motivou o projeto: guardar o que foi dito numa reunião de Teams, Meet, Zoom ou qualquer outra plataforma, sem depender de permissão, bot ou plugin da plataforma. O Windows permite ler o áudio que ele próprio está reproduzindo; capturando isso junto com o microfone, em trilhas separadas, obtém-se a reunião inteira e a separação entre a sua voz e a dos outros.

Esta fase entrega um núcleo completo e utilizável por linha de comando — gravar, importar, transcrever, guardar e buscar. As fases seguintes são superfícies sobre este núcleo, não extensões dele.

## What Changes

- Captura simultânea de duas trilhas independentes: o microfone e o áudio reproduzido pelo sistema. A separação física das trilhas é o que atribui as falas sem recorrer a um modelo de diarização.
- Alinhamento temporal das trilhas baseado em relógio monotônico, com preenchimento explícito das lacunas que o áudio do sistema produz quando fica ocioso. Sem isso, as duas transcrições deixam de bater na linha de tempo.
- Ciclo de vida de sessão de gravação com pausa, retomada e recuperação após queda do processo.
- Importação de arquivos de áudio e vídeo já existentes, aproveitando o mesmo pipeline de transcrição.
- Armazenamento das reuniões e segmentos em banco local com busca textual sobre todo o histórico.
- Fusão das duas trilhas numa linha de tempo única com atribuição de falante, exportada em formato legível e em formato estruturado.
- Fila de transcrição persistente e retomável, que nunca concorre com uma gravação em andamento.

## Capabilities

### New Capabilities

- `audio-capture`: captura simultânea de microfone e áudio do sistema em trilhas separadas e temporalmente alinhadas, resiliente a troca de dispositivo e a períodos de silêncio.
- `recording-session`: ciclo de vida de uma gravação, seus metadados, seu layout em disco e sua recuperação após interrupção.
- `media-import`: ingestão de arquivos de áudio e vídeo já existentes como reuniões transcritíveis.
- `transcript-store`: modelo de dados de reuniões e segmentos, fusão das trilhas em linha de tempo única, busca textual e exportação.
- `transcription-pipeline`: enfileiramento, execução, retomada e reprocessamento de transcrições, com estado observável por reunião.

### Modified Capabilities

Nenhuma. Esta fase consome `transcription-engine` e `environment-check` pelos contratos já estabelecidos, sem alterar os requisitos deles.

## Impact

- **Depende de**: `setup-transcription-benchmark` aplicada. O contrato `TranscriptionEngine`, a normalização de áudio e o diagnóstico de ambiente são pré-requisitos.
- **Código novo**: módulos de captura, sessão, importação, armazenamento e fila dentro de `voxvault-core`; comandos de linha de comando para operá-los.
- **Dependências novas**: um backend de captura com acesso direto à interface WASAPI, escolhido por um portão de validação no início desta fase — ele precisa expor posição de dispositivo, timestamp de alta resolução e sinalização de descontinuidade por pacote, e está verificado que o backend WASAPI do PortAudio não os expõe. Mais `soundfile` para escrita de áudio. Reforça a exigência de Python 3.12, já fixada na fase anterior.
- **Dados**: cria `voxvault.db` e a árvore `recordings/` sob o diretório de dados. O áudio é preservado indefinidamente por decisão de produto, a um custo aproximado de 120 MB por hora de reunião somando as duas trilhas.
- **Plataforma**: a captura de áudio do sistema depende de uma interface específica do Windows. Nada nesta fase se propõe a ser portável para outros sistemas operacionais.
- **Privacidade**: passa a existir material gravado de terceiros em disco. Todo o processamento é local e nenhum áudio ou transcrição sai da máquina nesta fase.
