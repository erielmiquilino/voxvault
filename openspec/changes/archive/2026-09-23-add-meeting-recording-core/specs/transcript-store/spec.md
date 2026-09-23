## Purpose

Guardar as reuniões e seus segmentos transcritos de forma consultável, fundir as duas trilhas de captura numa linha de tempo única com atribuição de falante, e permitir busca textual sobre todo o histórico. É a fonte de verdade que a interface gráfica e o servidor MCP vão consumir nas fases seguintes.

## ADDED Requirements

### Requirement: Modelo de reunião e segmentos

O sistema SHALL armazenar cada sessão como uma reunião, com identificador estável, título, instantes de início e fim, duração, estado de processamento, origem (gravada ou importada) e caminho do seu diretório em disco.

O sistema SHALL armazenar cada trecho transcrito como um segmento pertencente a uma reunião e a uma revisão, com identificador estável, trilha de origem, atribuição de falante, instantes de início e fim em milissegundos relativos ao começo da reunião, e texto.

Os segmentos de uma reunião SHALL ter **ordenação total e determinística**, definida por instante de início, e desempatada sucessivamente por trilha de origem e por identificador do segmento quando os instantes coincidirem.

Duas consultas sucessivas sobre a mesma revisão SHALL devolver os segmentos exatamente na mesma ordem. O sistema MUST NOT depender de ordenação apenas por instante de início, porque segmentos das duas trilhas podem começar no mesmo milissegundo e uma ordem instável quebraria qualquer paginação construída sobre ela.

#### Scenario: Reunião transcrita com segmentos das duas trilhas

- **WHEN** uma reunião gravada termina de ser transcrita
- **THEN** a reunião existe no armazenamento com seus metadados
- **AND** seus segmentos incluem trechos das duas trilhas, cada um com sua trilha de origem registrada

#### Scenario: Recuperação ordenada dos segmentos

- **WHEN** os segmentos de uma reunião são recuperados
- **THEN** vêm ordenados por instante de início crescente
- **AND** pertencem todos à revisão ativa da reunião

### Requirement: Revisões de transcrição

Cada execução de transcrição de uma reunião SHALL produzir uma **revisão**, identificada e associada ao identificador de motor e configuração que a produziu, ao vocabulário usado e ao instante de conclusão.

O motor, a configuração e o vocabulário de uma revisão SHALL ser capturados **no início da tentativa** e SHALL permanecer imutáveis durante toda ela, ainda que a configuração do sistema mude no intervalo. Sem isso, a transcrição da segunda trilha poderia usar um motor diferente da primeira e a revisão registraria um único identificador que descreveria mal o próprio conteúdo.

Uma tentativa interrompida é descartada por inteiro; sua retomada é uma **tentativa nova**, que captura a configuração vigente naquele momento. O sistema MUST NOT retomar uma tentativa interrompida preservando a configuração antiga junto com segmentos novos.

Todo segmento SHALL pertencer a exatamente uma revisão. O sistema MUST NOT compor a linha de tempo de uma reunião com segmentos de revisões diferentes, porque isso misturaria resultados de motores distintos num mesmo texto sem que o leitor perceba.

Uma revisão SHALL tornar-se ativa de forma atômica, com os segmentos de todas as suas trilhas passando a valer de uma só vez. Enquanto não é publicada, a revisão em construção MUST NOT ser visível em consultas, busca ou exportação.

Uma revisão parcial — aquela em que ao menos uma trilha falhou — SHALL tornar-se ativa somente quando não existir revisão ativa anterior mais completa. Quando existir, a revisão parcial SHALL ser registrada como tentativa falha, a revisão anterior SHALL permanecer ativa, e o usuário SHALL ser informado de que o reprocessamento não substituiu o resultado existente.

Quando todas as trilhas falharem, nenhuma revisão SHALL ser publicada e a revisão ativa anterior, se houver, SHALL permanecer intacta.

O índice de busca SHALL ser atualizado na mesma transação que publica a revisão, de modo que nunca aponte para uma revisão que não é a ativa.

As exportações em arquivo são **artefatos derivados**, reconstruíveis a qualquer momento a partir do armazenamento, e não podem ser publicadas atomicamente junto com a revisão. Por isso cada arquivo de exportação SHALL registrar o identificador da revisão que o gerou, e o sistema SHALL regerar as exportações de uma reunião sempre que o identificador registrado nelas divergir da revisão ativa — inclusive na inicialização, ao detectar reuniões nessa condição.

Uma exportação cujo identificador de revisão diverge da revisão ativa MUST NOT ser apresentada ao usuário nem devolvida por qualquer superfície como se fosse corrente.

#### Scenario: Primeira transcrição de uma reunião

- **WHEN** as duas trilhas de uma reunião são transcritas com sucesso pela primeira vez
- **THEN** uma revisão é publicada contendo os segmentos das duas trilhas
- **AND** a revisão registra o identificador de motor e configuração que a produziu

#### Scenario: Reprocessamento bem-sucedido substitui a revisão anterior

- **WHEN** uma reunião já transcrita é reprocessada com outro motor e ambas as trilhas têm sucesso
- **THEN** a nova revisão torna-se ativa de uma só vez
- **AND** nenhum segmento da revisão anterior permanece na linha de tempo
- **AND** a busca e as exportações passam a refletir apenas a nova revisão

#### Scenario: Reprocessamento parcial com revisão completa existente

- **WHEN** uma reunião com revisão ativa completa é reprocessada e apenas uma das trilhas tem sucesso
- **THEN** a revisão anterior permanece ativa e intacta
- **AND** a tentativa é registrada como falha com o motivo
- **AND** o usuário é informado de que o resultado existente não foi substituído

#### Scenario: Primeira transcrição parcial

- **WHEN** a primeira transcrição de uma reunião tem sucesso em apenas uma trilha
- **THEN** a revisão parcial torna-se ativa, por não existir revisão anterior
- **AND** a reunião é marcada como transcrição parcial

#### Scenario: Falha em todas as trilhas no reprocessamento

- **WHEN** um reprocessamento falha nas duas trilhas
- **THEN** nenhuma revisão é publicada
- **AND** a revisão ativa anterior permanece inalterada

#### Scenario: Revisão em construção não é visível

- **WHEN** uma transcrição está em andamento e um cliente consulta a reunião
- **THEN** a consulta devolve a revisão ativa anterior, ou o estado de processamento quando não houver nenhuma
- **AND** nenhum segmento da revisão em construção é devolvido

#### Scenario: Configuração alterada no meio de uma tentativa

- **WHEN** a configuração de motor é alterada depois de a primeira trilha ter sido transcrita e antes da segunda
- **THEN** as duas trilhas da tentativa em curso usam a configuração capturada no início dela
- **AND** a nova configuração vale apenas para tentativas iniciadas depois

#### Scenario: Queda entre publicar a revisão e regerar as exportações

- **WHEN** o processo é encerrado depois de publicar uma revisão e antes de regerar os arquivos de exportação
- **THEN** o identificador de revisão registrado nas exportações diverge da revisão ativa
- **AND** as exportações são regeradas na inicialização seguinte
- **AND** nenhuma superfície apresenta a exportação divergente como corrente

### Requirement: Linha de tempo única com atribuição de falante

O sistema SHALL fundir os segmentos das duas trilhas numa única linha de tempo ordenada por instante de início.

Segmentos provenientes da trilha de entrada SHALL ser atribuídos ao usuário. Segmentos provenientes da trilha do sistema SHALL ser atribuídos aos demais participantes, sem distinção entre eles. Segmentos de sessões importadas SHALL ser atribuídos a falante desconhecido.

O sistema MUST NOT inferir a identidade individual dos demais participantes.

#### Scenario: Fusão de duas trilhas transcritas

- **WHEN** as duas trilhas de uma reunião foram transcritas separadamente
- **THEN** a linha de tempo resultante intercala os segmentos das duas por instante de início
- **AND** cada segmento carrega a atribuição correspondente à sua trilha

#### Scenario: Fala simultânea nas duas trilhas

- **WHEN** o usuário e outro participante falam ao mesmo tempo e ambas as trilhas produzem segmentos sobrepostos
- **THEN** os dois segmentos são preservados integralmente
- **AND** a sobreposição é sinalizada na linha de tempo
- **AND** nenhum dos segmentos é descartado nem truncado

### Requirement: Busca textual sobre o histórico

O sistema SHALL oferecer busca textual sobre o texto de todos os segmentos de todas as reuniões.

Cada resultado SHALL identificar a reunião, o instante do trecho encontrado, a atribuição de falante e um recorte de texto com contexto ao redor da ocorrência.

A busca SHALL permitir restringir o escopo por intervalo de datas e por reunião.

A busca SHALL ser insensível a diferenças de caixa e de acentuação.

#### Scenario: Busca por termo presente em várias reuniões

- **WHEN** o usuário busca por um termo que aparece em três reuniões diferentes
- **THEN** os resultados identificam as três reuniões
- **AND** cada resultado traz o instante, a atribuição de falante e o texto com contexto

#### Scenario: Busca com acentuação divergente

- **WHEN** o usuário busca por um termo sem acentos que aparece acentuado nas transcrições
- **THEN** as ocorrências acentuadas são encontradas

#### Scenario: Busca restrita por período

- **WHEN** o usuário busca por um termo restringindo a um intervalo de datas
- **THEN** apenas ocorrências em reuniões dentro do intervalo são devolvidas

### Requirement: Exportação da transcrição

O sistema SHALL gerar, para cada reunião transcrita, uma exportação legível por humanos e uma exportação estruturada.

A exportação legível SHALL conter o título, a data, a duração e a linha de tempo com instante, atribuição de falante e texto de cada segmento.

A exportação estruturada SHALL conter todos os campos da reunião e de cada segmento, incluindo trilha de origem e instantes em milissegundos, suficientes para reconstruir a linha de tempo sem perda.

As exportações SHALL ser gravadas no diretório da reunião e regeradas quando a transcrição for refeita.

#### Scenario: Exportações geradas ao fim da transcrição

- **WHEN** a transcrição de uma reunião termina
- **THEN** a exportação legível e a estruturada existem no diretório da reunião
- **AND** ambas refletem a linha de tempo fundida

#### Scenario: Regeração após nova transcrição

- **WHEN** uma reunião é transcrita novamente com outro motor
- **THEN** as exportações são regeradas a partir do novo resultado

### Requirement: Acesso concorrente ao armazenamento

O sistema SHALL permitir que múltiplos processos leiam o armazenamento simultaneamente enquanto um processo escreve, sem que os leitores sejam bloqueados nem obtenham dados parcialmente escritos.

O armazenamento admite **um escritor por vez**. Como mais de um processo pode escrever — o serviço residente publicando transcrições e clientes MCP gravando notas —, o sistema SHALL tratar a contenção de escrita explicitamente:

- toda transação de escrita SHALL ser curta, e MUST NOT permanecer aberta durante leitura de arquivo, transcrição ou qualquer espera por entrada e saída externa;
- ao encontrar o armazenamento ocupado, o escritor SHALL aguardar e repetir por até 5 segundos;
- esgotado esse prazo, a operação SHALL falhar com erro que identifica a contenção, e MUST NOT ser reportada como sucesso nem deixar escrita parcial.

A espera por contenção de escrita MUST NOT ocorrer no caminho de execução da captura de áudio. O sistema SHALL desacoplar a persistência da captura, de modo que nenhuma disputa pelo armazenamento possa provocar perda de áudio.

Uma escrita interrompida MUST NOT deixar o armazenamento em estado inconsistente.

Este requisito existe porque o servidor MCP e a interface gráfica das fases seguintes leem e escrevem o mesmo armazenamento que o serviço residente escreve, inclusive com a aplicação principal fechada.

#### Scenario: Leitura durante uma transcrição em andamento

- **WHEN** um segundo processo consulta o armazenamento enquanto uma transcrição grava segmentos
- **THEN** a consulta responde sem bloquear
- **AND** devolve apenas dados já confirmados

#### Scenario: Dois escritores simultâneos

- **WHEN** dois clientes gravam notas enquanto o serviço residente publica uma transcrição
- **THEN** todas as escritas são concluídas sem perda
- **AND** nenhuma delas excede o prazo de espera por contenção

#### Scenario: Contenção além do prazo

- **WHEN** um escritor não consegue obter acesso de escrita dentro de 5 segundos
- **THEN** a operação falha com erro que identifica a contenção
- **AND** nenhuma escrita parcial permanece no armazenamento

#### Scenario: Contenção durante uma gravação ativa

- **WHEN** há disputa prolongada pelo armazenamento enquanto uma gravação está em andamento
- **THEN** a captura de áudio prossegue sem interrupção
- **AND** nenhum áudio é perdido em decorrência da contenção

#### Scenario: Escrita interrompida abruptamente

- **WHEN** o processo que grava segmentos é encerrado no meio da escrita
- **THEN** o armazenamento permanece consistente na próxima abertura
- **AND** nenhuma reunião fica com segmentos parcialmente gravados de uma mesma revisão

### Requirement: Evolução do esquema de dados

O sistema SHALL registrar a versão do esquema do armazenamento e SHALL aplicar automaticamente as migrações necessárias ao abrir um armazenamento de versão anterior.

O sistema MUST NOT abrir um armazenamento de versão mais recente do que a que conhece, e SHALL falhar com erro explícito nesse caso.

Nenhuma migração SHALL descartar reuniões, segmentos ou referências a áudio existentes.

Como mais de um processo pode abrir o armazenamento ao mesmo tempo — o serviço residente e um ou mais servidores MCP —, as migrações SHALL ser serializadas entre processos: no máximo um processo aplica migrações por vez, e os demais SHALL aguardar sua conclusão antes de operar. O sistema MUST NOT permitir que dois processos apliquem migrações concorrentemente.

Um processo que aguarda migração alheia SHALL desistir após 30 segundos e falhar com erro que identifica a espera, em vez de operar sobre esquema indeterminado.

#### Scenario: Abertura de armazenamento de versão anterior

- **WHEN** o sistema abre um armazenamento criado por uma versão anterior
- **THEN** as migrações pendentes são aplicadas automaticamente
- **AND** todas as reuniões e segmentos anteriores permanecem acessíveis

#### Scenario: Abertura de armazenamento de versão futura

- **WHEN** o sistema abre um armazenamento cuja versão de esquema é superior à conhecida
- **THEN** a abertura falha com erro que informa as duas versões
- **AND** nenhuma escrita é realizada

#### Scenario: Dois processos abrindo um armazenamento desatualizado ao mesmo tempo

- **WHEN** o serviço residente e um servidor MCP abrem simultaneamente um armazenamento de versão anterior
- **THEN** apenas um deles aplica as migrações
- **AND** o outro aguarda a conclusão e opera sobre o esquema já migrado
- **AND** nenhuma migração é aplicada duas vezes

#### Scenario: Espera por migração além do prazo

- **WHEN** um processo aguarda a migração conduzida por outro por mais de 30 segundos
- **THEN** a abertura falha com erro que identifica a espera
- **AND** o processo não opera sobre o armazenamento
