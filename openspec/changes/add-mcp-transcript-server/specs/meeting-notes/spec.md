## Purpose

Permitir que resumos, decisões e pendências produzidos a partir de uma reunião sejam gravados de volta no VoxVault e passem a fazer parte do histórico pesquisável, em vez de existirem apenas na conversa onde foram gerados. A transcrição continua sendo o registro do que foi dito; a nota é interpretação, e as duas nunca se confundem.

## ADDED Requirements

### Requirement: Modelo de nota

O sistema SHALL armazenar notas associadas a uma reunião, cada uma com identificador estável, tipo, conteúdo textual, autoria e instantes de criação e de última alteração.

O tipo SHALL ser um entre: resumo, decisões, pendências ou nota livre.

A autoria SHALL registrar a origem que gravou a nota, distinguindo no mínimo o usuário de um cliente automatizado, e no caso deste último SHALL identificar qual cliente.

Uma reunião SHALL poder ter várias notas, inclusive do mesmo tipo.

#### Scenario: Nota gravada por um agente

- **WHEN** um cliente automatizado grava um resumo de uma reunião
- **THEN** a nota é armazenada com tipo resumo, o conteúdo fornecido e a autoria identificando o cliente
- **AND** os instantes de criação e de última alteração são registrados

#### Scenario: Várias notas do mesmo tipo

- **WHEN** duas notas de resumo são gravadas para a mesma reunião
- **THEN** ambas coexistem com identificadores distintos
- **AND** nenhuma sobrescreve a outra

#### Scenario: Nota para reunião inexistente

- **WHEN** uma nota é gravada referenciando uma reunião que não existe
- **THEN** a operação falha identificando a reunião ausente
- **AND** nenhuma nota é criada

### Requirement: Independência entre nota e transcrição

Gravar, alterar ou remover uma nota MUST NOT modificar os segmentos, os metadados de processamento, o áudio ou o título da reunião associada.

Reprocessar a transcrição de uma reunião MUST NOT remover nem alterar suas notas.

#### Scenario: Reprocessamento com notas existentes

- **WHEN** uma reunião que possui notas é transcrita novamente
- **THEN** os segmentos são substituídos pelo novo resultado
- **AND** todas as notas permanecem intactas

#### Scenario: Escrita de nota não altera a transcrição

- **WHEN** uma nota é gravada para uma reunião transcrita
- **THEN** os segmentos e os metadados da reunião permanecem inalterados

### Requirement: Alteração e remoção de notas

O sistema SHALL permitir substituir o conteúdo de uma nota existente pelo seu identificador, atualizando o instante de última alteração.

O sistema SHALL permitir remover uma nota pelo seu identificador.

Alterar ou remover uma nota MUST NOT afetar as demais notas da mesma reunião.

#### Scenario: Atualização de um resumo

- **WHEN** o conteúdo de uma nota existente é substituído
- **THEN** o novo conteúdo é armazenado e o instante de última alteração é atualizado
- **AND** o identificador e a autoria original são preservados

#### Scenario: Remoção de uma nota entre várias

- **WHEN** uma nota é removida de uma reunião que tem três notas
- **THEN** apenas aquela nota deixa de existir
- **AND** as outras duas permanecem inalteradas

#### Scenario: Alteração de nota inexistente

- **WHEN** uma alteração referencia um identificador de nota que não existe
- **THEN** a operação falha identificando o identificador ausente

### Requirement: Notas pesquisáveis junto com as transcrições

O sistema SHALL incluir o conteúdo das notas na busca textual do histórico.

Os resultados de busca SHALL distinguir claramente uma ocorrência em nota de uma ocorrência em transcrição, e SHALL indicar o tipo e a autoria da nota.

A busca SHALL permitir restringir o escopo apenas a transcrições, apenas a notas, ou a ambos.

#### Scenario: Termo que aparece em nota e em transcrição

- **WHEN** o usuário busca por um termo que aparece tanto numa nota quanto num segmento transcrito
- **THEN** as duas ocorrências são devolvidas
- **AND** cada resultado identifica sua natureza, e no caso da nota também o tipo e a autoria

#### Scenario: Busca restrita a notas

- **WHEN** o usuário busca restringindo o escopo a notas
- **THEN** apenas ocorrências em notas são devolvidas

### Requirement: Notas na exportação

A exportação legível de uma reunião SHALL incluir suas notas, apresentadas em seção própria, separada e claramente distinta da linha de tempo transcrita.

A exportação estruturada SHALL incluir as notas com todos os seus campos.

A exportação MUST NOT apresentar conteúdo de nota misturado à linha de tempo, de modo que interpretação nunca seja lida como registro do que foi dito.

#### Scenario: Exportação de reunião com notas

- **WHEN** uma reunião com resumo e pendências é exportada
- **THEN** a exportação legível traz as notas em seção própria, identificadas por tipo e autoria
- **AND** a linha de tempo transcrita permanece separada e sem conteúdo de nota
