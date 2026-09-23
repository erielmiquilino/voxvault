# recording-ui Specification

## Purpose
Tornar a operação de gravar uma reunião imediata e observável: um controle para iniciar e parar, o estado do que está acontecendo, e a evidência ao vivo de que as duas trilhas estão realmente capturando áudio — porque descobrir que o microfone estava mudo só depois da reunião é a pior falha possível desta ferramenta.

## Requirements

### Requirement: Controle de gravação

A interface SHALL oferecer ações para iniciar, pausar, retomar e encerrar uma gravação, com apenas as ações válidas para o estado atual disponíveis a cada momento.

A interface SHALL permitir informar um título ao iniciar, sem torná-lo obrigatório.

O estado corrente — ocioso, gravando ou pausado — SHALL ser visível sem exigir nenhuma interação.

#### Scenario: Ciclo completo pela interface

- **WHEN** o usuário inicia, pausa, retoma e encerra uma gravação pela interface
- **THEN** cada transição é refletida imediatamente no estado visível
- **AND** apenas as ações válidas para cada estado ficam disponíveis

#### Scenario: Início sem título

- **WHEN** o usuário inicia uma gravação sem informar título
- **THEN** a gravação começa normalmente
- **AND** a reunião recebe o título padrão derivado do instante de início

#### Scenario: Falha ao iniciar

- **WHEN** o início da gravação falha por dispositivo indisponível
- **THEN** a causa é apresentada em linguagem natural nomeando o dispositivo
- **AND** o estado permanece ocioso

### Requirement: Tempo decorrido e trilhas em captura

Durante uma gravação, a interface SHALL apresentar o tempo decorrido, atualizado continuamente, excluindo os períodos pausados.

A interface SHALL indicar, separadamente para cada trilha, se ela está capturando.

#### Scenario: Tempo decorrido com pausa

- **WHEN** o usuário grava dez minutos, pausa por cinco e grava mais dez
- **THEN** o tempo decorrido apresentado ao final é de vinte minutos

#### Scenario: Uma trilha deixa de capturar

- **WHEN** uma trilha é encerrada por perda definitiva de dispositivo
- **THEN** a interface indica que aquela trilha não está mais capturando
- **AND** indica que a outra continua

### Requirement: Níveis de áudio ao vivo por trilha

Durante uma gravação, a interface SHALL apresentar o nível de áudio de cada trilha em tempo real, de forma que o usuário perceba imediatamente se uma delas não está recebendo som.

A apresentação dos níveis MUST NOT exigir processamento de áudio adicional além do já realizado pela captura.

#### Scenario: Microfone mudo percebido no início da reunião

- **WHEN** o usuário fala e o microfone está mudo
- **THEN** o nível da trilha de entrada permanece em zero de forma visível
- **AND** o nível da trilha do sistema continua reagindo ao áudio dos outros participantes

#### Scenario: Dispositivo de saída sem áudio

- **WHEN** ninguém fala na reunião
- **THEN** o nível da trilha do sistema permanece em repouso sem que isso seja apresentado como erro

### Requirement: Avisos apresentados durante a gravação

A interface SHALL apresentar, no momento em que ocorrem, os avisos emitidos pela captura: risco de eco ao iniciar sem fone, trilha silenciosa além do limiar, troca de dispositivo, e desalinhamento acima da tolerância.

Os avisos MUST NOT interromper a gravação nem exigir interação para que ela continue.

Um aviso apresentado durante a gravação SHALL permanecer consultável na reunião depois de encerrada.

#### Scenario: Aviso de eco ao iniciar

- **WHEN** o usuário inicia uma gravação com a saída em caixas de som
- **THEN** o aviso de risco de eco é apresentado
- **AND** a gravação inicia normalmente sem exigir interação

#### Scenario: Troca de dispositivo durante a reunião

- **WHEN** o usuário conecta um fone durante a gravação
- **THEN** a interface informa a troca e que a gravação continua
- **AND** o evento permanece consultável na reunião depois

### Requirement: Importação pela interface

A interface SHALL permitir importar arquivos de áudio e vídeo, inclusive por arrastar e soltar, com seleção de múltiplos arquivos numa única operação.

A interface SHALL apresentar o progresso da importação e o resultado individual de cada arquivo.

#### Scenario: Importação por arrastar e soltar

- **WHEN** o usuário arrasta três arquivos de mídia para a janela
- **THEN** três reuniões são criadas e enfileiradas para transcrição
- **AND** o progresso e o resultado de cada arquivo são apresentados

#### Scenario: Arquivo inválido entre os importados

- **WHEN** um dos arquivos arrastados não é mídia válida
- **THEN** os demais são importados normalmente
- **AND** o arquivo inválido é apresentado individualmente com o motivo
