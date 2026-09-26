## MODIFIED Requirements

### Requirement: Seleção de dispositivos e papel do padrão

O sistema SHALL distinguir explicitamente os papéis de dispositivo padrão que o sistema operacional mantém separadamente, e SHALL usar por padrão o papel de **comunicações**, tanto para a entrada quanto para a saída capturada, por ser o papel que as plataformas de videoconferência utilizam.

O sistema SHALL permitir configurar o papel usado, entre comunicações e multimídia, independentemente para cada trilha.

Quando a trilha do sistema segue o padrão e o microfone gravado é a entrada de um headset em modo de chamada — forma de headset ou de monofone —, a trilha do sistema SHALL gravar a saída ativa do mesmo aparelho com forma de headset ou de monofone, e não o padrão do papel, porque é por essa saída que o app de reunião toca a chamada. O aparelho SHALL ser reconhecido pelo seu identificador de contêiner, e MUST NOT ser reconhecido pelo nome do dispositivo.

Essa escolha SHALL ser reavaliada durante a gravação com o mesmo prazo de detecção de uma mudança de padrão: quando a saída de chamada do aparelho fica ativa, a trilha migra para ela; quando ela deixa de estar ativa, a trilha volta ao padrão do papel.

O sistema SHALL permitir, para cada trilha, uma entre duas políticas explícitas:

- **Seguir o padrão**: a trilha acompanha o dispositivo padrão do papel configurado, inclusive quando ele muda durante a gravação.
- **Dispositivo fixado**: a trilha usa um dispositivo específico, identificado por um identificador persistente entre reinicializações e reconexões, e MUST NOT migrar para outro dispositivo em nenhuma circunstância.

O sistema SHALL oferecer uma forma de listar os dispositivos disponíveis com seus identificadores persistentes, indicando qual é o padrão de cada papel.

Quando um dispositivo fixado não existe no momento de iniciar a gravação, o sistema SHALL falhar com erro que nomeia o dispositivo ausente e lista os disponíveis, e MUST NOT cair para o padrão do sistema.

#### Scenario: Listagem de dispositivos com papéis

- **WHEN** o usuário solicita a lista de dispositivos de áudio
- **THEN** o sistema apresenta os dispositivos de entrada e de saída com seus identificadores persistentes
- **AND** indica qual é o padrão de comunicações e qual é o padrão de multimídia

#### Scenario: Padrões de comunicações e multimídia divergentes

- **WHEN** o padrão de comunicações difere do padrão de multimídia e a política é seguir o padrão
- **THEN** a trilha usa o dispositivo do papel configurado, por omissão o de comunicações

#### Scenario: Chamada num headset Bluetooth

- **WHEN** o app de reunião usa o microfone de um headset Bluetooth e toca a chamada pela saída Hands-Free do headset, enquanto o padrão de comunicações é a saída estéreo do mesmo headset
- **THEN** a trilha do sistema grava a saída Hands-Free
- **AND** a voz dos outros participantes está na trilha do sistema

#### Scenario: Chamada que começa depois da gravação

- **WHEN** a gravação começa antes da chamada e a saída Hands-Free do headset só fica ativa quando a chamada começa
- **THEN** a trilha do sistema migra para a saída Hands-Free em no máximo 5 segundos
- **AND** o intervalo da migração é preenchido com silêncio e registrado nos metadados

#### Scenario: Dispositivo fixado indisponível

- **WHEN** o usuário inicia uma gravação com um dispositivo fixado que foi desconectado
- **THEN** a gravação não inicia
- **AND** o erro nomeia o dispositivo ausente e lista os disponíveis

### Requirement: Reação a mudança de dispositivo durante a gravação

O sistema SHALL detectar, em no máximo 5 segundos, tanto a perda do dispositivo em uso quanto a mudança do dispositivo padrão do papel configurado, ainda que o dispositivo anterior continue disponível e o fluxo de captura não apresente erro.

Para uma trilha com política de seguir o padrão, o sistema SHALL migrar a captura para o novo dispositivo padrão e prosseguir a gravação. Quando não existir dispositivo padrão utilizável para o papel, ou quando a abertura do novo padrão falhar, o sistema SHALL tentar novamente e, passados **30 segundos** sem sucesso, SHALL marcar aquela trilha como incompleta e avisar o usuário.

Para uma trilha com dispositivo fixado, o sistema MUST NOT migrar. Se o dispositivo fixado for perdido, o sistema SHALL tentar reabri-lo e, passados 30 segundos sem sucesso, SHALL marcar aquela trilha como incompleta e avisar o usuário.

Em ambas as políticas, o prazo de 30 segundos é de **recuperação**, e é distinto do prazo de 5 segundos de **detecção**. O intervalo total não capturado corresponde à soma dos dois e SHALL ser registrado como uma única lacuna nos metadados.

Esgotado o prazo, o sistema SHALL continuar tentando enquanto a gravação durar, e a trilha SHALL voltar a ser gravada quando um dispositivo utilizável estiver disponível; o intervalo inteiro, da perda à volta, SHALL ser registrado como uma única lacuna.

Uma tentativa de abertura que demora MUST NOT atrasar a detecção nem a recuperação da outra trilha.

Uma trilha marcada como incompleta MUST NOT encerrar a gravação: a outra trilha prossegue, e a sessão é finalizada normalmente ao comando do usuário.

O intervalo não capturado durante uma migração ou reabertura SHALL ser preenchido com silêncio, preservando o alinhamento temporal, e SHALL ser registrado nos metadados com instante e duração.

O sistema MUST NOT encerrar a gravação nem descartar o áudio já capturado em decorrência de mudança de dispositivo.

#### Scenario: Fone conectado muda o padrão sem desconectar o anterior

- **WHEN** o usuário conecta um fone durante uma gravação, o padrão de comunicações passa a ser o fone e o dispositivo anterior continua disponível
- **THEN** a mudança é detectada em no máximo 5 segundos
- **AND** a trilha com política de seguir o padrão migra para o fone e a gravação continua
- **AND** o intervalo da migração é preenchido com silêncio e registrado nos metadados

#### Scenario: Dispositivo fixado perdido e recuperado

- **WHEN** um dispositivo fixado é desconectado e reconectado dentro de 30 segundos
- **THEN** a captura é reaberta no mesmo dispositivo
- **AND** o intervalo é preenchido com silêncio e registrado nos metadados

#### Scenario: Dispositivo fixado não retorna dentro do prazo

- **WHEN** um dispositivo fixado permanece indisponível além de 30 segundos
- **THEN** a trilha afetada é marcada como incompleta nos metadados
- **AND** a outra trilha continua sendo gravada normalmente
- **AND** o usuário é notificado da perda
- **AND** se o dispositivo voltar antes do fim da gravação, a trilha volta a ser gravada nele

#### Scenario: Mudança de padrão com trilha fixada

- **WHEN** o dispositivo padrão do papel muda e a trilha tem dispositivo fixado
- **THEN** nenhuma migração ocorre
- **AND** a gravação continua no dispositivo fixado

#### Scenario: Nenhum dispositivo padrão utilizável para o papel

- **WHEN** o único dispositivo de saída é removido durante uma gravação com política de seguir o padrão
- **THEN** o sistema tenta obter um padrão utilizável
- **AND** passados 30 segundos sem sucesso, a trilha do sistema é marcada como incompleta e o usuário é avisado
- **AND** a trilha de entrada continua sendo gravada e a sessão prossegue até o comando do usuário

#### Scenario: Headset que volta depois do prazo

- **WHEN** um headset é desconectado durante uma gravação e volta a ficar disponível um minuto depois
- **THEN** as trilhas que o usavam voltam a ser gravadas quando ele volta
- **AND** o minuto sem dispositivo é preenchido com silêncio e registrado como uma única lacuna

#### Scenario: Abertura que demora numa trilha

- **WHEN** a abertura do dispositivo de uma trilha fica travada durante a recuperação
- **THEN** a perda de dispositivo da outra trilha continua sendo detectada em no máximo 5 segundos

### Requirement: Escrita incremental e comportamento sob falha de escrita

O sistema SHALL escrever o áudio capturado em disco de forma incremental durante a gravação, e MUST NOT manter a gravação inteira apenas em memória até o encerramento.

Se o processo for encerrado abruptamente, o áudio escrito até aquele instante SHALL permanecer legível e utilizável.

A perda potencial em um encerramento abrupto é exatamente o áudio ainda não escrito. Por isso a garantia de durabilidade é expressa em função da acumulação pendente, e não como um número isolado:

- o sistema SHALL forçar a durabilidade do que foi capturado ao menos a cada **2 segundos** de áudio por trilha;
- em operação normal, definida como acumulação pendente abaixo de 2 segundos, a perda máxima em encerramento abrupto SHALL ser de 2 segundos de áudio por trilha;
- quando a acumulação ultrapassa esse patamar, a perda potencial cresce junto com ela, e é por isso que os patamares abaixo existem.

O sistema SHALL monitorar continuamente a acumulação de áudio pendente de escrita, com o seguinte comportamento por trilha:

- acima de 2 segundos acumulados, emitir aviso ao usuário e registrar nos metadados, informando que a perda potencial em caso de queda aumentou;
- acima de 10 segundos acumulados, marcar a sessão como degradada nos metadados;
- acima de 60 segundos acumulados, encerrar a gravação de forma limpa, por impossibilidade de acompanhar a taxa de captura.

Quando o escritor deixar de avançar por completo — acumulação crescente sem nenhuma escrita concluída por mais de 10 segundos —, o sistema SHALL encerrar a gravação de forma limpa imediatamente, sem aguardar o patamar de 60 segundos, porque nesse caso a acumulação restante não será escrita em nenhuma hipótese.

O sistema SHALL registrar nos metadados a acumulação pendente máxima observada durante a sessão, de modo que a perda potencial daquela gravação seja conhecida depois.

Blocos que não puderem ser escritos SHALL ser contabilizados e registrados nos metadados com instante e duração. O sistema MUST NOT descartar áudio silenciosamente.

Um bloco que não puder ser convertido ou posicionado na linha do tempo MUST NOT interromper a escrita da trilha: ele é contabilizado como bloco não escrito, e os blocos seguintes continuam sendo escritos. Numa troca de dispositivo, os blocos do dispositivo anterior SHALL ser escritos no formato dele, e os do novo dispositivo, no formato novo.

Quando a escrita falhar por falta de espaço em disco ou por recusa do sistema de arquivos, o sistema SHALL encerrar a gravação de forma limpa, preservando o áudio já escrito, e SHALL informar a causa ao usuário.

O sistema SHALL encerrar a gravação de forma limpa quando o espaço livre no diretório de dados cair abaixo de 500 MB, antes que a escrita passe a falhar.

#### Scenario: Processo encerrado abruptamente durante a gravação

- **WHEN** o processo é morto após quarenta minutos de gravação, com a acumulação pendente em operação normal
- **THEN** os arquivos de áudio das duas trilhas contêm o áudio capturado, com perda de no máximo 2 segundos por trilha
- **AND** os arquivos são legíveis por ferramentas de áudio comuns

#### Scenario: Escritor paralisado

- **WHEN** nenhuma escrita é concluída por mais de 10 segundos enquanto a acumulação cresce
- **THEN** a gravação é encerrada de forma limpa imediatamente, sem aguardar o patamar de 60 segundos
- **AND** a acumulação pendente máxima observada é registrada nos metadados

#### Scenario: Escritor não acompanha a taxa de captura

- **WHEN** a acumulação pendente de escrita de uma trilha ultrapassa 10 segundos
- **THEN** a sessão é marcada como degradada nos metadados
- **AND** o usuário é avisado

#### Scenario: Troca de dispositivo com formatos diferentes

- **WHEN** uma trilha troca de um dispositivo de 16 kHz mono para um de 48 kHz estéreo com blocos do anterior ainda por escrever
- **THEN** os blocos do dispositivo anterior são escritos no formato dele
- **AND** a trilha continua sendo escrita com o áudio do novo dispositivo

#### Scenario: Bloco que não pode ser convertido

- **WHEN** um bloco capturado não pode ser convertido para o formato de gravação
- **THEN** o bloco é contabilizado nos metadados como não escrito, com instante e duração
- **AND** os blocos seguintes da trilha continuam sendo escritos

#### Scenario: Disco enche durante a gravação

- **WHEN** o espaço livre no diretório de dados cai abaixo de 500 MB durante uma gravação
- **THEN** a gravação é encerrada de forma limpa
- **AND** o áudio já escrito é preservado e a sessão é submetida para transcrição
- **AND** a causa é informada ao usuário

#### Scenario: Escrita recusada pelo sistema de arquivos

- **WHEN** a escrita passa a falhar por recusa do sistema de arquivos
- **THEN** a gravação é encerrada de forma limpa preservando o que já foi escrito
- **AND** a causa é informada ao usuário e registrada nos metadados
