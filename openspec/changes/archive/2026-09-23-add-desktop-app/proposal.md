## Why

Ao fim da Fase 2 o VoxVault já é útil, mas exige linha de comando para gravar e um cliente de agente para ler. Faltam as duas coisas que uma pessoa faz o tempo todo e que o terminal atrapalha: apertar gravar quando a reunião começa, e reler um trecho ouvindo o áudio correspondente.

A interface existe para essas duas operações. Ela é uma superfície sobre o núcleo já pronto, não uma reimplementação dele, e é construída sob a restrição que motivou o projeto inteiro: o aplicativo não pode pesar na máquina durante a reunião.

## What Changes

- Adiciona um aplicativo de desktop nativo que hospeda a interface e gerencia o ciclo de vida do serviço local que executa o núcleo.
- Prepara automaticamente o ambiente de execução do núcleo na primeira abertura, sem empacotar o interpretador e suas bibliotecas de GPU dentro do instalável.
- Adiciona controle de gravação com estado, tempo decorrido, níveis de áudio por trilha ao vivo e exibição dos avisos que hoje só existem em metadados.
- Adiciona a biblioteca de reuniões: lista com estado de processamento, leitura da transcrição com atribuição de falante, reprodução de áudio sincronizada com o texto, busca sobre o histórico e visualização das notas gravadas por agentes.
- Adiciona as configurações: dispositivos, motor de transcrição, diretório de dados, vocabulário de domínio e o diagnóstico de ambiente apresentado de forma legível.

## Capabilities

### New Capabilities

- `desktop-shell`: aplicativo hospedeiro, preparo e ciclo de vida do serviço local, comunicação restrita à máquina, estado de saúde e encerramento seguro.
- `recording-ui`: controle e observação de uma gravação em andamento, incluindo níveis por trilha e avisos em tempo real.
- `library-ui`: navegação, leitura, reprodução sincronizada e busca sobre o histórico de reuniões e suas notas.
- `settings-ui`: configuração de dispositivos, motor, diretório de dados e vocabulário, com diagnóstico de ambiente acessível.

### Modified Capabilities

Nenhuma. A interface consome os comportamentos já especificados em `audio-capture`, `recording-session`, `media-import`, `transcript-store`, `transcription-pipeline` e `meeting-notes` sem alterar seus requisitos.

## Impact

- **Depende de**: `add-meeting-recording-core` **e** `add-mcp-transcript-server` aplicadas. A segunda é pré-requisito técnico, não apenas desejável: a biblioteca lê, cria, edita e remove notas, cujo modelo e cujas operações nascem em `meeting-notes`; e as configurações apresentam o item de diagnóstico do registro do servidor MCP, definido em `mcp-server`. Sem a fase 2 aplicada, essas partes da interface não têm sobre o que se apoiar.
- **Código novo**: aplicativo de desktop com interface em Svelte, e uma camada de serviço local no núcleo que expõe as operações já existentes para a interface.
- **Dependências novas**: Rust e Tauri para o hospedeiro, Svelte e Vite para a interface, e uma camada de servidor local no núcleo. Node e npm já estão disponíveis na máquina.
- **Distribuição**: o aplicativo roda a partir do build local nesta máquina. Não há instalador, assinatura de código nem atualização automática, por decisão registrada no projeto.
- **Restrição de desempenho**: a interface não pode introduzir custo perceptível durante uma gravação. Isso é requisito verificável, não intenção.
