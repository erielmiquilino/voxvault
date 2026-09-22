## Purpose

Perceber que uma reunião começou, para que o início da conversa — onde o contexto costuma ser estabelecido — não se perca porque o usuário lembrou de gravar tarde demais. A percepção é local, baseada em sinais do próprio sistema sobre uso de áudio, e nunca inspeciona conteúdo.

## ADDED Requirements

### Requirement: Detecção de início de reunião

O sistema SHALL detectar que uma reunião provavelmente começou observando sinais locais do sistema operacional sobre captura de áudio ativa por outro aplicativo e sobre aplicativos de videoconferência em execução.

Os dois sinais SHALL estar **correlacionados ao mesmo processo**: a detecção exige que o processo que detém a captura de microfone seja ele próprio um aplicativo reconhecido como plataforma de reunião. O sistema MUST NOT considerar detectada uma reunião quando um aplicativo reconhecido está apenas em execução e a captura pertence a outro processo, porque essa combinação ocorre todo dia — o cliente de reunião fica residente enquanto qualquer outro programa usa o microfone.

Quando o processo que detém a captura for um navegador, o sistema MUST NOT inferir a plataforma a partir do navegador em si, que é ambíguo. Nesse caso a detecção SHALL ser tratada como sinal fraco e SHALL depender de um período mínimo de sustentação maior, configurável separadamente.

A detecção MUST NOT inspecionar o conteúdo do áudio, da tela ou de qualquer comunicação, nem títulos de janela, nem endereços visitados.

A detecção SHALL exigir que o sinal se sustente por um período mínimo configurável antes de ser considerada válida, de modo que usos momentâneos do microfone não disparem o mecanismo. Os padrões SHALL ser de **20 segundos** para aplicativo de reunião reconhecido e **60 segundos** para navegador, e o período de cessação que caracteriza o fim SHALL ter padrão de **60 segundos**.

#### Scenario: Reunião iniciada numa plataforma conhecida

- **WHEN** um aplicativo de videoconferência reconhecido detém a captura do microfone por mais de 20 segundos
- **THEN** o início de reunião é detectado

#### Scenario: Aplicativo de reunião ocioso com outro programa usando o microfone

- **WHEN** um aplicativo de reunião está em execução sem capturar, e um gravador independente detém a captura do microfone
- **THEN** nenhum início de reunião é detectado
- **AND** a ausência de correlação entre os dois sinais é o motivo

#### Scenario: Reunião num navegador

- **WHEN** um navegador detém a captura do microfone por mais de 60 segundos
- **THEN** o início de reunião é detectado como sinal fraco
- **AND** nenhuma plataforma específica é inferida a partir do navegador

#### Scenario: Uso momentâneo do microfone

- **WHEN** um aplicativo reconhecido detém a captura do microfone por menos de 20 segundos
- **THEN** nenhum início de reunião é detectado

#### Scenario: Conteúdo nunca inspecionado

- **WHEN** a detecção está ativa
- **THEN** nenhum conteúdo de áudio, tela ou comunicação é lido pelo detector

### Requirement: Ação configurável ao detectar

O sistema SHALL permitir configurar a ação tomada ao detectar o início de uma reunião entre: não fazer nada, sugerir a gravação, ou iniciar a gravação automaticamente.

A ação padrão SHALL ser sugerir a gravação.

Quando configurado para sugerir, o sistema SHALL apresentar uma notificação que permite iniciar a gravação com um único acionamento, e SHALL deixar de oferecê-la após um tempo limite sem resposta.

Quando configurado para iniciar automaticamente, o sistema SHALL notificar que a gravação começou por detecção automática, oferecendo descartá-la imediatamente.

Este requisito existe porque iniciar gravação sem intenção explícita pode registrar conversas que o usuário não pretendia guardar; por isso o comportamento permissivo nunca é o padrão.

#### Scenario: Sugestão aceita

- **WHEN** o início de reunião é detectado com a ação configurada para sugerir e o usuário aciona a sugestão
- **THEN** a gravação inicia normalmente

#### Scenario: Sugestão ignorada

- **WHEN** a sugestão não recebe resposta dentro do tempo limite
- **THEN** ela deixa de ser oferecida
- **AND** nenhuma gravação é iniciada

#### Scenario: Início automático com descarte imediato

- **WHEN** a gravação inicia por detecção automática e o usuário aciona o descarte na notificação
- **THEN** a gravação é encerrada
- **AND** a sessão e seu áudio são removidos sem ser submetidos para transcrição

### Requirement: Detecção de fim de reunião

O sistema SHALL detectar que a reunião terminou quando os sinais que caracterizaram seu início deixarem de existir por um período mínimo configurável.

Quando a gravação em andamento tiver sido iniciada por detecção automática, o sistema SHALL encerrá-la ao detectar o fim.

Quando a gravação tiver sido iniciada manualmente pelo usuário, o sistema MUST NOT encerrá-la automaticamente, e SHALL apenas notificar que a reunião aparenta ter terminado.

#### Scenario: Fim de reunião com gravação iniciada automaticamente

- **WHEN** os sinais de reunião cessam além do período mínimo e a gravação havia sido iniciada por detecção
- **THEN** a gravação é encerrada e submetida para transcrição

#### Scenario: Fim de reunião com gravação iniciada manualmente

- **WHEN** os sinais de reunião cessam e a gravação havia sido iniciada pelo usuário
- **THEN** a gravação continua
- **AND** o usuário é notificado de que a reunião aparenta ter terminado

### Requirement: Não interferência com gravação em andamento

Quando já existe uma gravação ativa, a detecção de início de reunião MUST NOT iniciar outra, sugerir outra, nem interromper a existente.

#### Scenario: Reunião detectada durante uma gravação já em andamento

- **WHEN** o início de reunião é detectado com uma gravação já ativa
- **THEN** nenhuma sugestão é apresentada
- **AND** a gravação em andamento não é afetada

### Requirement: Controle sobre o que dispara a detecção

O sistema SHALL apresentar quais aplicativos são considerados na detecção e SHALL permitir que o usuário exclua qualquer um deles.

O sistema SHALL permitir desativar a detecção por completo.

Aplicativos excluídos MUST NOT disparar detecção em nenhuma circunstância.

#### Scenario: Aplicativo excluído da detecção

- **WHEN** o usuário exclui um aplicativo da detecção e ele passa a capturar o microfone
- **THEN** nenhum início de reunião é detectado

#### Scenario: Detecção desativada

- **WHEN** a detecção está desativada
- **THEN** nenhuma sugestão nem início automático ocorre em nenhuma circunstância
