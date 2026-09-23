## MODIFIED Requirements

### Requirement: Preparo do ambiente de execução na primeira abertura

Na primeira abertura, ou sempre que o ambiente de execução do núcleo estiver ausente, incompleto ou desatualizado em relação à versão instalada, o aplicativo SHALL prepará-lo automaticamente, informando o progresso ao usuário.

O aplicativo MUST NOT exigir que o usuário prepare o ambiente manualmente antes do primeiro uso.

Antes de iniciar o primeiro preparo, o aplicativo SHALL:

- detectar se há GPU NVIDIA compatível e apresentar o resultado;
- pedir a pasta de dados, já preenchida com a sugestão da distribuição e mostrando o espaço livre no volume escolhido;
- apresentar o que será baixado, com o tamanho estimado de cada parte e o total — interpretador, dependências, componentes de aceleração por GPU quando houver GPU, e o modelo de transcrição escolhido para o hardware;
- recusar o início quando o espaço livre não comportar o ambiente e o modelo, informando quanto falta em cada volume.

Sem GPU compatível — ou com uma GPU cuja memória não comporta nem o menor modelo padrão —, o preparo MUST NOT baixar os componentes de aceleração por GPU.

O modelo de transcrição padrão para o hardware SHALL ser baixado durante o preparo, de modo que a primeira transcrição não espere por download.

O progresso SHALL ser apresentado por etapa — interpretador, dependências, aceleração por GPU quando aplicável, modelo de transcrição e verificação final —, com a quantidade baixada quando conhecida.

Um preparo interrompido ou falho SHALL poder ser retomado, e a retomada MUST NOT baixar de novo o que já tinha sido concluído.

Quando o preparo falhar, o aplicativo SHALL apresentar a causa em linguagem natural e a ação corretiva — inclusive a falta de conexão com a rede —, e MUST NOT abrir a interface principal num estado em que os comandos aparentem funcionar.

#### Scenario: Primeira abertura numa máquina preparada apenas com os pré-requisitos do sistema

- **WHEN** o usuário abre o aplicativo pela primeira vez
- **THEN** o aplicativo apresenta o hardware detectado, a pasta de dados sugerida com o espaço livre e o que será baixado com os tamanhos
- **AND** ao confirmar, o ambiente é preparado com progresso visível por etapa
- **AND** a interface principal abre ao fim do preparo

#### Scenario: Máquina sem GPU NVIDIA

- **WHEN** o preparo é iniciado numa máquina sem GPU NVIDIA compatível
- **THEN** o que será baixado não inclui componentes de aceleração por GPU
- **AND** o modelo apresentado é o padrão para CPU

#### Scenario: Espaço insuficiente

- **WHEN** o volume da pasta de dados escolhida não tem espaço para o modelo
- **THEN** o preparo não é iniciado
- **AND** o aplicativo informa quanto falta e em qual volume

#### Scenario: Conexão perdida durante o download do modelo

- **WHEN** a conexão cai no meio do download do modelo e o usuário retoma o preparo depois que ela volta
- **THEN** o preparo continua da etapa do modelo
- **AND** o interpretador e as dependências não são baixados de novo

#### Scenario: Falha no preparo do ambiente

- **WHEN** o preparo do ambiente falha
- **THEN** a causa e a ação corretiva são apresentadas em linguagem natural
- **AND** a interface principal não abre em estado aparentemente funcional

#### Scenario: Abertura subsequente com ambiente já preparado

- **WHEN** o usuário abre o aplicativo com o ambiente já preparado para a versão instalada
- **THEN** nenhum preparo é refeito
- **AND** a interface principal abre diretamente
