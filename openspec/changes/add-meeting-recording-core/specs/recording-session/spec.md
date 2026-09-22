## Purpose

Governar o ciclo de vida de uma gravação — iniciar, pausar, retomar, encerrar — junto com os metadados que a descrevem, o layout em disco que a torna autocontida, o processo que mantém a captura viva entre comandos, e a recuperação de sessões interrompidas por queda do processo ou da máquina.

## ADDED Requirements

### Requirement: Serviço residente proprietário da captura e da fila

O sistema SHALL executar um serviço residente que é o único proprietário da captura de áudio e da fila de transcrição.

Todas as demais superfícies — comandos de linha de comando e, quando existir, a aplicação de desktop — SHALL operar como clientes desse serviço, e MUST NOT abrir dispositivos de áudio nem executar transcrição por conta própria.

O serviço SHALL admitir apenas uma instância por diretório de dados. Uma segunda tentativa de iniciá-lo SHALL conectar-se à instância existente em vez de criar outra.

Adicionalmente, e independentemente do diretório de dados, o sistema SHALL garantir exclusividade **global na máquina** para os recursos que não admitem dois donos: a captura de áudio e a execução de transcrição. Quando um serviço já detém a captura ou executa transcrição, um segundo serviço apontado para outro diretório de dados MUST NOT iniciar gravação nem transcrição, e SHALL falhar com erro que identifica o serviço detentor e o diretório de dados dele.

O sistema SHALL implementar essa exclusividade por um mecanismo de escopo de máquina, e MUST NOT derivá-la do diretório de dados, sob pena de duas instâncias legítimas disputarem os mesmos dispositivos e a mesma GPU sem que nenhuma perceba.

Cada superfície SHALL localizar a instância em execução por um ponto de encontro de escopo de usuário, independente do diretório de trabalho, que publica o endereço do serviço e o segredo da sessão corrente. Toda superfície — comandos de linha de comando e aplicação de desktop — SHALL usar o mesmo ponto de encontro; o segredo MUST NOT ser publicado apenas para quem iniciou o serviço.

O serviço SHALL ser iniciado sob demanda pela primeira superfície que precisar dele, e SHALL permanecer em execução enquanto houver gravação ativa, sessões pendentes na fila, ou **ao menos um cliente conectado**. Sem gravação ativa, com a fila vazia e sem clientes conectados, o serviço SHALL encerrar-se após um período de ociosidade configurável, com padrão de 300 segundos.

Ao encerrar-se por ociosidade, o serviço SHALL remover sua publicação do ponto de encontro antes de terminar, de modo que a próxima superfície o inicie em vez de tentar conectar-se a um endereço morto.

O encerramento do serviço SHALL terminar toda a árvore de processos que ele criou, sem deixar processo órfão. O sistema MUST NOT presumir que terminar o processo do serviço encerra seus descendentes.

Este requisito existe porque iniciar e encerrar uma gravação são invocações distintas: sem um proprietário residente, não há nada que mantenha a captura viva entre os dois comandos.

#### Scenario: Comandos sucessivos de linha de comando

- **WHEN** o usuário executa o comando de iniciar gravação e, minutos depois, o de encerrar
- **THEN** o serviço residente foi iniciado pelo primeiro comando e manteve a captura entre os dois
- **AND** o segundo comando é atendido pela mesma instância do serviço

#### Scenario: Segunda superfície conectando-se ao serviço existente

- **WHEN** uma segunda superfície precisa do serviço enquanto ele já está em execução
- **THEN** ela se conecta à instância existente
- **AND** nenhuma segunda instância é criada

#### Scenario: Ociosidade sem trabalho pendente e sem clientes

- **WHEN** o serviço permanece sem gravação ativa, com a fila vazia e sem nenhum cliente conectado, além do período de ociosidade
- **THEN** o serviço encerra-se
- **AND** sua publicação é removida do ponto de encontro antes de terminar
- **AND** nenhum processo descendente permanece em execução

#### Scenario: Cliente conectado impede o encerramento por ociosidade

- **WHEN** a aplicação de desktop permanece aberta e conectada, sem gravação nem fila, além do período de ociosidade
- **THEN** o serviço permanece em execução
- **AND** encerra-se pela política de ociosidade somente depois que o último cliente se desconectar

#### Scenario: Segundo serviço com outro diretório de dados

- **WHEN** um serviço já está gravando e outro, apontado para um diretório de dados diferente, recebe um comando de iniciar gravação
- **THEN** a operação falha identificando o serviço detentor e o diretório de dados dele
- **AND** a gravação em andamento não é afetada

#### Scenario: Superfícies localizam a mesma instância

- **WHEN** a aplicação de desktop é aberta depois de o serviço ter sido iniciado por um comando de linha de comando
- **THEN** ela localiza o endereço e o segredo pelo ponto de encontro de escopo de usuário
- **AND** conecta-se à instância existente sem iniciar outra

#### Scenario: Encerramento com trabalho pendente

- **WHEN** o período de ociosidade é atingido mas há sessões pendentes na fila
- **THEN** o serviço permanece em execução até concluí-las

#### Scenario: Encerramento do serviço com descendentes ativos

- **WHEN** o serviço é encerrado enquanto executa um processo de transcrição
- **THEN** toda a árvore de processos é terminada
- **AND** nenhum processo órfão permanece após o encerramento

### Requirement: Ciclo de vida da gravação

O sistema SHALL permitir iniciar uma gravação, que passa a capturar até ser explicitamente encerrada.

Ao iniciar, o sistema SHALL criar a sessão em disco e registrá-la no armazenamento antes de capturar a primeira amostra, de modo que uma sessão exista mesmo que a captura falhe em seguida.

Ao encerrar, o sistema SHALL finalizar os arquivos de áudio, registrar a duração total e submeter a sessão para transcrição.

#### Scenario: Gravação iniciada e encerrada normalmente

- **WHEN** o usuário inicia uma gravação, espera, e a encerra
- **THEN** a sessão existe no armazenamento com instantes de início e fim e duração total
- **AND** as duas trilhas de áudio estão finalizadas e legíveis
- **AND** a sessão é submetida para transcrição

#### Scenario: Falha ao abrir a captura

- **WHEN** o início da gravação falha porque um dispositivo não pôde ser aberto
- **THEN** a sessão é marcada como falha com o motivo registrado
- **AND** nenhum arquivo de áudio vazio é deixado para trás

### Requirement: Finalização retomável e ponto de durabilidade

A finalização de uma sessão compreende quatro passos: fechar os arquivos de áudio capturados, comprimi-los sem perdas, atualizar os metadados e a duração, e submeter a sessão para transcrição.

O sistema SHALL tornar cada passo idempotente e retomável, e SHALL registrar em disco o progresso da finalização, de modo que uma interrupção em qualquer ponto seja recuperável na próxima inicialização.

O áudio original capturado MUST NOT ser removido antes que o arquivo comprimido correspondente tenha sido validado como legível e de duração equivalente.

Quando a compressão falhar, o sistema SHALL preservar o áudio original, marcar a sessão informando que o áudio permaneceu no formato não comprimido, e prosseguir com a transcrição normalmente.

A sessão SHALL ser considerada durável a partir do momento em que os arquivos de áudio estão fechados e os metadados gravados; a submissão para transcrição SHALL ser recuperável a partir do estado persistido, e não depender de o processo permanecer vivo.

#### Scenario: Interrupção entre comprimir e enfileirar

- **WHEN** o processo é encerrado depois de comprimir o áudio e antes de submeter a sessão para transcrição
- **THEN** na próxima inicialização a sessão é detectada como finalização pendente
- **AND** a submissão para transcrição é concluída sem refazer a compressão

#### Scenario: Falha na compressão

- **WHEN** a compressão do áudio de uma sessão falha
- **THEN** o áudio original é preservado e permanece utilizável
- **AND** a sessão registra que o áudio permaneceu não comprimido
- **AND** a transcrição prossegue normalmente

#### Scenario: Interrupção durante a compressão

- **WHEN** o processo é encerrado no meio da compressão de uma trilha
- **THEN** o áudio original ainda existe
- **AND** a compressão é refeita do início na próxima inicialização

### Requirement: Pausa e retomada

O sistema SHALL permitir pausar uma gravação em andamento e retomá-la posteriormente dentro da mesma sessão.

Durante a pausa, o sistema MUST NOT capturar áudio de nenhuma das trilhas.

O período pausado SHALL ser preenchido com silêncio nas duas trilhas, preservando o alinhamento da linha de tempo, e cada intervalo de pausa SHALL ser registrado nos metadados com seu instante inicial e sua duração.

#### Scenario: Pausa durante um intervalo da reunião

- **WHEN** o usuário pausa a gravação por cinco minutos e depois retoma
- **THEN** nenhum áudio é capturado durante a pausa
- **AND** as duas trilhas contêm cinco minutos de silêncio naquele intervalo
- **AND** o intervalo de pausa consta nos metadados da sessão

#### Scenario: Gravação encerrada enquanto pausada

- **WHEN** o usuário encerra a gravação sem retomá-la após uma pausa
- **THEN** a sessão é finalizada normalmente
- **AND** a duração total exclui o período final pausado

### Requirement: Suspensão e retomada do sistema operacional

O sistema operacional concede um prazo muito curto — da ordem de dois segundos — para reagir ao aviso de suspensão. A finalização completa inclui compressão e não cabe nesse prazo, de modo que tentá-la inteira significaria perder a gravação.

Ao ser notificado da suspensão iminente durante uma gravação ativa, o sistema SHALL executar apenas o **mínimo durável**, nesta ordem: interromper a captura, forçar a escrita do áudio pendente, fechar os arquivos, e persistir os metadados com a duração real e o motivo de encerramento por suspensão. O sistema MUST NOT executar compressão, geração de exportações nem qualquer trabalho prolongado nesse ponto.

O mínimo durável SHALL ser concluído em no máximo **1000 ms**, e o sistema SHALL ser projetado para que esse prazo seja alcançável — o que exige que a acumulação pendente de escrita esteja limitada, como já especificado.

Após o mínimo durável, a sessão fica em estado de finalização pendente. Os passos restantes — compressão, validação e submissão para transcrição — SHALL ser concluídos na retomada, pelo mesmo processo se ele tiver sobrevivido à suspensão, ou pela recuperação de sessão interrompida na próxima inicialização.

Ao retomar da suspensão, o sistema MUST NOT reabrir automaticamente a gravação encerrada, e SHALL informar ao usuário que a sessão foi encerrada por suspensão do sistema.

#### Scenario: Suspensão com processo sobrevivente

- **WHEN** o sistema operacional entra em suspensão com uma gravação ativa e o processo sobrevive à retomada
- **THEN** o mínimo durável é concluído em no máximo 1000 ms antes da suspensão
- **AND** a compressão e a submissão para transcrição são concluídas após a retomada pelo mesmo processo
- **AND** os metadados registram o encerramento por suspensão

#### Scenario: Suspensão com processo encerrado

- **WHEN** o sistema operacional entra em suspensão com uma gravação ativa e o processo não sobrevive à retomada
- **THEN** o áudio e os metadados persistidos pelo mínimo durável permanecem íntegros
- **AND** a sessão é detectada como finalização pendente na próxima inicialização e concluída a partir daí

#### Scenario: Retomada após suspensão

- **WHEN** o sistema retoma da suspensão
- **THEN** nenhuma gravação é reiniciada automaticamente
- **AND** o usuário é informado de que a sessão anterior foi encerrada por suspensão

### Requirement: Sessão de gravação exclusiva

O sistema MUST NOT permitir mais de uma gravação ativa simultaneamente.

Uma tentativa de iniciar uma gravação enquanto outra está ativa SHALL falhar com erro que identifica a sessão em andamento.

#### Scenario: Segunda gravação solicitada com uma em andamento

- **WHEN** o usuário tenta iniciar uma gravação enquanto outra está ativa
- **THEN** a operação falha identificando a sessão em andamento e seu instante de início
- **AND** a gravação em andamento não é afetada

### Requirement: Identificação e título da sessão

Cada sessão SHALL receber um identificador estável e imutável no momento da criação.

O sistema SHALL permitir informar um título ao iniciar a gravação e SHALL permitir renomeá-lo a qualquer momento depois.

Quando nenhum título é informado, o sistema SHALL atribuir um título derivado do instante de início.

Renomear uma sessão MUST NOT alterar seu identificador nem o caminho dos arquivos já gravados.

#### Scenario: Gravação iniciada sem título

- **WHEN** o usuário inicia uma gravação sem informar título
- **THEN** a sessão recebe um título derivado da data e hora de início

#### Scenario: Sessão renomeada após a gravação

- **WHEN** o usuário renomeia uma sessão já encerrada
- **THEN** o novo título passa a ser exibido
- **AND** o identificador e os caminhos em disco permanecem inalterados

### Requirement: Layout autocontido em disco

Cada sessão SHALL ocupar um diretório próprio sob o diretório de dados configurado, contendo as trilhas de áudio, os metadados da sessão e as transcrições geradas.

O diretório SHALL ser nomeado de forma estável, derivada do instante de início e do identificador da sessão, e MUST NOT depender do título, que é mutável.

Os metadados SHALL registrar, no mínimo: identificador, título, instantes de início e fim, duração, dispositivos utilizados com seus identificadores persistentes e papéis, latência informada por cada fluxo, intervalos de pausa, lacunas de captura, migrações e reaberturas de dispositivo, mudanças de formato, blocos não escritos, desvio de alinhamento medido, estado de degradação, motivo de encerramento, avisos emitidos e o identificador de motor e configuração que produziu cada revisão de transcrição.

#### Scenario: Diretório de sessão após uma gravação

- **WHEN** uma gravação é encerrada
- **THEN** existe um diretório contendo as duas trilhas de áudio e os metadados da sessão
- **AND** os metadados contêm todos os campos exigidos

#### Scenario: Sessão renomeada não move arquivos

- **WHEN** uma sessão é renomeada
- **THEN** o diretório em disco mantém seu nome original

### Requirement: Recuperação de sessão interrompida

Ao iniciar, o serviço SHALL identificar sessões que ficaram marcadas como ativas ou com finalização pendente sem processo correspondente em execução, e SHALL tratá-las como interrompidas.

Para cada sessão interrompida, o sistema SHALL retomar a finalização a partir do passo em que ela parou, registrar a duração real, marcar a sessão como recuperada nos metadados, e submetê-la para transcrição.

O sistema MUST NOT descartar áudio de uma sessão interrompida.

#### Scenario: Queda de energia durante a gravação

- **WHEN** a máquina desliga abruptamente durante uma gravação e o serviço é iniciado novamente
- **THEN** a sessão é detectada como interrompida
- **AND** o áudio escrito até a interrupção é finalizado e permanece utilizável
- **AND** a sessão é marcada como recuperada e submetida para transcrição

#### Scenario: Sessão interrompida sem áudio utilizável

- **WHEN** uma sessão interrompida contém áudio de duração desprezível
- **THEN** a sessão é marcada como falha em vez de recuperada
- **AND** o usuário é informado de que nada aproveitável foi gravado

### Requirement: Preservação do áudio

O sistema SHALL preservar o áudio de uma sessão indefinidamente após a transcrição, e MUST NOT apagá-lo automaticamente.

A remoção do áudio de uma sessão SHALL ocorrer apenas por ação explícita do usuário sobre aquela sessão, e somente quando a sessão estiver em estado **pronta**, **parcial** ou **falha**.

A remoção SHALL ser recusada, com a causa nomeada, quando a sessão estiver gravando, em finalização pendente, aguardando transcrição ou sendo transcrita — inclusive quando a transcrição em curso for um reprocessamento.

A validação desses estados SHALL residir no serviço residente, e não em cada superfície, de modo que a linha de comando e a interface gráfica obedeçam exatamente à mesma regra. Uma superfície MUST NOT permitir uma operação que o serviço recusaria.

Este requisito existe para permitir re-transcrever gravações antigas quando um motor melhor estiver disponível, e para permitir conferir o que foi efetivamente dito quando a transcrição estiver em dúvida.

#### Scenario: Áudio após transcrição bem-sucedida

- **WHEN** a transcrição de uma sessão termina com sucesso
- **THEN** as trilhas de áudio permanecem em disco inalteradas

#### Scenario: Remoção explícita do áudio de uma sessão pronta

- **WHEN** o usuário solicita explicitamente a remoção do áudio de uma sessão em estado pronta
- **THEN** as trilhas de áudio daquela sessão são removidas
- **AND** a transcrição e os metadados são preservados
- **AND** a sessão passa a indicar que não pode mais ser re-transcrita

#### Scenario: Remoção recusada durante transcrição

- **WHEN** o usuário solicita a remoção do áudio de uma sessão que está sendo transcrita
- **THEN** a operação é recusada com a causa nomeada
- **AND** o áudio e a transcrição em curso permanecem intactos

#### Scenario: Mesma regra nas duas superfícies

- **WHEN** a remoção é solicitada pela linha de comando e pela interface gráfica para uma sessão em estado que não a permite
- **THEN** ambas são recusadas pelo serviço com a mesma causa
