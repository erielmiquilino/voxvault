# transcription-engine Specification

## Purpose
Definir a fronteira estável entre o VoxVault e qualquer motor de transcrição, local ou remoto. Todo o resto do sistema depende deste contrato e nunca de uma implementação específica, de modo que trocar transcrição local por um provedor online seja escrever uma implementação nova sem tocar em nada mais.

## Requirements

### Requirement: Contrato de transcrição

O sistema SHALL definir um contrato de transcrição que recebe o caminho de um arquivo de áudio, um código de idioma e um texto opcional de vocabulário de domínio, e devolve uma sequência ordenada de segmentos.

Cada implementação do contrato SHALL ser substituível por qualquer outra sem exigir mudança em qualquer consumidor do contrato.

O contrato SHALL expor um identificador legível do motor e da configuração em uso, de modo que qualquer transcrição produzida possa ser atribuída ao que a gerou.

#### Scenario: Transcrição de áudio válido

- **WHEN** um consumidor invoca o contrato com um arquivo de áudio legível e idioma `pt`
- **THEN** recebe uma sequência de segmentos, cada um com início, fim e texto
- **AND** recebe o identificador do motor e da configuração usados

#### Scenario: Troca de implementação sem mudança no consumidor

- **WHEN** a implementação configurada é substituída por outra que cumpre o mesmo contrato
- **THEN** os consumidores continuam funcionando sem alteração de código
- **AND** o identificador do motor nos resultados reflete a nova implementação

### Requirement: Formato dos segmentos

Cada segmento SHALL conter um instante de início e um instante de fim, ambos em milissegundos inteiros relativos ao começo do áudio de entrada, e um texto não vazio.

O instante de início SHALL ser maior ou igual a zero. O instante de fim SHALL ser maior que o de início. Os segmentos SHALL ser devolvidos em ordem não decrescente de instante de início.

O sistema MUST NOT devolver segmentos cujo texto seja vazio ou composto apenas de espaços.

#### Scenario: Ordenação e integridade dos segmentos

- **WHEN** uma transcrição devolve mais de um segmento
- **THEN** cada segmento tem início maior ou igual a zero e fim maior que o início
- **AND** a sequência está em ordem não decrescente de instante de início

#### Scenario: Áudio sem fala

- **WHEN** o áudio de entrada não contém fala alguma
- **THEN** o contrato devolve uma sequência vazia de segmentos
- **AND** a operação é considerada bem-sucedida, não um erro

### Requirement: Supressão de alucinação em silêncio

O motor SHALL aplicar detecção de atividade de voz antes da decodificação e MUST NOT emitir segmentos para trechos sem fala.

O motor MUST NOT condicionar a decodificação de um trecho ao texto decodificado dos trechos anteriores, de modo a impedir que um erro se propague em laço pelo restante do áudio.

Esta exigência existe porque modelos de transcrição preenchem silêncio com texto aprendido de legendas, produzindo conteúdo inventado que é indistinguível de fala real para quem lê a transcrição depois.

#### Scenario: Trecho longo de silêncio no meio do áudio

- **WHEN** o áudio contém um trecho de trinta segundos sem fala entre duas falas reais
- **THEN** nenhum segmento é emitido cobrindo esse trecho
- **AND** os segmentos das falas reais preservam seus instantes corretos em relação ao início do áudio

#### Scenario: Áudio inteiramente silencioso

- **WHEN** o áudio de entrada é um trecho de silêncio de cinco minutos
- **THEN** a sequência devolvida é vazia
- **AND** nenhum texto é produzido

### Requirement: Normalização da entrada de áudio

O contrato SHALL aceitar qualquer arquivo de áudio ou vídeo que o decodificador de mídia externo consiga ler, e SHALL normalizá-lo internamente para o formato que o motor exige.

Os instantes reportados nos segmentos SHALL ser relativos ao início do arquivo original fornecido pelo consumidor, independentemente de qualquer conversão intermediária.

O sistema MUST NOT modificar nem sobrescrever o arquivo de entrada.

#### Scenario: Entrada em formato comprimido

- **WHEN** o consumidor fornece um arquivo de áudio comprimido com taxa de amostragem e número de canais diferentes do que o motor exige
- **THEN** o arquivo é normalizado internamente antes da decodificação
- **AND** os instantes dos segmentos são relativos ao início do arquivo original
- **AND** o arquivo original permanece inalterado

#### Scenario: Entrada de vídeo

- **WHEN** o consumidor fornece um arquivo de vídeo com trilha de áudio
- **THEN** a trilha de áudio é extraída e transcrita
- **AND** o arquivo de vídeo original permanece inalterado

### Requirement: Vocabulário de domínio

O contrato SHALL aceitar um texto opcional de vocabulário de domínio — nomes próprios, siglas e jargão — e SHALL usá-lo para orientar a transcrição.

Quando o vocabulário não é fornecido, a transcrição SHALL prosseguir normalmente.

#### Scenario: Vocabulário fornecido

- **WHEN** o consumidor fornece vocabulário contendo nomes e siglas do time
- **THEN** a transcrição é orientada por esse vocabulário
- **AND** o vocabulário usado é registrado junto com a identificação da configuração

#### Scenario: Vocabulário ausente

- **WHEN** o consumidor não fornece vocabulário
- **THEN** a transcrição prossegue sem orientação adicional

### Requirement: Execução acelerada e política de degradação

A implementação local SHALL decidir entre GPU, CPU e recusa de execução seguindo exatamente a matriz de disponibilidade definida pela verificação de ambiente, e MUST NOT adotar política própria divergente dela.

Em particular: a implementação SHALL usar GPU quando há GPU compatível e bibliotecas carregáveis; SHALL executar em CPU quando não há GPU compatível no hardware; e SHALL recusar a execução quando há GPU mas as bibliotecas não carregam, salvo se a execução em CPU tiver sido habilitada explicitamente por configuração.

O sistema MUST NOT degradar para CPU silenciosamente em nenhuma circunstância.

A configuração de execução SHALL ser explícita quanto ao dispositivo e à precisão. A configuração suportada em GPU é `float16`; a configuração suportada em CPU é `int8`, por ser a única com desempenho utilizável nesta classe de máquina.

O identificador de motor e configuração SHALL registrar dispositivo, precisão e modelo efetivamente usados, de modo que uma transcrição produzida em CPU seja distinguível de uma produzida em GPU ao ser lida depois.

#### Scenario: GPU disponível

- **WHEN** a implementação local é iniciada numa máquina com GPU compatível, bibliotecas carregáveis e memória suficiente
- **THEN** a inferência roda acelerada por GPU em `float16`
- **AND** o identificador da configuração registra dispositivo, precisão e modelo

#### Scenario: Máquina sem GPU no hardware

- **WHEN** a implementação local é iniciada numa máquina sem GPU compatível
- **THEN** a inferência roda em CPU em `int8`
- **AND** um aviso explícito sobre a perda de desempenho é emitido ao usuário

#### Scenario: GPU presente com bibliotecas quebradas

- **WHEN** existe GPU compatível mas as bibliotecas de inferência não carregam e a execução em CPU não foi habilitada
- **THEN** a operação é recusada com erro que nomeia a biblioteca que falhou
- **AND** nenhuma inferência é executada em CPU

#### Scenario: Memória de GPU insuficiente para o modelo pedido

- **WHEN** o modelo solicitado não cabe na memória de GPU disponível
- **THEN** a operação falha com erro que nomeia o modelo, a memória exigida e a disponível
- **AND** a mensagem sugere um modelo menor

### Requirement: Configuração padrão do motor

O sistema SHALL definir uma configuração padrão de motor aplicada quando nenhuma outra é indicada, de modo que toda a ferramenta seja utilizável sem que o usuário precise escolher.

A configuração padrão inicial SHALL ser o modelo `large-v3` em `float16` na GPU, com o idioma `pt`.

Essa configuração é **provisória** e existe para não bloquear as fases seguintes enquanto a avaliação comparativa de qualidade não tiver sido concluída pelo usuário. Substituí-la SHALL ser uma alteração de configuração, e MUST NOT exigir mudança em qualquer consumidor do contrato.

O sistema SHALL registrar em cada reunião transcrita qual configuração foi usada, de modo que, se o padrão mudar depois, seja possível saber o que produziu cada transcrição existente.

#### Scenario: Transcrição sem configuração explícita

- **WHEN** uma transcrição é disparada sem indicação de motor
- **THEN** a configuração padrão é aplicada
- **AND** o identificador registrado na reunião reflete a configuração efetivamente usada

#### Scenario: Troca do padrão após a avaliação

- **WHEN** o padrão é alterado por configuração
- **THEN** as próximas transcrições passam a usar o novo padrão
- **AND** as transcrições existentes continuam registrando a configuração que as produziu

### Requirement: Falhas explícitas e tipadas

O contrato SHALL sinalizar falhas por categorias distinguíveis pelo consumidor, no mínimo: entrada ilegível ou corrompida, pré-requisito de ambiente ausente, recurso de hardware insuficiente, e falha do provedor.

Mensagens de erro SHALL nomear o arquivo, o modelo ou o recurso envolvido.

O sistema MUST NOT devolver uma transcrição parcial como se fosse completa; uma transcrição interrompida SHALL ser sinalizada como falha.

#### Scenario: Arquivo de entrada corrompido

- **WHEN** o consumidor fornece um arquivo que o decodificador não consegue ler
- **THEN** a operação falha na categoria de entrada ilegível
- **AND** a mensagem nomeia o caminho do arquivo

#### Scenario: Interrupção no meio da transcrição

- **WHEN** a transcrição é interrompida antes de processar todo o áudio
- **THEN** a operação é sinalizada como falha
- **AND** nenhum resultado parcial é devolvido como sucesso
