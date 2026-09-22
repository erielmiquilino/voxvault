## Purpose

Rodar vários motores ou modelos de transcrição sobre exatamente o mesmo áudio e apresentar os resultados lado a lado, para que uma pessoa leia e decida se a transcrição local atende. É o instrumento do portão de decisão da Fase 0, e permanece útil depois para reavaliar qualquer motor novo contra o que já está em uso.

## ADDED Requirements

### Requirement: Execução comparativa sobre a mesma entrada

O sistema SHALL executar um conjunto configurável de motores ou configurações de modelo sobre o mesmo arquivo de áudio, numa única invocação.

Todas as execuções SHALL receber entrada idêntica: mesmo arquivo, mesmo idioma e mesmo vocabulário de domínio. O sistema MUST NOT aplicar pré-processamento diferente entre execuções.

O sistema SHALL registrar, para cada execução, o identificador do motor e da configuração que a produziu.

#### Scenario: Comparação de duas configurações de modelo

- **WHEN** o usuário executa o benchmark sobre um arquivo indicando duas configurações de modelo
- **THEN** ambas transcrevem o mesmo arquivo com os mesmos parâmetros de entrada
- **AND** cada resultado é rotulado com o identificador de sua configuração

#### Scenario: Falha de uma execução não derruba as demais

- **WHEN** uma das configurações falha durante o benchmark
- **THEN** as demais configurações são executadas até o fim
- **AND** o relatório registra a falha daquela configuração com o motivo
- **AND** o comando termina com código de saída diferente de zero

### Requirement: Métricas de desempenho por execução

Para cada execução, o sistema SHALL medir e registrar: tempo total de parede, duração do áudio de entrada, fator de tempo real derivado dos dois, e pico de memória de GPU utilizado.

O tempo medido SHALL incluir o carregamento do modelo, reportado separadamente do tempo de decodificação, para que o custo fixo não seja confundido com o custo por hora de áudio.

#### Scenario: Métricas de uma execução acelerada por GPU

- **WHEN** uma configuração roda com aceleração por GPU
- **THEN** o relatório registra tempo de carregamento, tempo de decodificação, fator de tempo real e pico de memória de GPU

#### Scenario: Métricas de execução em CPU

- **WHEN** uma configuração roda sem aceleração por GPU
- **THEN** o relatório registra os tempos e o fator de tempo real
- **AND** o pico de memória de GPU é reportado como não aplicável

### Requirement: Isolamento de recursos entre execuções

O sistema SHALL liberar a memória de GPU alocada por uma execução antes de iniciar a próxima, de modo que o pico medido seja atribuível a uma única configuração.

O sistema MUST NOT manter dois modelos carregados simultaneamente em memória de GPU durante o benchmark.

#### Scenario: Execuções sequenciais com liberação de memória

- **WHEN** o benchmark roda três configurações em sequência numa máquina com memória de GPU limitada
- **THEN** cada uma é carregada, executada e descarregada antes da seguinte
- **AND** nenhuma execução falha por falta de memória causada por uma execução anterior

### Requirement: Relatório comparativo legível

O sistema SHALL produzir um relatório em formato legível por humanos que apresenta, para cada janela de tempo do áudio, o texto produzido por cada configuração alinhado lado a lado.

O alinhamento SHALL ser feito por instante no áudio, não por índice de segmento, já que configurações diferentes segmentam o áudio de formas diferentes.

O relatório SHALL abrir com uma tabela-resumo das métricas de desempenho de todas as configurações, antes do corpo comparativo.

#### Scenario: Leitura do relatório comparativo

- **WHEN** o benchmark termina com duas configurações bem-sucedidas
- **THEN** o relatório abre com uma tabela comparando tempo, fator de tempo real e pico de memória das duas
- **AND** o corpo apresenta, por janela de tempo, o texto de cada configuração em colunas alinhadas
- **AND** cada janela mostra seu instante inicial em formato de tempo legível

#### Scenario: Configurações que segmentam o áudio de forma diferente

- **WHEN** duas configurações produzem números diferentes de segmentos para o mesmo trecho
- **THEN** o relatório agrupa os textos pela janela de tempo em que caem
- **AND** nenhum texto é descartado do relatório por não ter correspondente na outra coluna

### Requirement: Preservação dos resultados brutos

O sistema SHALL gravar, além do relatório comparativo, a transcrição completa de cada execução em arquivo próprio, com seus segmentos e instantes íntegros.

Os resultados SHALL ser gravados sob o diretório de dados configurado, agrupados por execução de benchmark, identificados por instante da execução e pelo arquivo de entrada.

O sistema MUST NOT sobrescrever resultados de uma execução anterior de benchmark.

#### Scenario: Resultados brutos preservados por execução

- **WHEN** o benchmark termina
- **THEN** cada configuração tem sua transcrição completa gravada em arquivo próprio
- **AND** o relatório comparativo referencia esses arquivos

#### Scenario: Segunda execução de benchmark sobre o mesmo arquivo

- **WHEN** o usuário roda o benchmark novamente sobre o mesmo arquivo de entrada
- **THEN** os resultados da execução anterior permanecem intactos
- **AND** os novos resultados são gravados sob uma identificação distinta
