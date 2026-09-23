## Purpose

Permitir reencontrar o que foi dito: navegar pelas reuniões, ler a transcrição sabendo quem falou e quando, ouvir o áudio exato de um trecho em dúvida, buscar um assunto em todo o histórico e ver as notas que os agentes gravaram.

## ADDED Requirements

### Requirement: Lista de reuniões

A interface SHALL apresentar as reuniões da mais recente para a mais antiga, cada uma com título, data, duração, estado de processamento, origem e indicação de existência de notas.

A lista SHALL permitir filtrar por período, por estado e por termo no título.

Reuniões em processamento SHALL indicar seu estado corrente, e o progresso SHALL ser atualizado sem exigir que o usuário recarregue a lista.

#### Scenario: Lista após algumas reuniões

- **WHEN** o usuário abre a biblioteca
- **THEN** as reuniões aparecem da mais recente para a mais antiga com todos os campos exigidos

#### Scenario: Progresso de transcrição em andamento

- **WHEN** uma reunião está sendo transcrita enquanto a lista está aberta
- **THEN** o estado e o progresso são atualizados sem interação do usuário
- **AND** ao terminar, a reunião passa a estado pronto na mesma sessão

#### Scenario: Reunião em estado de falha

- **WHEN** uma reunião falhou na transcrição
- **THEN** a lista indica a falha
- **AND** o motivo é consultável a partir da reunião

### Requirement: Leitura da transcrição

A interface SHALL apresentar a linha de tempo de uma reunião com instante, atribuição de falante e texto de cada segmento.

Segmentos atribuídos ao usuário e aos demais participantes SHALL ser visualmente distinguíveis. Segmentos de sessão importada SHALL ser apresentados como de falante desconhecido.

Trechos com fala sobreposta SHALL ser apresentados de forma que os dois segmentos permaneçam legíveis e a sobreposição seja perceptível.

A interface SHALL permitir copiar um trecho selecionado com seu instante e sua atribuição de falante.

#### Scenario: Leitura de uma reunião com as duas trilhas

- **WHEN** o usuário abre uma reunião gravada e transcrita
- **THEN** a linha de tempo apresenta os segmentos intercalados com instante e falante
- **AND** falas do usuário e dos demais são visualmente distinguíveis

#### Scenario: Leitura de reunião importada

- **WHEN** o usuário abre uma reunião criada por importação
- **THEN** os segmentos aparecem como de falante desconhecido
- **AND** nenhum segmento é apresentado como sendo do usuário

#### Scenario: Trecho com fala sobreposta

- **WHEN** a reunião contém um trecho em que os dois lados falaram ao mesmo tempo
- **THEN** os dois segmentos permanecem legíveis
- **AND** a sobreposição é perceptível na apresentação

### Requirement: Reprodução de áudio sincronizada com o texto

A interface SHALL permitir reproduzir o áudio de uma reunião cujo áudio ainda exista.

Acionar um segmento SHALL posicionar a reprodução no instante daquele segmento.

Durante a reprodução, o segmento correspondente ao instante corrente SHALL ser destacado e acompanhado automaticamente.

A interface SHALL permitir escolher qual trilha reproduzir, ou ambas mixadas.

Quando o áudio da reunião tiver sido removido, as ações de reprodução SHALL ficar indisponíveis com a causa explicada.

As ações de reprodução SHALL ficar igualmente indisponíveis enquanto houver uma gravação ativa, porque o áudio reproduzido seria capturado pela trilha do sistema da gravação em curso e incorporado a ela como se fosse fala de participante.

#### Scenario: Conferência de um trecho em dúvida

- **WHEN** o usuário aciona um segmento da transcrição
- **THEN** a reprodução começa no instante daquele segmento
- **AND** o segmento correspondente é destacado conforme o áudio avança

#### Scenario: Escolha de trilha na reprodução

- **WHEN** o usuário escolhe reproduzir apenas a trilha do sistema
- **THEN** apenas o áudio gravado daquela trilha é reproduzido, isto é, a mistura que saiu pelo dispositivo de saída durante a gravação
- **AND** a trilha de entrada não é reproduzida

#### Scenario: Reunião com áudio removido

- **WHEN** o usuário abre uma reunião cujo áudio foi removido
- **THEN** a transcrição continua legível
- **AND** as ações de reprodução ficam indisponíveis com a causa explicada

#### Scenario: Tentativa de reprodução durante uma gravação

- **WHEN** o usuário abre uma reunião antiga enquanto uma gravação está em andamento
- **THEN** a transcrição continua legível
- **AND** as ações de reprodução ficam indisponíveis com a causa explicada
- **AND** nenhum áudio é reproduzido que pudesse ser capturado pela gravação em curso

### Requirement: Busca no histórico pela interface

A interface SHALL oferecer busca textual sobre todo o histórico, com filtros de período e escopo entre transcrições, notas ou ambos.

Cada resultado SHALL identificar a reunião, o instante quando aplicável, a natureza do conteúdo e um recorte com contexto.

Acionar um resultado SHALL abrir a reunião posicionada no trecho encontrado.

#### Scenario: Busca e navegação até o trecho

- **WHEN** o usuário busca um termo e aciona um resultado de transcrição
- **THEN** a reunião é aberta posicionada no segmento correspondente

#### Scenario: Resultado em nota

- **WHEN** um resultado corresponde a uma nota
- **THEN** o resultado identifica que se trata de nota, com seu tipo e autoria
- **AND** acioná-lo abre a reunião na nota correspondente

### Requirement: Notas visíveis na reunião

A interface SHALL apresentar as notas de uma reunião em área própria, separada da linha de tempo, identificando tipo, autoria e instante de criação.

A interface SHALL permitir criar, editar e remover notas do usuário.

A interface MUST NOT apresentar conteúdo de nota intercalado à linha de tempo transcrita.

#### Scenario: Resumo gravado por um agente

- **WHEN** o usuário abre uma reunião para a qual um agente gravou um resumo
- **THEN** o resumo aparece em área própria identificando tipo, autoria e instante
- **AND** a linha de tempo transcrita permanece separada

#### Scenario: Nota criada pelo usuário

- **WHEN** o usuário cria uma nota pela interface
- **THEN** a nota é gravada com autoria do usuário
- **AND** passa a ser encontrável pela busca

### Requirement: Ações sobre uma reunião

A interface SHALL permitir renomear uma reunião, solicitar novo processamento da transcrição, remover o áudio, exportar a transcrição e abrir o diretório da reunião no gerenciador de arquivos do sistema.

Ações destrutivas SHALL exigir confirmação explícita que descreve exatamente o que será perdido.

Ações indisponíveis para o estado atual da reunião SHALL aparecer desabilitadas com a causa explicada, e MUST NOT falhar apenas no momento do acionamento.

#### Scenario: Remoção de áudio com confirmação

- **WHEN** o usuário solicita a remoção do áudio de uma reunião
- **THEN** a confirmação descreve que o áudio será perdido e que a reunião não poderá mais ser reprocessada
- **AND** ao confirmar, o áudio é removido e a transcrição é preservada

#### Scenario: Reprocessamento indisponível

- **WHEN** o usuário abre uma reunião cujo áudio foi removido
- **THEN** a ação de reprocessar aparece desabilitada com a causa explicada
