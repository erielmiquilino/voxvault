## MODIFIED Requirements

### Requirement: Tempo decorrido e trilhas em captura

Durante uma gravação, a interface SHALL apresentar o tempo decorrido, atualizado continuamente, excluindo os períodos pausados.

A interface SHALL indicar, separadamente para cada trilha, se ela está capturando.

A indicação de que uma trilha não está capturando SHALL vir do estado do dispositivo dela — perdido, sendo reaberto, ou sem dispositivo além do prazo de recuperação — e MUST NOT ser deduzida de a trilha do sistema não receber áudio, porque ela não recebe nada enquanto nada é reproduzido. Enquanto a trilha do sistema estiver em silêncio além do limiar do seu aviso, a interface SHALL mostrar, sem apresentá-lo como erro, qual saída está sendo gravada.

#### Scenario: Tempo decorrido com pausa

- **WHEN** o usuário grava dez minutos, pausa por cinco e grava mais dez
- **THEN** o tempo decorrido apresentado ao final é de vinte minutos

#### Scenario: Uma trilha deixa de capturar

- **WHEN** uma trilha perde o dispositivo durante a gravação
- **THEN** a interface indica que aquela trilha não está capturando e que o dispositivo está sendo reaberto
- **AND** indica que a outra continua
- **AND** quando o dispositivo volta, a interface indica que a trilha voltou a capturar

#### Scenario: Saída quieta no começo da gravação

- **WHEN** uma gravação começa sem nada sendo reproduzido no computador
- **THEN** a trilha do sistema aparece capturando, com o nível em repouso
- **AND** nenhuma indicação de dispositivo perdido é apresentada
