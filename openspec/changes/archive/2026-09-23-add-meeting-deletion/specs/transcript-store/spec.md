## ADDED Requirements

### Requirement: Exclusão definitiva de reunião

O sistema SHALL permitir excluir uma reunião de forma definitiva, removendo: o áudio de todas as trilhas, todas as revisões de transcrição e seus segmentos, todas as notas, as exportações, as entradas da reunião e de suas notas no índice de busca, e o diretório da reunião com tudo o que ele contém.

A exclusão SHALL estar disponível pela interface e pela linha de comando, e MUST NOT estar disponível pelo servidor MCP.

Antes de excluir, o sistema SHALL oferecer uma prévia do que será perdido — título, data, duração, quantidade de revisões, quantidade de notas, arquivos com seus tamanhos e o espaço total a liberar — e obter a prévia MUST NOT alterar nada.

Pela linha de comando, a exclusão SHALL exigir confirmação explícita no próprio comando; sem ela, o comando SHALL apresentar a prévia e dizer como confirmar, sem remover nada.

A exclusão SHALL ser recusada, com a causa em linguagem natural e sem remover nada, quando:

- a reunião estiver sendo gravada;
- uma tentativa de transcrição da reunião estiver em execução;
- qualquer arquivo da reunião não puder ser removido, por exemplo por estar aberto por outro processo.

Uma reunião apenas aguardando na fila de transcrição SHALL poder ser excluída, e MUST NOT ser transcrita depois disso.

Para uma reunião importada, a exclusão SHALL remover apenas a cópia mantida pelo VoxVault e MUST NOT tocar no arquivo de onde ela foi importada.

A exclusão SHALL ser indivisível para quem consulta o armazenamento: em nenhum momento, inclusive depois de uma interrupção abrupta do processo ou do sistema operacional, SHALL existir uma reunião listada cujos arquivos já foram apagados. Arquivos de uma exclusão interrompida SHALL ser resolvidos na inicialização seguinte do serviço: restaurados, se a reunião ainda consta do armazenamento, ou removidos, se não consta mais.

Excluir várias reuniões num mesmo pedido SHALL tratar cada uma de forma independente: a falha de uma MUST NOT impedir a exclusão das demais nem deixar a que falhou parcialmente removida, e o resultado SHALL informar, por reunião, se foi excluída ou o motivo da recusa.

#### Scenario: Exclusão de uma reunião transcrita com notas

- **WHEN** o usuário exclui uma reunião com áudio, duas revisões de transcrição e três notas
- **THEN** nenhuma informação da reunião permanece no armazenamento
- **AND** o diretório da reunião deixa de existir
- **AND** a busca no histórico não retorna mais trechos nem notas dessa reunião

#### Scenario: Prévia antes de excluir

- **WHEN** o usuário pede a prévia de exclusão de uma reunião
- **THEN** a prévia traz título, data, duração, revisões, notas, arquivos com tamanhos e o espaço total a liberar
- **AND** nada é removido

#### Scenario: Linha de comando sem confirmação

- **WHEN** o comando de exclusão é executado sem a confirmação explícita
- **THEN** a prévia é apresentada com a instrução de como confirmar
- **AND** nada é removido

#### Scenario: Exclusão recusada durante a gravação

- **WHEN** o usuário pede a exclusão de uma reunião que está sendo gravada
- **THEN** a exclusão é recusada com a causa explicada
- **AND** a gravação e a reunião permanecem intactas

#### Scenario: Exclusão recusada durante a transcrição

- **WHEN** o usuário pede a exclusão de uma reunião cuja transcrição está em execução
- **THEN** a exclusão é recusada com a causa explicada
- **AND** a transcrição prossegue normalmente

#### Scenario: Reunião aguardando na fila

- **WHEN** o usuário exclui uma reunião que apenas aguarda na fila de transcrição
- **THEN** a reunião é excluída
- **AND** nenhuma transcrição dela é iniciada depois

#### Scenario: Arquivo da reunião em uso

- **WHEN** um arquivo da reunião está aberto por outro processo e não pode ser removido
- **THEN** a exclusão é recusada nomeando o motivo
- **AND** a reunião permanece listada com todos os seus arquivos

#### Scenario: Reunião importada

- **WHEN** o usuário exclui uma reunião que foi importada de um arquivo
- **THEN** a cópia mantida pelo VoxVault é removida
- **AND** o arquivo original permanece intacto no lugar de onde foi importado

#### Scenario: Interrupção no meio da exclusão

- **WHEN** o processo é encerrado abruptamente durante uma exclusão
- **THEN** na inicialização seguinte do serviço a reunião está ou inteira, listada e com todos os arquivos, ou completamente excluída
- **AND** nenhum arquivo órfão da exclusão interrompida permanece no diretório de dados

#### Scenario: Várias reuniões com uma recusa

- **WHEN** o usuário exclui três reuniões num mesmo pedido e uma delas está sendo transcrita
- **THEN** as outras duas são excluídas
- **AND** a que está sendo transcrita permanece intacta
- **AND** o resultado informa a recusa dela com a causa

#### Scenario: Nenhuma exclusão pelo servidor MCP

- **WHEN** um cliente lista as ferramentas expostas pelo servidor MCP
- **THEN** nenhuma delas exclui reunião
