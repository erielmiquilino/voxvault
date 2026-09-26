## MODIFIED Requirements

### Requirement: Ciclo de vida da gravação

O sistema SHALL permitir iniciar uma gravação, que passa a capturar até ser explicitamente encerrada.

Ao iniciar, o sistema SHALL criar a sessão em disco e registrá-la no armazenamento antes de capturar a primeira amostra, de modo que uma sessão exista mesmo que a captura falhe em seguida.

Ao encerrar, o sistema SHALL finalizar os arquivos de áudio, registrar a duração total e submeter a sessão para transcrição.

Se a finalização falhar em qualquer passo, o sistema SHALL finalizar a sessão com o áudio que está em disco, registrar a duração que os arquivos indicam, fechar a sessão no armazenamento e submetê-la para transcrição, e SHALL informar a falha. Uma sessão encerrada MUST NOT continuar marcada como em gravação.

#### Scenario: Gravação iniciada e encerrada normalmente

- **WHEN** o usuário inicia uma gravação, espera, e a encerra
- **THEN** a sessão existe no armazenamento com instantes de início e fim e duração total
- **AND** as duas trilhas de áudio estão finalizadas e legíveis
- **AND** a sessão é submetida para transcrição

#### Scenario: Falha ao abrir a captura

- **WHEN** o início da gravação falha porque um dispositivo não pôde ser aberto
- **THEN** a sessão é marcada como falha com o motivo registrado
- **AND** nenhum arquivo de áudio vazio é deixado para trás

#### Scenario: Falha no meio do encerramento

- **WHEN** o encerramento de uma gravação falha num dos passos da finalização
- **THEN** a sessão é finalizada com o áudio em disco e fechada no armazenamento com a duração dos arquivos
- **AND** a sessão é submetida para transcrição
- **AND** a falha é informada e registrada no log do serviço

## ADDED Requirements

### Requirement: Registro de eventos do serviço

O serviço SHALL registrar num arquivo de log, com data e hora, cada início e encerramento de gravação, cada perda, recuperação, migração e marcação de trilha como incompleta, cada aviso da sessão, cada falha com a sua causa, e o próprio encerramento do serviço com o motivo.

As linhas do log SHALL identificar a reunião pelo seu identificador e MUST NOT conter o título da reunião nem conteúdo transcrito.

O arquivo de log SHALL ter tamanho limitado: ao passar de 1 MB, ele SHALL ser substituído por um novo antes da próxima inicialização do serviço, mantendo a versão anterior.

#### Scenario: Troca de dispositivo registrada

- **WHEN** uma trilha perde o dispositivo durante uma gravação e depois volta a ser gravada em outro
- **THEN** o log do serviço tem uma linha com data e hora para a perda e outra para a volta, com o nome do dispositivo

#### Scenario: Log que cresceu demais

- **WHEN** o log do serviço passa de 1 MB
- **THEN** na próxima inicialização do serviço o log começa vazio
- **AND** o conteúdo anterior continua disponível na versão anterior do arquivo
