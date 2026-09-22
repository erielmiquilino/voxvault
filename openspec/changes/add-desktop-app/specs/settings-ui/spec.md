## Purpose

Reunir num lugar as escolhas que mudam como a ferramenta se comporta — quais dispositivos capturar, qual motor transcreve, onde os dados ficam e que vocabulário orienta a transcrição — e tornar o diagnóstico de ambiente legível para quem não vai abrir um terminal.

## ADDED Requirements

### Requirement: Configuração de dispositivos

A interface SHALL apresentar os dispositivos de entrada e de saída disponíveis com seus identificadores persistentes, indicando separadamente qual é o padrão de **comunicações** e qual é o padrão de **multimídia**, e qual está selecionado.

A interface SHALL permitir escolher, para cada uma das duas trilhas independentemente, entre seguir o padrão de um papel — comunicações ou multimídia, com comunicações como padrão — ou fixar um dispositivo específico.

A interface SHALL explicar a diferença entre os papéis em linguagem natural, informando que as plataformas de videoconferência usam o de comunicações.

A interface SHALL deixar explícito que a trilha do sistema captura **toda** a mistura reproduzida pelo dispositivo de saída selecionado, e não apenas o áudio da reunião.

Quando um dispositivo fixado não estiver mais disponível, a interface SHALL sinalizá-lo e explicar que a gravação falhará até que a escolha seja corrigida.

A alteração de dispositivos MUST NOT ser permitida durante uma gravação ativa.

#### Scenario: Fixar um microfone específico

- **WHEN** o usuário fixa um microfone específico em vez do padrão do sistema
- **THEN** as gravações seguintes usam esse dispositivo
- **AND** a escolha persiste entre aberturas do aplicativo

#### Scenario: Dispositivo fixado desconectado

- **WHEN** o dispositivo fixado não está mais disponível
- **THEN** a interface o sinaliza e explica que a gravação falhará até a correção

#### Scenario: Tentativa de alterar durante gravação

- **WHEN** há uma gravação ativa
- **THEN** a alteração de dispositivos fica indisponível com a causa explicada

#### Scenario: Padrões de comunicações e multimídia divergentes

- **WHEN** o padrão de comunicações difere do padrão de multimídia
- **THEN** a interface apresenta os dois separadamente, identificados
- **AND** a seleção por papel deixa claro qual dispositivo será efetivamente usado

### Requirement: Configuração do motor de transcrição

A interface SHALL permitir escolher entre os motores e modelos disponíveis, apresentando para cada um as características que orientam a escolha, no mínimo a exigência aproximada de memória e a velocidade relativa.

A interface SHALL indicar quando um motor selecionado não pode ser executado no ambiente atual, com a causa.

A troca de motor MUST NOT alterar transcrições já produzidas; ela passa a valer para as próximas e para reprocessamentos solicitados explicitamente.

#### Scenario: Troca de modelo

- **WHEN** o usuário troca o modelo selecionado
- **THEN** as próximas transcrições usam o novo modelo
- **AND** as transcrições já existentes permanecem inalteradas

#### Scenario: Modelo que não cabe na memória disponível

- **WHEN** o usuário seleciona um modelo que não pode ser executado no ambiente atual
- **THEN** a interface indica a impossibilidade e a causa antes que qualquer transcrição seja tentada

### Requirement: Configuração do diretório de dados

A interface SHALL apresentar o diretório de dados em uso e o espaço livre disponível nele.

A interface SHALL permitir alterá-lo, validando que o novo caminho é gravável antes de aceitar a mudança.

Alterar o diretório de dados MUST NOT mover nem apagar automaticamente o conteúdo do diretório anterior, e a interface SHALL deixar isso explícito ao usuário.

A alteração MUST NOT ser permitida durante uma gravação ativa, com transcrições em andamento, ou com sessões pendentes na fila, porque o trabalho pendente refere-se a arquivos do diretório anterior.

A alteração SHALL ser gravada na configuração compartilhada, e não apenas no estado da aplicação.

Quando o diretório de dados efetivo vier de uma fonte de precedência **superior** à do arquivo de configuração — um argumento de invocação ou uma variável de ambiente —, a interface MUST NOT oferecer a alteração como se ela fosse surtir efeito. Nesse caso a interface SHALL apresentar o valor em modo somente leitura, nomear a fonte que o está impondo, e explicar o que precisa ser feito fora da aplicação para que a configuração volte a valer.

Aceitar uma alteração que a precedência anularia seria pior que recusá-la: o usuário veria a mudança gravada e continuaria operando sobre o armazenamento antigo sem nenhum sinal.

Como o serviço residente resolve o diretório de dados na sua inicialização, a alteração SHALL exigir o reinício do serviço para passar a valer. A interface SHALL conduzir esse reinício e SHALL confirmar ao usuário que o serviço voltou apontando para o novo diretório antes de dar a operação por concluída.

A interface SHALL avisar explicitamente que servidores MCP em execução continuam ligados ao diretório anterior até que a sessão do cliente seja reiniciada, e SHALL instruir o usuário a reiniciar o cliente MCP.

#### Scenario: Espaço em disco baixo

- **WHEN** o espaço livre do diretório de dados cai abaixo do limiar
- **THEN** a interface o sinaliza de forma visível
- **AND** informa o espaço restante e o consumo aproximado por hora de gravação

#### Scenario: Alteração do diretório de dados

- **WHEN** o usuário aponta o diretório de dados para um novo caminho gravável, sem trabalho pendente
- **THEN** a mudança é aceita e gravada na configuração compartilhada
- **AND** a interface informa explicitamente que o conteúdo anterior permaneceu no caminho antigo
- **AND** a interface avisa que os clientes MCP precisam ser reiniciados para enxergar o novo diretório

#### Scenario: Alteração bloqueada por trabalho pendente

- **WHEN** o usuário tenta alterar o diretório de dados com sessões aguardando transcrição
- **THEN** a alteração fica indisponível com a causa explicada
- **AND** a fila pendente não é afetada

#### Scenario: Diretório imposto por variável de ambiente

- **WHEN** o diretório de dados efetivo vem de uma variável de ambiente
- **THEN** a interface apresenta o valor em modo somente leitura
- **AND** nomeia a variável de ambiente como fonte
- **AND** explica o que precisa ser feito fora da aplicação para que a configuração volte a valer

#### Scenario: Serviço reiniciado após a alteração

- **WHEN** o usuário conclui a alteração do diretório de dados
- **THEN** a interface conduz o reinício do serviço residente
- **AND** confirma que o serviço voltou apontando para o novo diretório antes de dar a operação por concluída

### Requirement: Vocabulário de domínio

A interface SHALL permitir editar o vocabulário de domínio global, com explicação de seu efeito sobre a transcrição.

A interface SHALL permitir definir vocabulário específico para uma reunião, a partir dela.

Alterar o vocabulário MUST NOT alterar transcrições já produzidas.

#### Scenario: Edição do vocabulário global

- **WHEN** o usuário adiciona nomes e siglas ao vocabulário global
- **THEN** as próximas transcrições passam a recebê-lo
- **AND** as já produzidas permanecem inalteradas

### Requirement: Diagnóstico de ambiente acessível pela interface

A interface SHALL apresentar o resultado do diagnóstico de ambiente item a item, com o estado de cada um e, para os que não estiverem em ordem, a ação corretiva em linguagem natural.

A interface SHALL permitir reexecutar o diagnóstico sob demanda.

Quando o registro do servidor MCP no cliente do usuário estiver ausente, a interface SHALL apresentar o trecho de configuração a ser adicionado, de forma copiável.

#### Scenario: Diagnóstico com um item em falha

- **WHEN** o diagnóstico indica um pré-requisito ausente
- **THEN** o item aparece em falha com a ação corretiva em linguagem natural

#### Scenario: Registro MCP ausente

- **WHEN** o servidor MCP não está registrado na configuração do cliente
- **THEN** o trecho de configuração a adicionar é apresentado de forma copiável

#### Scenario: Reexecução após a correção

- **WHEN** o usuário corrige o pré-requisito e reexecuta o diagnóstico
- **THEN** o item passa a aparecer em ordem sem necessidade de reiniciar o aplicativo
