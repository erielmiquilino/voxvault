# tray-and-hotkeys Specification

## Purpose
Manter o VoxVault alcançável sem trocar de janela, para que gravar custe um atalho em vez de uma sequência de cliques no momento em que a reunião já começou, e para que o usuário saiba o que a ferramenta está fazendo sem precisar olhar para ela.

## Requirements

### Requirement: Ícone de bandeja com estado

O aplicativo SHALL manter um ícone na bandeja do sistema durante toda a sua execução, inclusive com a janela principal fechada.

O ícone SHALL refletir o estado da captura feita pelo próprio VoxVault entre ocioso, gravando, pausado e falha do serviço local. Os quatro estados SHALL ser distinguíveis sem interação e por forma, não apenas por cor.

O estado gravando SHALL significar que o VoxVault está capturando o microfone naquele momento. O ícone MUST NOT indicar o uso do microfone por outros aplicativos.

Uma mudança de estado causada pelo próprio aplicativo SHALL aparecer no ícone em até 1 segundo; uma causada por outra superfície — como uma gravação iniciada pela linha de comando — SHALL aparecer em até 5 segundos.

Passar o ponteiro sobre o ícone SHALL apresentar o estado em texto e, durante uma gravação, o tempo decorrido e o título da reunião, com atraso máximo de 2 segundos em relação ao tempo real decorrido.

#### Scenario: Estado durante uma gravação

- **WHEN** uma gravação está em andamento
- **THEN** o ícone reflete o estado de gravação
- **AND** o texto de apresentação traz o tempo decorrido e o título da reunião

#### Scenario: Gravação pausada

- **WHEN** a gravação em andamento é pausada
- **THEN** o ícone passa ao estado pausado, distinguível do de gravação por forma
- **AND** o texto de apresentação indica pausa e o tempo gravado até então

#### Scenario: Estado de falha do serviço local

- **WHEN** o serviço local está indisponível
- **THEN** o ícone reflete o estado de falha
- **AND** o texto de apresentação resume a causa

#### Scenario: Gravação iniciada por outra superfície

- **WHEN** uma gravação é iniciada pela linha de comando com o aplicativo recolhido na bandeja
- **THEN** o ícone passa ao estado de gravação em até 5 segundos

#### Scenario: Outro aplicativo usando o microfone

- **WHEN** outro aplicativo usa o microfone e o VoxVault não está gravando
- **THEN** o ícone permanece no estado ocioso

### Requirement: Ações rápidas pela bandeja

Um clique com o botão principal no ícone SHALL abrir a janela principal na última tela vista, e MUST NOT alterar o estado da gravação.

Um clique com o botão secundário SHALL abrir o menu da bandeja, cujo primeiro item SHALL ser "Iniciar gravação" quando ocioso, ou "Encerrar gravação" quando gravando ou pausado. Seguem, nesta ordem: pausar ou retomar, disponível apenas durante uma gravação; abrir a janela principal; abrir a última reunião; e encerrar o aplicativo.

Apenas as ações válidas para o estado corrente SHALL estar habilitadas. Com o serviço local indisponível, iniciar gravação SHALL aparecer desabilitado com a indicação de indisponibilidade. Abrir a última reunião SHALL aparecer desabilitado quando não houver reunião.

Iniciar ou encerrar a gravação pela bandeja MUST NOT abrir a janela principal.

Acionar o encerramento do aplicativo pela bandeja com gravação ativa SHALL apresentar a mesma confirmação e o mesmo encerramento limpo exigidos no encerramento seguro, inclusive quando a janela principal estiver fechada.

Encerrar o aplicativo MUST NOT encerrar o serviço local, que continua responsável pela fila de transcrição.

#### Scenario: Gravação iniciada pela bandeja

- **WHEN** o usuário aciona iniciar gravação pelo menu da bandeja
- **THEN** a gravação começa sem que a janela principal seja aberta
- **AND** o ícone passa a refletir o estado de gravação

#### Scenario: Gravação encerrada pela bandeja

- **WHEN** o usuário aciona encerrar gravação pelo menu da bandeja durante uma gravação
- **THEN** a gravação é encerrada e submetida para transcrição sem abrir a janela principal
- **AND** o ícone volta ao estado ocioso

#### Scenario: Clique principal no ícone

- **WHEN** o usuário clica com o botão principal no ícone com o aplicativo recolhido
- **THEN** a janela principal abre na última tela vista
- **AND** o estado da gravação não se altera

#### Scenario: Menu com o serviço indisponível

- **WHEN** o usuário abre o menu da bandeja com o serviço local indisponível
- **THEN** iniciar gravação aparece desabilitado com a indicação de indisponibilidade

#### Scenario: Encerramento do aplicativo pela bandeja com gravação ativa

- **WHEN** o usuário aciona encerrar o aplicativo pela bandeja durante uma gravação, com a janela principal fechada
- **THEN** a confirmação é apresentada informando a gravação em andamento e sua duração
- **AND** ao confirmar, a gravação é encerrada de forma limpa antes do aplicativo terminar
- **AND** ao cancelar, o aplicativo permanece na bandeja e a gravação continua

### Requirement: Atalho global de teclado

O aplicativo SHALL registrar um atalho global que inicia a gravação quando ocioso e a encerra quando em andamento, funcionando com qualquer aplicativo em primeiro plano e com a janela principal fechada.

O atalho padrão SHALL ser Alt+Shift+R.

O atalho SHALL ser configurável pelo usuário nas Configurações, capturando a combinação pressionada; a combinação SHALL conter ao menos um modificador e uma tecla, e SHALL passar a valer imediatamente, sem reiniciar o aplicativo, persistindo entre execuções.

Quando o atalho configurado já estiver em uso por outro aplicativo, o sistema SHALL informar a falha de registro, SHALL manter em vigor o atalho anterior, e MUST NOT falhar silenciosamente.

Iniciar ou encerrar a gravação pelo atalho MUST NOT abrir a janela principal.

Cada acionamento do atalho SHALL produzir uma confirmação perceptível, já que o usuário pode não estar vendo a janela nem a bandeja: a notificação de gravação iniciada ou encerrada, além da mudança do ícone. Quando essas notificações estiverem desativadas, a interface de Configurações SHALL avisar que o atalho passa a ter como confirmação apenas o ícone.

#### Scenario: Gravação iniciada durante uma reunião em tela cheia

- **WHEN** o usuário aciona o atalho global com a plataforma de reunião em primeiro plano
- **THEN** a gravação inicia sem que a janela principal seja aberta
- **AND** uma confirmação perceptível é apresentada

#### Scenario: Atalho já em uso por outro aplicativo

- **WHEN** o atalho escolhido não pode ser registrado porque outro aplicativo o utiliza
- **THEN** o usuário é informado da falha de registro
- **AND** o atalho anterior continua valendo
- **AND** a configuração indica o conflito até que seja resolvido

#### Scenario: Atalho acionado com gravação em andamento

- **WHEN** o usuário aciona o atalho durante uma gravação
- **THEN** a gravação é encerrada e submetida para transcrição
- **AND** uma confirmação perceptível é apresentada

#### Scenario: Troca do atalho

- **WHEN** o usuário troca o atalho nas Configurações
- **THEN** o novo atalho passa a funcionar imediatamente
- **AND** o anterior deixa de responder
- **AND** o novo continua valendo depois que o aplicativo é reiniciado

### Requirement: Recolhimento para a bandeja

Fechar a janela principal, pelo botão de fechar ou pelo atalho de fechar janela do sistema, e minimizá-la SHALL recolher o aplicativo para a bandeja, sem encerrá-lo, sem interromper uma gravação em andamento e sem pedir confirmação.

Ao recolher, a interface SHALL ser descarregada da memória, restando apenas o ícone de bandeja e o serviço local, e MUST NOT permanecer botão do aplicativo na barra de tarefas.

Na primeira vez que o aplicativo for recolhido, o sistema SHALL emitir uma notificação informando que o VoxVault continua em execução na bandeja e como encerrá-lo. Esse aviso MUST NOT se repetir, inclusive depois de reinícios.

O encerramento do aplicativo SHALL acontecer apenas pelo item de encerrar do menu da bandeja, ou por encerramento da sessão ou desligamento do sistema operacional.

Reabrir a janela — pela bandeja, por uma nova execução do aplicativo ou pelo acionamento de uma notificação — SHALL restaurar a última tela vista, e a janela SHALL estar utilizável em até 2 segundos.

#### Scenario: Janela fechada durante uma gravação

- **WHEN** o usuário fecha a janela durante uma gravação
- **THEN** o aplicativo é recolhido para a bandeja
- **AND** a gravação continua sem interrupção e sem pedir confirmação

#### Scenario: Janela minimizada

- **WHEN** o usuário minimiza a janela
- **THEN** o aplicativo é recolhido para a bandeja
- **AND** nenhum botão do aplicativo permanece na barra de tarefas

#### Scenario: Primeiro recolhimento

- **WHEN** o aplicativo é recolhido para a bandeja pela primeira vez
- **THEN** uma notificação explica que ele continua em execução e como encerrá-lo
- **AND** nos recolhimentos seguintes, inclusive depois de reiniciar, a notificação não se repete

#### Scenario: Reabertura na última tela

- **WHEN** o usuário reabre a janela depois de recolhê-la com uma reunião aberta
- **THEN** a janela volta mostrando a mesma reunião
- **AND** está utilizável em até 2 segundos

### Requirement: Notificações do sistema

O aplicativo SHALL emitir notificações do sistema, funcionando também com a janela principal fechada, nas categorias: gravação iniciada, gravação encerrada, transcrição concluída, transcrição falha, avisos de captura durante a gravação e reunião detectada.

Avisos de captura SHALL incluir, no mínimo: o microfone entregando somente silêncio digital — nenhuma amostra diferente de zero, como num microfone mudo pelo sistema ou num dispositivo que parou de captar sem ser removido — por 60 segundos consecutivos durante uma gravação; e a perda, a recuperação ou o encerramento como incompleta de uma trilha por mudança de dispositivo.

Uma sala silenciosa com o microfone funcionando MUST NOT disparar o aviso de silêncio, porque o ruído de fundo de um microfone vivo não é silêncio digital.

A notificação de reunião detectada SHALL oferecer o acionamento único de gravar exigido pela detecção de reunião. Acionada depois do tempo limite da sugestão, ela SHALL abrir a janela principal em vez de iniciar a gravação.

Acionar uma notificação de transcrição concluída ou falha SHALL abrir a reunião correspondente; acionar uma notificação de gravação ou de aviso de captura SHALL abrir a tela de gravação.

O usuário SHALL poder desativar as notificações por categoria nas Configurações; todas as categorias SHALL vir ativadas por padrão.

Notificações MUST NOT ser emitidas repetidamente para o mesmo evento, e eventos anteriores à abertura do aplicativo MUST NOT gerar notificação.

#### Scenario: Transcrição concluída com a janela fechada

- **WHEN** uma transcrição termina com o aplicativo recolhido na bandeja
- **THEN** uma notificação do sistema é emitida
- **AND** acioná-la abre a reunião correspondente

#### Scenario: Microfone mudo durante a gravação

- **WHEN** o microfone entrega somente silêncio digital por 60 segundos consecutivos durante uma gravação
- **THEN** uma notificação de aviso de captura é emitida uma única vez
- **AND** uma nova notificação só ocorre se o sinal voltar e depois faltar outra vez por 60 segundos

#### Scenario: Sala silenciosa com o microfone funcionando

- **WHEN** ninguém fala por vários minutos durante uma gravação com o microfone funcionando
- **THEN** nenhum aviso de silêncio é emitido

#### Scenario: Sugestão de gravação acionada depois do tempo limite

- **WHEN** o usuário aciona a notificação de reunião detectada depois do tempo limite da sugestão
- **THEN** a janela principal é aberta
- **AND** nenhuma gravação é iniciada por esse acionamento

#### Scenario: Categoria de notificação desativada

- **WHEN** o usuário desativa as notificações de gravação iniciada
- **THEN** nenhuma notificação dessa categoria é emitida
- **AND** as demais categorias continuam funcionando

#### Scenario: Reabertura do aplicativo sem notificações antigas

- **WHEN** o aplicativo é aberto depois de transcrições terem terminado enquanto ele estava encerrado
- **THEN** nenhuma notificação é emitida por essas transcrições

### Requirement: Início junto com o sistema

O aplicativo SHALL permitir configurar, nas Configurações, sua inicialização automática junto com o sistema operacional, desativada por padrão, com efeito imediato.

Quando iniciado dessa forma, o aplicativo SHALL subir recolhido na bandeja, sem abrir a janela principal.

O início automático MUST NOT iniciar gravação por si só.

Desativar o início automático SHALL remover o registro de inicialização do sistema operacional.

#### Scenario: Início automático após reinicialização

- **WHEN** o sistema é reiniciado com o início automático ativado
- **THEN** o aplicativo sobe recolhido na bandeja
- **AND** nenhuma gravação é iniciada automaticamente por esse motivo

#### Scenario: Início automático desativado

- **WHEN** o usuário desativa o início automático e reinicia o sistema
- **THEN** o aplicativo não é iniciado
- **AND** nenhum registro de inicialização do VoxVault permanece no sistema operacional
