## MODIFIED Requirements

### Requirement: Ambiente de execução isolado

O ambiente de execução do núcleo — interpretador e dependências — SHALL ficar numa pasta por usuário, fora da pasta de instalação do aplicativo e fora de `%APPDATA%` e `%LOCALAPPDATA%`.

O ambiente SHALL usar um interpretador próprio, obtido durante o preparo, e MUST NOT usar, alterar ou depender de um Python instalado no sistema, nem alterar variáveis de ambiente globais.

Para que a linha de comando seja chamada pelo nome, o `PATH` do usuário SHALL receber uma única pasta, acrescentada ao fim, contendo apenas os executáveis do VoxVault — a linha de comando e o servidor MCP. O interpretador e as ferramentas das dependências MUST NOT ser alcançáveis pelo `PATH`.

#### Scenario: Máquina com outro Python instalado

- **WHEN** o VoxVault é preparado numa máquina que já tem um Python no `PATH`
- **THEN** o ambiente do VoxVault usa o próprio interpretador
- **AND** o Python existente e seus pacotes permanecem inalterados, e `python` continua sendo o dele

#### Scenario: Linha de comando pelo nome

- **WHEN** o preparo termina e o usuário, ou um agente, abre um terminal novo
- **THEN** `voxvault` e `voxvault-mcp` são encontrados pelo nome
- **AND** nenhum outro executável do ambiente passa a ser encontrado pelo `PATH`

#### Scenario: O restante do PATH preservado

- **WHEN** a pasta do VoxVault é acrescentada a um `PATH` longo, com variáveis como `%USERPROFILE%`
- **THEN** as entradas existentes permanecem iguais, na mesma ordem e com as variáveis sem expandir
- **AND** a pasta do VoxVault aparece uma única vez, no fim

### Requirement: Desinstalação sem perda de gravações

A desinstalação SHALL remover os arquivos do aplicativo, o ambiente de execução, o registro de início automático, a pasta do VoxVault no `PATH` do usuário, os arquivos do serviço local e as preferências por usuário do VoxVault.

A desinstalação MUST NOT remover nada de dentro da pasta de dados — gravações, transcrições, notas e modelos — e SHALL informar, ao terminar, onde essas gravações foram mantidas.

A desinstalação SHALL encerrar o serviço local antes de remover o ambiente de execução, e SHALL ser recusada, informando o motivo, enquanto houver uma gravação em andamento.

Uma atualização MUST NOT executar as remoções da desinstalação.

#### Scenario: Desinstalação com gravações existentes

- **WHEN** o usuário desinstala o VoxVault tendo reuniões gravadas
- **THEN** aplicativo, ambiente, início automático, a pasta do VoxVault no `PATH` e preferências são removidos
- **AND** a pasta de dados permanece intacta
- **AND** a desinstalação informa onde as gravações foram mantidas

#### Scenario: Desinstalação durante uma gravação

- **WHEN** o usuário tenta desinstalar com uma gravação em andamento
- **THEN** a desinstalação é recusada informando que há uma gravação em andamento
- **AND** a gravação continua

#### Scenario: Atualização não remove o ambiente

- **WHEN** uma versão nova é instalada sobre a anterior
- **THEN** o ambiente de execução, a pasta do VoxVault no `PATH` e o início automático configurado permanecem
