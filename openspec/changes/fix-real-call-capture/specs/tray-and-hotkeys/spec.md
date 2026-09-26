## MODIFIED Requirements

### Requirement: Notificações do sistema

O aplicativo SHALL emitir notificações do sistema, funcionando também com a janela principal fechada, nas categorias: gravação iniciada, gravação encerrada, transcrição concluída, transcrição falha, avisos de captura durante a gravação e reunião detectada.

Avisos de captura SHALL incluir, no mínimo: o microfone entregando somente silêncio digital — nenhuma amostra diferente de zero, como num microfone mudo pelo sistema ou num dispositivo que parou de captar sem ser removido — por 60 segundos consecutivos durante uma gravação; a trilha do sistema em silêncio digital por 120 segundos consecutivos durante uma gravação, com o nome da saída gravada, porque a chamada pode estar tocando em outra saída; e a perda, a recuperação ou o encerramento como incompleta de uma trilha por mudança de dispositivo.

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

#### Scenario: Trilha do sistema em silêncio

- **WHEN** a trilha do sistema entrega somente silêncio digital por 120 segundos consecutivos durante uma gravação
- **THEN** uma notificação de aviso de captura nomeia a saída gravada, uma única vez
- **AND** uma nova notificação só ocorre se o som voltar e depois faltar outra vez por 120 segundos

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
