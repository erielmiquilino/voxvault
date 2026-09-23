## Why

Existe uma falha de uso que nenhuma das fases anteriores resolve: a reunião começa, alguém já está falando, e só então o usuário lembra de abrir a ferramenta e apertar gravar. O começo da conversa — que costuma ser onde o contexto é estabelecido — se perde. Uma ferramenta de registro que depende de disciplina no momento de maior distração falha justamente quando importa.

Esta fase ataca isso por dois caminhos: tornar o comando de gravar alcançável sem trocar de janela, e fazer a própria ferramenta perceber que uma reunião começou.

## What Changes

- Adiciona ícone de bandeja que reflete o estado de gravação e oferece as ações essenciais sem abrir a janela principal.
- Adiciona atalho global de teclado para iniciar e encerrar gravação a partir de qualquer aplicativo.
- Adiciona o fechamento da janela para a bandeja, mantendo a ferramenta disponível sem ocupar a barra de tarefas, com encerramento real como ação distinta.
- Adiciona notificações do sistema para os eventos que o usuário precisa saber quando não está olhando para a janela: gravação iniciada e encerrada, transcrição concluída, e avisos de captura.
- Adiciona detecção de início e fim de reunião, com ação configurável entre sugerir a gravação e iniciá-la automaticamente.
- Adiciona a opção de iniciar a ferramenta junto com o sistema, desligada por padrão.

## Capabilities

### New Capabilities

- `tray-and-hotkeys`: presença permanente na bandeja, atalho global, comportamento de fechamento e notificações do sistema.
- `meeting-autodetect`: percepção de que uma reunião começou ou terminou, e a ação configurável decorrente disso.

### Modified Capabilities

Nenhuma. Esta fase acrescenta caminhos de acionamento para comportamentos já especificados em `recording-session` e `audio-capture`, sem alterar seus requisitos.

## Impact

- **Depende de**: `add-desktop-app` aplicada. Bandeja, atalho global e notificações vivem no processo hospedeiro.
- **Código novo**: bandeja, registro de atalho global, integração com notificações do sistema e detector de reunião, todos no aplicativo hospedeiro; a configuração correspondente no núcleo.
- **Escopo deliberadamente reduzido**: vínculo automático com a agenda e exportação para o Obsidian foram considerados e deixados de fora, porque o usuário não os selecionou entre as saídas desejadas do produto. Ambos permanecem viáveis como mudanças futuras sobre o que já existe, sem retrabalho.
- **Privacidade**: a detecção de reunião observa sinais locais do sistema sobre uso de áudio e aplicativos em execução. Nenhum conteúdo é inspecionado e nada sai da máquina.
- **Risco de captura indesejada**: o início automático de gravação pode registrar conversas que o usuário não pretendia gravar. Por isso a ação padrão é sugerir, e não iniciar.
