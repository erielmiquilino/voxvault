# audio-capture Specification

## Purpose
Capturar simultaneamente a voz do usuário e o áudio que o sistema operacional está reproduzindo, em duas trilhas separadas e alinhadas na mesma linha de tempo, sem exigir cabo de áudio virtual, bot na reunião ou qualquer cooperação da plataforma de videoconferência. A separação física das trilhas é o mecanismo que atribui falas sem recorrer a um modelo de diarização.

## Requirements

### Requirement: Captura simultânea em trilhas separadas

O sistema SHALL capturar, durante uma gravação, duas trilhas independentes: uma proveniente de um dispositivo de entrada de áudio e outra proveniente da mistura reproduzida por um dispositivo de saída.

As trilhas SHALL ser gravadas em arquivos distintos e MUST NOT ser mixadas entre si em momento algum.

A captura do áudio reproduzido MUST NOT exigir instalação de dispositivo de áudio virtual nem qualquer configuração dentro da plataforma de videoconferência.

A trilha do sistema SHALL conter toda a mistura reproduzida pelo dispositivo de saída capturado, e não apenas o áudio da plataforma de reunião. O sistema MUST NOT prometer, em nenhuma parte da interface ou da documentação, isolar a reunião de outros sons reproduzidos no mesmo dispositivo.

Quando a plataforma de reunião reproduzir áudio num dispositivo de saída diferente do capturado, a trilha do sistema não conterá a reunião. O sistema SHALL avisar o usuário, ao iniciar, qual dispositivo de saída está sendo capturado.

#### Scenario: Gravação de uma reunião com fala dos dois lados

- **WHEN** o usuário inicia uma gravação, a reunião reproduz no dispositivo de saída capturado, e tanto ele quanto os outros participantes falam
- **THEN** a trilha de entrada contém a voz captada pelo dispositivo de entrada
- **AND** a trilha do sistema contém a mistura reproduzida pelo dispositivo de saída capturado, incluindo a reunião
- **AND** os dois arquivos existem separadamente ao fim da gravação

#### Scenario: Outro aplicativo reproduzindo som durante a gravação

- **WHEN** um vídeo é reproduzido em outro aplicativo, no mesmo dispositivo de saída, durante uma gravação
- **THEN** esse áudio aparece na trilha do sistema
- **AND** a transcrição resultante o atribui aos demais participantes, por ser indistinguível na origem física

#### Scenario: Reunião em dispositivo de saída diferente do capturado

- **WHEN** a plataforma de reunião reproduz num dispositivo de saída que não é o capturado
- **THEN** a trilha do sistema não contém o áudio da reunião
- **AND** o aviso emitido no início identificou qual dispositivo estava sendo capturado

#### Scenario: Independência da plataforma de reunião

- **WHEN** o usuário grava reuniões em plataformas diferentes reproduzindo no dispositivo capturado
- **THEN** a captura funciona de forma idêntica em todas elas
- **AND** nenhuma configuração específica de plataforma é exigida

### Requirement: Supressão de realimentação pela própria reprodução

O sistema MUST NOT reproduzir áudio de reuniões gravadas enquanto houver uma gravação ativa, porque essa reprodução seria capturada pela trilha do sistema e incorporada à gravação em curso como se fosse fala de participante.

As ações de reprodução SHALL ficar indisponíveis durante uma gravação ativa, com a causa explicada.

#### Scenario: Tentativa de reproduzir uma reunião durante uma gravação

- **WHEN** o usuário tenta reproduzir o áudio de uma reunião antiga com uma gravação ativa
- **THEN** a reprodução não ocorre
- **AND** a causa é explicada ao usuário

### Requirement: Dados exigidos da interface de captura

A interface de captura usada pelo sistema SHALL fornecer, para cada pacote de áudio capturado:

1. a **posição de dispositivo** do pacote, isto é, a contagem de quadros desde o início do fluxo do ponto de vista do dispositivo;
2. um **timestamp de relógio de alta resolução** correspondente à aquisição desse pacote;
3. a **sinalização de descontinuidade** emitida pelo sistema operacional quando houve perda de pacotes entre a entrega anterior e a atual.

O sistema MUST NOT substituir a posição de dispositivo ou o timestamp de aquisição por um relógio lido no momento em que o pacote foi entregue à aplicação, ainda que somado a uma estimativa de latência. Um valor assim carrega o atraso de escalonamento que o alinhamento precisa eliminar, e satisfaria a forma do requisito contradizendo o seu propósito.

Antes de qualquer implementação do alinhamento, o sistema SHALL demonstrar por medição que a interface escolhida fornece os três dados, submetendo-a a carga de processador durante a captura e verificando que os instantes reportados **não** se deslocam com o atraso de entrega.

Uma interface que não forneça os três dados MUST NOT ser adotada como backend de captura.

#### Scenario: Validação do backend sob carga

- **WHEN** a interface de captura candidata é exercitada sob carga de processador durante uma captura
- **THEN** os instantes de aquisição reportados permanecem estáveis em relação ao áudio capturado
- **AND** não acompanham o atraso de entrega dos pacotes

#### Scenario: Descontinuidade sinalizada pelo sistema operacional

- **WHEN** o sistema operacional sinaliza perda de pacotes durante a captura
- **THEN** a sinalização é recebida pelo sistema
- **AND** a lacuna correspondente é registrada nos metadados com instante e duração

#### Scenario: Interface sem os dados exigidos

- **WHEN** uma interface candidata não fornece posição de dispositivo, timestamp de aquisição ou sinalização de descontinuidade
- **THEN** ela é rejeitada como backend de captura
- **AND** a rejeição e seu motivo são registrados na documentação do projeto

### Requirement: Alinhamento temporal das trilhas

O sistema SHALL posicionar cada bloco de áudio na linha de tempo pela **posição de dispositivo** do pacote, ancorada no timestamp de aquisição, e não pelo instante em que o bloco foi entregue à aplicação.

No início da sessão, o sistema SHALL estabelecer uma referência temporal comum registrando, para cada trilha, o par formado pela posição de dispositivo inicial e seu timestamp de aquisição. Todos os instantes subsequentes daquela trilha SHALL ser derivados dessa âncora, e as duas trilhas SHALL ser expressas na mesma referência.

Quando uma trilha não entregar pacote algum no início da sessão — situação normal no loopback, que fica ocioso enquanto nada é reproduzido —, sua âncora SHALL ser estabelecida no primeiro pacote recebido, e o intervalo entre o início da sessão e esse pacote SHALL ser preenchido com silêncio.

O sistema SHALL considerar que houve lacuna real de captura quando a posição de dispositivo avançar mais do que o número de quadros efetivamente entregues, ou quando o sistema operacional sinalizar descontinuidade. Nesses casos SHALL preencher a diferença com silêncio e registrar a lacuna nos metadados com instante e duração.

O sistema MUST NOT preencher silêncio por divergência inferior a 200 ms entre a posição esperada e a observada, de modo que jitter normal de entrega não produza inserção alguma.

Quando dois pacotes consecutivos indicarem posições sobrepostas, o sistema SHALL descartar a porção já escrita e escrever apenas o excedente, e MUST NOT recuar a posição de escrita nem duplicar áudio.

Quando a posição de dispositivo ou o timestamp forem inválidos para um pacote, o sistema SHALL derivar a posição da contagem contínua de quadros desde a última âncora válida, SHALL registrar a ocorrência nos metadados, e SHALL restabelecer a âncora no próximo pacote válido.

#### Scenario: Período prolongado sem áudio reproduzido

- **WHEN** ninguém fala na reunião por dois minutos e o dispositivo de saída fica ocioso
- **THEN** a trilha do sistema recebe silêncio correspondente a esse período
- **AND** a lacuna é registrada nos metadados com instante e duração
- **AND** uma fala que ocorre após o silêncio aparece no mesmo instante nas duas trilhas

#### Scenario: Atraso de entrega sob carga do sistema

- **WHEN** o sistema está sob carga e os pacotes de uma trilha são entregues com atraso, sem perda de captura
- **THEN** a posição de dispositivo não indica avanço além dos quadros entregues
- **AND** nenhum silêncio é inserido na trilha
- **AND** o áudio permanece contínuo e na posição correta

#### Scenario: Loopback ocioso no início da sessão

- **WHEN** a gravação inicia e nada é reproduzido nos primeiros trinta segundos
- **THEN** a âncora da trilha do sistema é estabelecida no primeiro pacote recebido
- **AND** os trinta segundos iniciais são preenchidos com silêncio
- **AND** o áudio subsequente fica alinhado com a trilha de entrada

#### Scenario: Jitter abaixo do limiar

- **WHEN** a divergência entre posição esperada e observada é inferior a 200 ms
- **THEN** nenhum silêncio é inserido

#### Scenario: Pacotes com posições sobrepostas

- **WHEN** dois pacotes consecutivos indicam posições que se sobrepõem
- **THEN** apenas o excedente é escrito
- **AND** a posição de escrita não recua e nenhum áudio é duplicado

#### Scenario: Timestamp inválido em um pacote

- **WHEN** a interface de captura reporta posição ou timestamp inválidos para um pacote
- **THEN** a posição é derivada da contagem contínua de quadros desde a última âncora válida
- **AND** a ocorrência é registrada nos metadados
- **AND** a âncora é restabelecida no próximo pacote válido

#### Scenario: Período prolongado sem áudio reproduzido

- **WHEN** ninguém fala na reunião por dois minutos e o dispositivo de saída fica ocioso
- **THEN** a trilha do sistema recebe silêncio correspondente a esse período
- **AND** a lacuna é registrada nos metadados com instante e duração
- **AND** uma fala que ocorre após o silêncio aparece no mesmo instante nas duas trilhas

#### Scenario: Atraso de entrega sob carga do sistema

- **WHEN** o sistema está sob carga e os blocos de uma trilha são entregues com atraso, sem perda de áudio na captura
- **THEN** nenhum silêncio é inserido na trilha
- **AND** o áudio permanece contínuo e na posição correta

#### Scenario: Instante de aquisição indisponível

- **WHEN** a interface de captura não fornece instante de aquisição válido para um bloco
- **THEN** a posição é derivada da contagem contínua de amostras desde a última referência válida
- **AND** a ocorrência é registrada nos metadados

### Requirement: Verificação numérica do alinhamento

O erro de alinhamento entre as trilhas SHALL ser verificado por medição do deslocamento de um evento sonoro conhecido, presente nas duas trilhas, e não por comparação das durações dos arquivos.

O erro medido SHALL ser de no máximo 250 ms ao final de uma gravação de 60 minutos.

O sistema SHALL medir o desvio estimado ao longo da gravação e SHALL marcar a sessão com aviso quando ele ultrapassar 500 ms.

Comparar as durações finais das duas trilhas MUST NOT ser aceito como evidência de alinhamento, porque arquivos de mesma duração podem estar deslocados entre si.

#### Scenario: Medição do alinhamento com evento conhecido

- **WHEN** uma gravação de 60 minutos contém um evento sonoro identificável nas duas trilhas
- **THEN** o deslocamento medido desse evento entre as trilhas é de no máximo 250 ms

#### Scenario: Desvio acima do limiar de aviso

- **WHEN** o desvio estimado durante a gravação ultrapassa 500 ms
- **THEN** a sessão é marcada com aviso nos metadados
- **AND** o aviso é apresentado ao usuário junto com a transcrição resultante

### Requirement: Atraso de início da captura

O instante de início da sessão SHALL ser aquele em que **ambos os fluxos estão abertos e armados**, expresso na referência temporal comum — e não o instante do comando, nem o da primeira amostra recebida.

Definir o início pela primeira amostra recebida seria incorreto: o fluxo de loopback não entrega pacote algum enquanto nada é reproduzido, de modo que a trilha do sistema pode permanecer minutos sem entregar nada após estar corretamente aberta.

O atraso entre o comando e o instante de início da sessão SHALL respeitar os seguintes tetos:

| Situação | Teto |
|---|---|
| Nenhuma transcrição em execução | 1500 ms |
| Transcrição em execução, que precisa ser interrompida e ter a memória de GPU liberada | 2000 ms |

Quando o atraso exceder o teto aplicável, o sistema SHALL registrar a ocorrência nos metadados com o valor medido e com a situação em que ocorreu.

#### Scenario: Início de gravação sem transcrição em andamento

- **WHEN** o usuário aciona iniciar gravação sem transcrição em execução
- **THEN** ambos os fluxos estão abertos e armados em no máximo 1500 ms
- **AND** o instante de início da sessão é o do armamento dos dois fluxos

#### Scenario: Início de gravação com transcrição em andamento

- **WHEN** o usuário aciona iniciar gravação com uma transcrição em execução
- **THEN** ambos os fluxos estão abertos e armados em no máximo 2000 ms, incluindo a interrupção e a liberação de memória de GPU

#### Scenario: Loopback sem entregar pacotes no início

- **WHEN** o fluxo de loopback está aberto e armado mas nada é reproduzido por trinta segundos
- **THEN** o instante de início da sessão já foi estabelecido no armamento
- **AND** a ausência de pacotes não é contabilizada como atraso de início

### Requirement: Formato de gravação

O sistema SHALL gravar ambas as trilhas convertidas para taxa de amostragem de 16 kHz, um canal e profundidade de 16 bits, independentemente do formato nativo dos dispositivos.

Ao encerrar a gravação, o sistema SHALL comprimir as trilhas sem perda adicional de informação.

O sistema SHALL permitir, por configuração, preservar também o áudio na taxa e no número de canais nativos do dispositivo, desativado por padrão.

Quando o formato nativo de um dispositivo mudar durante a gravação, o sistema SHALL continuar produzindo saída no formato de gravação e SHALL registrar a mudança nos metadados.

#### Scenario: Dispositivos com formatos nativos diferentes

- **WHEN** o dispositivo de entrada opera a 44,1 kHz mono e o de saída a 48 kHz estéreo
- **THEN** ambas as trilhas são gravadas a 16 kHz, um canal, 16 bits
- **AND** nenhuma diferença de taxa entre os dispositivos provoca desalinhamento entre as trilhas

#### Scenario: Mudança de formato durante a gravação

- **WHEN** o formato nativo de um dispositivo muda durante a gravação
- **THEN** a trilha continua sendo gravada no formato de gravação sem interrupção
- **AND** a mudança é registrada nos metadados

#### Scenario: Preservação de áudio em qualidade nativa

- **WHEN** a opção de preservar qualidade nativa está ativada
- **THEN** o sistema grava adicionalmente as trilhas no formato nativo dos dispositivos
- **AND** a transcrição continua usando as trilhas de 16 kHz

### Requirement: Seleção de dispositivos e papel do padrão

O sistema SHALL distinguir explicitamente os papéis de dispositivo padrão que o sistema operacional mantém separadamente, e SHALL usar por padrão o papel de **comunicações**, tanto para a entrada quanto para a saída capturada, por ser o papel que as plataformas de videoconferência utilizam.

O sistema SHALL permitir configurar o papel usado, entre comunicações e multimídia, independentemente para cada trilha.

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

#### Scenario: Dispositivo fixado indisponível

- **WHEN** o usuário inicia uma gravação com um dispositivo fixado que foi desconectado
- **THEN** a gravação não inicia
- **AND** o erro nomeia o dispositivo ausente e lista os disponíveis

### Requirement: Reação a mudança de dispositivo durante a gravação

O sistema SHALL detectar, em no máximo 5 segundos, tanto a perda do dispositivo em uso quanto a mudança do dispositivo padrão do papel configurado, ainda que o dispositivo anterior continue disponível e o fluxo de captura não apresente erro.

Para uma trilha com política de seguir o padrão, o sistema SHALL migrar a captura para o novo dispositivo padrão e prosseguir a gravação. Quando não existir dispositivo padrão utilizável para o papel, ou quando a abertura do novo padrão falhar, o sistema SHALL tentar novamente por até **30 segundos** e, esgotado esse prazo, SHALL encerrar aquela trilha marcando-a como incompleta.

Para uma trilha com dispositivo fixado, o sistema MUST NOT migrar. Se o dispositivo fixado for perdido, o sistema SHALL tentar reabri-lo por até 30 segundos e, esgotado esse prazo, SHALL encerrar aquela trilha marcando-a como incompleta.

Em ambas as políticas, o prazo de 30 segundos é de **recuperação**, e é distinto do prazo de 5 segundos de **detecção**. O intervalo total não capturado corresponde à soma dos dois e SHALL ser registrado como uma única lacuna nos metadados.

O encerramento de uma trilha por esgotamento do prazo MUST NOT encerrar a gravação: a outra trilha prossegue, e a sessão é finalizada normalmente ao comando do usuário.

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
- **THEN** a trilha afetada é encerrada e marcada como incompleta nos metadados
- **AND** a outra trilha continua sendo gravada normalmente
- **AND** o usuário é notificado da perda

#### Scenario: Mudança de padrão com trilha fixada

- **WHEN** o dispositivo padrão do papel muda e a trilha tem dispositivo fixado
- **THEN** nenhuma migração ocorre
- **AND** a gravação continua no dispositivo fixado

#### Scenario: Nenhum dispositivo padrão utilizável para o papel

- **WHEN** o único dispositivo de saída é removido durante uma gravação com política de seguir o padrão
- **THEN** o sistema tenta obter um padrão utilizável por até 30 segundos
- **AND** esgotado o prazo, a trilha do sistema é encerrada e marcada como incompleta
- **AND** a trilha de entrada continua sendo gravada e a sessão prossegue até o comando do usuário

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

#### Scenario: Disco enche durante a gravação

- **WHEN** o espaço livre no diretório de dados cai abaixo de 500 MB durante uma gravação
- **THEN** a gravação é encerrada de forma limpa
- **AND** o áudio já escrito é preservado e a sessão é submetida para transcrição
- **AND** a causa é informada ao usuário

#### Scenario: Escrita recusada pelo sistema de arquivos

- **WHEN** a escrita passa a falhar por recusa do sistema de arquivos
- **THEN** a gravação é encerrada de forma limpa preservando o que já foi escrito
- **AND** a causa é informada ao usuário e registrada nos metadados

### Requirement: Detecção de trilha silenciosa e risco de eco

O sistema SHALL monitorar o nível de áudio de cada trilha durante a gravação e SHALL avisar o usuário quando uma trilha permanecer inteiramente silenciosa além de um limiar de tempo configurável, com padrão de 120 segundos, já que isso indica microfone mudo ou dispositivo de captura incorreto.

O sistema SHALL avisar o usuário, ao iniciar a gravação, quando o dispositivo de saída em uso não aparentar ser um fone, porque nesse caso o microfone capta o áudio dos outros participantes e o mesmo trecho passa a aparecer duplicado nas duas trilhas.

O aviso de eco MUST NOT impedir o início da gravação.

#### Scenario: Microfone mudo durante a gravação

- **WHEN** a trilha de entrada permanece silenciosa por mais de 120 segundos
- **THEN** o usuário é avisado de que o microfone pode estar mudo
- **AND** a gravação continua

#### Scenario: Gravação iniciada com saída em caixas de som

- **WHEN** o usuário inicia uma gravação com o dispositivo de saída que não aparenta ser fone
- **THEN** o sistema avisa sobre o risco de duplicação por eco
- **AND** a gravação inicia normalmente
- **AND** o aviso é registrado nos metadados da sessão
