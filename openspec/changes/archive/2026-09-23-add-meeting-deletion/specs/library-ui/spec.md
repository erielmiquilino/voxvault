## MODIFIED Requirements

### Requirement: Ações sobre uma reunião

A interface SHALL permitir renomear uma reunião, solicitar novo processamento da transcrição, remover o áudio, excluir a reunião, exportar a transcrição e abrir o diretório da reunião no gerenciador de arquivos do sistema.

Ações destrutivas SHALL exigir confirmação explícita que descreve exatamente o que será perdido.

A confirmação de exclusão SHALL listar título, data, duração, quantidade de notas, o que será apagado — áudio, transcrições, notas e exportações — e o espaço em disco que será liberado, e SHALL deixar claro que a exclusão não pode ser desfeita. O botão que confirma SHALL nomear a ação como exclusão definitiva, e o foco inicial MUST NOT estar nele.

Concluída a exclusão, a interface SHALL sair da reunião excluída e voltar à Biblioteca, que MUST NOT listá-la mais.

Ações indisponíveis para o estado atual da reunião SHALL aparecer desabilitadas com a causa explicada, e MUST NOT falhar apenas no momento do acionamento.

#### Scenario: Remoção de áudio com confirmação

- **WHEN** o usuário solicita a remoção do áudio de uma reunião
- **THEN** a confirmação descreve que o áudio será perdido e que a reunião não poderá mais ser reprocessada
- **AND** ao confirmar, o áudio é removido e a transcrição é preservada

#### Scenario: Reprocessamento indisponível

- **WHEN** o usuário abre uma reunião cujo áudio foi removido
- **THEN** a ação de reprocessar aparece desabilitada com a causa explicada

#### Scenario: Exclusão com confirmação

- **WHEN** o usuário solicita a exclusão de uma reunião
- **THEN** a confirmação lista título, data, duração, notas, o que será apagado e o espaço a liberar, e informa que não pode ser desfeita
- **AND** ao confirmar, a reunião é excluída e a interface volta à Biblioteca sem ela
- **AND** ao cancelar, nada é removido

#### Scenario: Exclusão de uma reunião cujo áudio está tocando

- **WHEN** o usuário exclui a reunião cujo áudio está sendo reproduzido
- **THEN** a reprodução é interrompida e o arquivo é liberado antes da exclusão
- **AND** a exclusão não é recusada por causa do próprio reprodutor

#### Scenario: Exclusão indisponível durante gravação ou transcrição

- **WHEN** o usuário abre uma reunião que está sendo gravada ou transcrita
- **THEN** a ação de excluir aparece desabilitada com a causa explicada

## ADDED Requirements

### Requirement: Exclusão de várias reuniões

A Biblioteca SHALL oferecer um modo de seleção em que o usuário marca várias reuniões e as exclui de uma vez.

No modo de seleção, a interface SHALL permitir marcar e desmarcar reuniões individualmente e marcar de uma só vez todas as reuniões visíveis com os filtros correntes, e SHALL apresentar continuamente a quantidade selecionada, a duração somada e o espaço somado.

Reuniões que não podem ser excluídas no estado corrente — sendo gravadas ou transcritas — SHALL aparecer não selecionáveis, com a causa explicada.

A exclusão das selecionadas SHALL exigir uma única confirmação que soma a quantidade de reuniões, a duração, as notas e o espaço a liberar, e informa que não pode ser desfeita.

Quando parte das reuniões selecionadas não puder ser excluída no momento da confirmação, as demais SHALL ser excluídas, e a interface SHALL apresentar, nomeando cada reunião, o motivo de cada recusa.

Sair do modo de seleção SHALL desmarcar tudo sem alterar nenhuma reunião.

#### Scenario: Exclusão de várias reuniões de teste

- **WHEN** o usuário entra no modo de seleção, marca três reuniões e confirma a exclusão
- **THEN** a confirmação apresenta a soma de reuniões, duração, notas e espaço
- **AND** ao confirmar, as três deixam de ser listadas

#### Scenario: Seleção de todas as visíveis com filtro ativo

- **WHEN** o usuário filtra a Biblioteca por um termo e marca todas as visíveis
- **THEN** apenas as reuniões que atendem ao filtro ficam marcadas

#### Scenario: Reunião em transcrição não selecionável

- **WHEN** o modo de seleção está ativo e uma das reuniões está sendo transcrita
- **THEN** ela aparece não selecionável com a causa explicada

#### Scenario: Recusa parcial no momento da exclusão

- **WHEN** entre a seleção e a confirmação uma das reuniões selecionadas passa a ser transcrita
- **THEN** as demais são excluídas
- **AND** a interface informa, nomeando a reunião, que ela não foi excluída e por quê

#### Scenario: Saída do modo de seleção

- **WHEN** o usuário sai do modo de seleção com reuniões marcadas
- **THEN** a seleção é desfeita
- **AND** nenhuma reunião é alterada
