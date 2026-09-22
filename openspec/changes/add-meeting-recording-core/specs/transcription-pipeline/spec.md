## Purpose

Transformar gravações e importações em transcrições de forma automática, retomável e observável, sem nunca competir por recursos com uma gravação em andamento. É o componente que preserva a premissa central do produto: durante a reunião a máquina só grava.

## ADDED Requirements

### Requirement: Enfileiramento automático

O sistema SHALL enfileirar automaticamente para transcrição toda sessão encerrada, recuperada ou importada, sem exigir ação adicional do usuário.

O sistema SHALL permitir também o enfileiramento explícito de uma sessão específica.

#### Scenario: Gravação encerrada

- **WHEN** o usuário encerra uma gravação
- **THEN** a sessão é enfileirada para transcrição automaticamente

#### Scenario: Importação concluída

- **WHEN** um arquivo é importado com sucesso
- **THEN** a sessão resultante é enfileirada para transcrição automaticamente

### Requirement: Não concorrência com gravação ativa

O sistema MUST NOT executar transcrição enquanto houver uma gravação ativa.

Sessões enfileiradas durante uma gravação SHALL permanecer na fila e SHALL começar a ser processadas apenas depois que a gravação for encerrada ou pausada.

Quando uma gravação é solicitada com uma transcrição em andamento, o sistema SHALL interromper a transcrição e SHALL capturar a primeira amostra de áudio em no máximo 2000 ms contados do comando, incluindo o tempo de interrupção e de liberação de memória de GPU.

Este requisito existe porque a transcrição satura GPU e CPU, e o produto se propõe justamente a não degradar a máquina durante a reunião.

#### Scenario: Sessão enfileirada durante outra gravação

- **WHEN** o usuário importa um arquivo enquanto uma gravação está em andamento
- **THEN** a sessão importada permanece na fila sem ser processada
- **AND** a transcrição começa depois que a gravação é encerrada

#### Scenario: Gravação iniciada com transcrição em andamento

- **WHEN** o usuário inicia uma gravação enquanto uma transcrição está sendo executada
- **THEN** a transcrição em andamento é interrompida de forma limpa e devolvida à fila
- **AND** a primeira amostra de áudio é capturada em no máximo 2000 ms contados do comando
- **AND** a transcrição interrompida é retomada após o fim da gravação

### Requirement: Fila persistente e retomável

A fila de transcrição SHALL sobreviver ao encerramento do processo.

Ao iniciar, o sistema SHALL retomar o processamento das sessões pendentes, e SHALL devolver à fila qualquer sessão que estivesse em processamento quando o processo foi encerrado.

Uma transcrição interrompida SHALL descartar a revisão em construção por completo, sem publicá-la, e MUST NOT deixar segmentos dela associados à reunião. A revisão ativa anterior, quando houver, permanece intacta.

#### Scenario: Processo encerrado com sessões na fila

- **WHEN** o processo é encerrado com duas sessões aguardando transcrição
- **THEN** ao iniciar novamente as duas sessões continuam na fila
- **AND** o processamento é retomado automaticamente

#### Scenario: Processo encerrado durante uma transcrição

- **WHEN** o processo é encerrado no meio da transcrição de uma trilha
- **THEN** a sessão volta ao estado pendente
- **AND** a revisão em construção é descartada por completo, sem deixar segmentos no armazenamento
- **AND** a revisão ativa anterior, quando houver, permanece intacta
- **AND** a transcrição é refeita do início na próxima execução

### Requirement: Processamento por trilha com falha isolada

O sistema SHALL transcrever cada trilha de uma sessão independentemente.

A falha na transcrição de uma trilha MUST NOT impedir que a outra seja transcrita.

Quando apenas uma trilha é transcrita com sucesso, o resultado compõe uma revisão parcial, cuja publicação segue a política de revisões do armazenamento: ela só se torna ativa quando não existir revisão ativa anterior mais completa.

Quando a revisão parcial se torna ativa, a reunião SHALL ficar em estado que indica transcrição parcial, e a linha de tempo SHALL ser gerada a partir da trilha disponível.

Quando a revisão parcial não se torna ativa por existir resultado anterior mais completo, a tentativa SHALL ser registrada como falha com o motivo, e o usuário SHALL ser informado de que o resultado existente foi preservado.

#### Scenario: Uma das trilhas falha na primeira transcrição

- **WHEN** a transcrição da trilha do sistema falha e a do microfone tem sucesso, e a reunião não tem revisão anterior
- **THEN** a revisão parcial com os segmentos do microfone torna-se ativa
- **AND** a reunião fica marcada como transcrição parcial com o motivo da falha registrado
- **AND** a linha de tempo é gerada apenas com os segmentos disponíveis

#### Scenario: Uma das trilhas falha no reprocessamento de reunião já completa

- **WHEN** uma trilha falha ao reprocessar uma reunião que já tinha revisão ativa completa
- **THEN** a revisão parcial não é publicada
- **AND** a revisão anterior permanece ativa e inalterada
- **AND** o usuário é informado de que o resultado existente foi preservado

### Requirement: Estado observável da reunião

Cada reunião SHALL expor **dois atributos independentes**, e MUST NOT colapsá-los num único estado:

1. **Disponibilidade da transcrição**: se existe revisão ativa e se ela é completa ou parcial. Uma reunião com revisão ativa tem transcrição legível, qualquer que seja o estado da tentativa corrente.
2. **Estado da tentativa corrente**: gravando, finalização pendente, aguardando transcrição, transcrevendo, ou última tentativa falha, com o motivo legível quando aplicável.

Essa separação existe porque uma reunião reprocessada tem, ao mesmo tempo, uma transcrição completa disponível e uma nova tentativa em andamento — ou falha. Um estado único obrigaria a mentir sobre um dos dois.

Toda superfície que apresente o estado de uma reunião SHALL apresentar os dois atributos, e MUST NOT indicar ausência de transcrição quando existe revisão ativa.

Transições de estado SHALL ser persistidas, de modo que sobrevivam ao encerramento do processo.

#### Scenario: Reprocessamento de reunião já transcrita

- **WHEN** uma reunião com revisão ativa completa é reprocessada
- **THEN** a disponibilidade indica transcrição completa durante todo o reprocessamento
- **AND** o estado da tentativa corrente indica que ela está sendo transcrita
- **AND** a transcrição anterior permanece legível o tempo todo

#### Scenario: Reprocessamento que falha

- **WHEN** um reprocessamento falha nas duas trilhas
- **THEN** a disponibilidade continua indicando a revisão ativa anterior
- **AND** o estado da tentativa corrente indica falha com o motivo legível

#### Scenario: Reunião nunca transcrita

- **WHEN** uma reunião aguarda sua primeira transcrição
- **THEN** a disponibilidade indica ausência de revisão ativa
- **AND** o estado da tentativa corrente indica que está aguardando

### Requirement: Pedido de reprocessamento repetido

O sistema MUST NOT enfileirar mais de uma tentativa de transcrição por reunião.

Quando um reprocessamento é solicitado para uma reunião que já está aguardando ou sendo transcrita, o sistema SHALL recusar o pedido informando a tentativa em andamento, e MUST NOT interromper nem duplicar a tentativa corrente.

#### Scenario: Reprocessamento pedido duas vezes

- **WHEN** o usuário solicita reprocessar uma reunião que já está na fila
- **THEN** o segundo pedido é recusado informando a tentativa em andamento
- **AND** apenas uma tentativa permanece enfileirada

#### Scenario: Acompanhamento de uma reunião pelo ciclo completo

- **WHEN** o usuário consulta o estado de uma reunião ao longo do processo
- **THEN** o estado evolui de gravando para aguardando transcrição, transcrevendo e pronta

#### Scenario: Consulta de estado após falha

- **WHEN** a transcrição de uma reunião falha nas duas trilhas
- **THEN** a reunião fica em estado de falha
- **AND** o motivo é legível na consulta

### Requirement: Reprocessamento de transcrição

O sistema SHALL permitir transcrever novamente uma reunião cujo áudio ainda exista, usando a configuração de motor vigente ou uma indicada explicitamente.

O reprocessamento SHALL produzir uma nova revisão e, quando publicada, SHALL substituir integralmente a revisão ativa anterior e regerar as exportações a partir dela.

O sistema MUST NOT publicar uma revisão que combine segmentos novos com segmentos da revisão anterior.

O sistema MUST NOT permitir reprocessar uma reunião cujo áudio tenha sido removido, e SHALL falhar com erro explícito nesse caso.

#### Scenario: Reprocessamento com motor melhor

- **WHEN** o usuário reprocessa uma reunião antiga após trocar a configuração do motor
- **THEN** uma nova revisão é publicada e torna-se ativa de uma só vez
- **AND** nenhum segmento da revisão anterior permanece na linha de tempo
- **AND** as exportações são regeradas a partir da nova revisão
- **AND** os metadados registram o novo identificador de motor e configuração

#### Scenario: Reprocessamento sem áudio disponível

- **WHEN** o usuário tenta reprocessar uma reunião cujo áudio foi removido
- **THEN** a operação falha com erro explícito
- **AND** a transcrição existente permanece intacta

### Requirement: Vocabulário de domínio configurável

O sistema SHALL permitir configurar um vocabulário de domínio global — nomes, siglas e jargão recorrentes — repassado ao motor em toda transcrição.

O sistema SHALL permitir sobrescrever esse vocabulário por reunião.

O vocabulário efetivamente usado SHALL ser registrado nos metadados da reunião.

#### Scenario: Vocabulário global aplicado

- **WHEN** existe vocabulário global configurado e uma reunião é transcrita
- **THEN** o vocabulário é repassado ao motor
- **AND** fica registrado nos metadados da reunião

#### Scenario: Vocabulário específico de uma reunião

- **WHEN** uma reunião tem vocabulário próprio configurado
- **THEN** o vocabulário da reunião é usado no lugar do global
