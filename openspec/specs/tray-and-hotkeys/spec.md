# tray-and-hotkeys Specification

## Purpose
Manter o VoxVault alcançável sem trocar de janela, para que gravar custe um atalho em vez de uma sequência de cliques no momento em que a reunião já começou, e para que o usuário saiba o que a ferramenta está fazendo sem precisar olhar para ela.

## Requirements

### Requirement: Ícone de bandeja com estado

O aplicativo SHALL manter um ícone na bandeja do sistema enquanto estiver em execução.

O ícone SHALL refletir visualmente o estado corrente entre ocioso, gravando, pausado e falha, de forma distinguível sem interação.

Passar o ponteiro sobre o ícone SHALL apresentar o estado em texto e, durante uma gravação, o tempo decorrido.

#### Scenario: Estado durante uma gravação

- **WHEN** uma gravação está em andamento
- **THEN** o ícone reflete o estado de gravação
- **AND** o texto de apresentação traz o tempo decorrido

#### Scenario: Estado de falha do serviço local

- **WHEN** o serviço local está indisponível
- **THEN** o ícone reflete o estado de falha

### Requirement: Ações rápidas pela bandeja

O menu do ícone de bandeja SHALL oferecer, no mínimo: iniciar ou encerrar gravação conforme o estado, pausar ou retomar quando aplicável, abrir a janela principal, abrir a última reunião e encerrar o aplicativo.

Apenas as ações válidas para o estado corrente SHALL estar disponíveis.

Acionar o encerramento do aplicativo pela bandeja com gravação ativa SHALL seguir a mesma confirmação e o mesmo encerramento limpo exigidos ao fechar a janela.

#### Scenario: Gravação iniciada pela bandeja

- **WHEN** o usuário aciona iniciar gravação pelo menu da bandeja
- **THEN** a gravação começa sem que a janela principal seja aberta
- **AND** o ícone passa a refletir o estado de gravação

#### Scenario: Encerramento do aplicativo pela bandeja com gravação ativa

- **WHEN** o usuário aciona encerrar o aplicativo pela bandeja durante uma gravação
- **THEN** a confirmação é apresentada informando a gravação em andamento e sua duração
- **AND** ao confirmar, a gravação é encerrada de forma limpa antes do aplicativo terminar

### Requirement: Atalho global de teclado

O aplicativo SHALL registrar um atalho global que inicia a gravação quando ocioso e a encerra quando em andamento, funcionando com qualquer aplicativo em primeiro plano.

O atalho SHALL ser configurável pelo usuário.

Quando o atalho configurado já estiver em uso por outro aplicativo, o sistema SHALL informar a falha de registro e MUST NOT falhar silenciosamente.

Cada acionamento do atalho SHALL produzir uma confirmação perceptível, já que o usuário pode não estar vendo a janela nem a bandeja.

#### Scenario: Gravação iniciada durante uma reunião em tela cheia

- **WHEN** o usuário aciona o atalho global com a plataforma de reunião em primeiro plano
- **THEN** a gravação inicia
- **AND** uma confirmação perceptível é apresentada

#### Scenario: Atalho já em uso por outro aplicativo

- **WHEN** o atalho configurado não pode ser registrado porque outro aplicativo o utiliza
- **THEN** o usuário é informado da falha de registro
- **AND** a configuração indica o conflito até que seja resolvido

#### Scenario: Atalho acionado com gravação em andamento

- **WHEN** o usuário aciona o atalho durante uma gravação
- **THEN** a gravação é encerrada e submetida para transcrição
- **AND** uma confirmação perceptível é apresentada

### Requirement: Fechamento para a bandeja

O aplicativo SHALL permitir configurar se fechar a janela encerra o aplicativo ou apenas o recolhe para a bandeja.

Quando configurado para recolher, fechar a janela durante uma gravação MUST NOT interromper a gravação nem pedir confirmação.

O encerramento real do aplicativo SHALL permanecer disponível como ação distinta, com a confirmação de gravação ativa quando aplicável.

#### Scenario: Janela fechada durante uma gravação com recolhimento configurado

- **WHEN** o usuário fecha a janela durante uma gravação com o recolhimento configurado
- **THEN** a janela é recolhida para a bandeja
- **AND** a gravação continua sem interrupção e sem pedir confirmação

#### Scenario: Janela fechada com encerramento configurado

- **WHEN** o usuário fecha a janela com o encerramento configurado e sem gravação ativa
- **THEN** o aplicativo termina e o ícone desaparece da bandeja

### Requirement: Notificações do sistema

O aplicativo SHALL emitir notificações do sistema para: gravação iniciada, gravação encerrada, transcrição concluída, transcrição falha, e avisos de captura relevantes durante a gravação.

Acionar uma notificação de transcrição concluída SHALL abrir a reunião correspondente.

O usuário SHALL poder desativar as notificações por categoria.

Notificações MUST NOT ser emitidas repetidamente para o mesmo evento.

#### Scenario: Transcrição concluída com a janela fechada

- **WHEN** uma transcrição termina com o aplicativo recolhido na bandeja
- **THEN** uma notificação do sistema é emitida
- **AND** acioná-la abre a reunião correspondente

#### Scenario: Categoria de notificação desativada

- **WHEN** o usuário desativa as notificações de gravação iniciada
- **THEN** nenhuma notificação dessa categoria é emitida
- **AND** as demais categorias continuam funcionando

### Requirement: Início junto com o sistema

O aplicativo SHALL permitir configurar sua inicialização automática junto com o sistema operacional, desativada por padrão.

Quando iniciado dessa forma, o aplicativo SHALL subir recolhido na bandeja, sem abrir a janela principal.

O início automático MUST NOT iniciar gravação por si só.

#### Scenario: Início automático após reinicialização

- **WHEN** o sistema é reiniciado com o início automático ativado
- **THEN** o aplicativo sobe recolhido na bandeja
- **AND** nenhuma gravação é iniciada automaticamente por esse motivo
