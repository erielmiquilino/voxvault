# app-distribution Specification

## Purpose
Levar o VoxVault a um computador Windows qualquer: instalar sem pré-requisitos técnicos, manter um ambiente de execução próprio do núcleo, atualizar sem refazer downloads, desinstalar sem tocar no conteúdo da pasta de dados, e não usar a rede fora do preparo.

## Requirements

### Requirement: Instalação por usuário sem pré-requisitos técnicos

O VoxVault SHALL ser instalável por um único instalador, por usuário, sem direitos de administrador, em Windows 10 e Windows 11 de 64 bits.

A instalação MUST NOT exigir Python, uv, ffmpeg ou qualquer outro programa previamente instalado, e SHALL trazer o núcleo e tudo o que é necessário para preparar o ambiente de execução dele.

A instalação SHALL criar uma entrada no menu Iniciar e SHALL garantir o componente de navegação embutido de que a interface depende.

O arquivo do instalador SHALL ter no máximo 40 MB.

#### Scenario: Instalação numa máquina sem Python

- **WHEN** o instalador é executado por um usuário sem direitos de administrador numa máquina sem Python, uv nem ffmpeg
- **THEN** a instalação conclui sem pedir elevação
- **AND** o VoxVault aparece no menu Iniciar e abre na tela de preparo

#### Scenario: Tamanho do instalador

- **WHEN** o instalador de uma versão é gerado
- **THEN** seu tamanho é de no máximo 40 MB

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

### Requirement: Atualização preserva o ambiente

Instalar uma versão mais nova sobre uma existente SHALL preservar o ambiente de execução, os modelos baixados, as configurações e o conteúdo da pasta de dados.

Depois de uma atualização, o aplicativo SHALL preparar apenas o que mudou em relação ao ambiente existente, com o mesmo progresso visível do preparo, e MUST NOT baixar de novo o modelo nem componentes que não mudaram.

#### Scenario: Atualização sem mudança de dependências

- **WHEN** uma versão nova que não altera dependências é instalada sobre a anterior
- **THEN** o aplicativo abre sem baixar nenhum componente
- **AND** gravações, configurações e modelo continuam disponíveis

#### Scenario: Atualização com uma dependência alterada

- **WHEN** uma versão nova que altera uma dependência é instalada
- **THEN** apenas o que mudou é obtido, com progresso visível
- **AND** o modelo não é baixado de novo

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

### Requirement: Rede apenas para o preparo

O aplicativo e o núcleo SHALL acessar a rede apenas para obter, durante o preparo, o interpretador e as dependências do ambiente de execução, e para baixar o modelo de transcrição.

Depois do preparo, gravação, transcrição, busca, importação, exclusão, notas e o servidor MCP SHALL funcionar sem acesso à rede.

O VoxVault MUST NOT enviar telemetria, relatórios de falha ou métricas de uso, nem consultar a existência de versões novas.

#### Scenario: Uso sem rede depois do preparo

- **WHEN** a máquina fica sem acesso à rede depois do preparo concluído
- **THEN** gravar, transcrever, buscar, importar, excluir, gravar notas e consultar pelo servidor MCP funcionam normalmente

#### Scenario: Nenhuma comunicação espontânea

- **WHEN** o aplicativo fica aberto na bandeja por uma hora sem uso
- **THEN** nenhuma conexão de rede é aberta pelo VoxVault

### Requirement: Mídia e compressão sem programas externos

A importação dos formatos suportados, a leitura de sua duração e a compressão sem perdas das gravações SHALL funcionar apenas com o que o instalador e o preparo fornecem, e MUST NOT exigir ffmpeg ou qualquer outro programa externo.

#### Scenario: Importação numa máquina sem ffmpeg

- **WHEN** o usuário importa um áudio `.opus` e um vídeo `.mp4` numa máquina sem ffmpeg
- **THEN** as duas importações concluem e entram na fila de transcrição

#### Scenario: Compressão de uma gravação numa máquina sem ffmpeg

- **WHEN** uma gravação é finalizada numa máquina sem ffmpeg
- **THEN** o áudio é comprimido sem perdas
- **AND** a verificação de que a compressão não perdeu amostras é aprovada

### Requirement: Pasta de dados padrão e continuidade

Quando nenhuma pasta de dados estiver configurada, a pasta sugerida SHALL ser a pasta `VoxVault` dentro da pasta do perfil do usuário.

Quando nenhuma pasta de dados estiver configurada e existir um armazenamento do VoxVault na pasta padrão anterior, `D:\VoxVault`, essa pasta SHALL continuar sendo a pasta de dados efetiva, e o diagnóstico SHALL identificar essa origem.

#### Scenario: Primeira instalação

- **WHEN** o VoxVault é preparado numa máquina sem configuração anterior
- **THEN** a pasta de dados sugerida é `VoxVault` dentro do perfil do usuário

#### Scenario: Usuário com dados na pasta padrão anterior

- **WHEN** o VoxVault é atualizado numa máquina sem pasta de dados configurada e com reuniões em `D:\VoxVault`
- **THEN** as reuniões continuam aparecendo na Biblioteca
- **AND** o diagnóstico informa que a pasta de dados vem do padrão anterior com dados existentes
