## MODIFIED Requirements

### Requirement: Configuração padrão do motor

O sistema SHALL definir uma configuração padrão de motor aplicada quando nenhuma outra é indicada, de modo que toda a ferramenta seja utilizável sem que o usuário precise escolher.

A configuração padrão SHALL depender do hardware, com o idioma `pt` em todos os casos:

| Hardware | Modelo | Execução |
|---|---|---|
| GPU compatível com memória suficiente para o `large-v3` | `large-v3` | GPU, `float16` |
| GPU compatível com memória suficiente para o `large-v3-turbo`, mas não para o `large-v3` | `large-v3-turbo` | GPU, `float16` |
| Nenhuma GPU compatível, ou GPU sem memória suficiente nem para o `large-v3-turbo` | `large-v3-turbo` | CPU, `int8` |

A memória exigida por modelo SHALL ser a mesma usada pela matriz de disponibilidade, de modo que o padrão escolhido nunca seja um modelo que a própria matriz recusaria por falta de memória.

O padrão em CPU decorre de medição: numa CPU de 6 núcleos, o `large-v3-turbo` transcreveu 1,37 vez o tempo real contra 0,96 do `large-v3`, e produziu a transcrição mais completa das três configurações medidas.

Um modelo indicado explicitamente pelo usuário SHALL prevalecer sobre o padrão por hardware, respeitando a matriz de disponibilidade.

Essa configuração é **provisória** e existe para não bloquear as fases seguintes enquanto a avaliação comparativa de qualidade não tiver sido concluída pelo usuário. Substituí-la SHALL ser uma alteração de configuração, e MUST NOT exigir mudança em qualquer consumidor do contrato.

O sistema SHALL registrar em cada reunião transcrita qual configuração foi usada, de modo que, se o padrão mudar depois, seja possível saber o que produziu cada transcrição existente.

#### Scenario: Transcrição sem configuração explícita

- **WHEN** uma transcrição é disparada sem indicação de motor
- **THEN** a configuração padrão para o hardware da máquina é aplicada
- **AND** o identificador registrado na reunião reflete a configuração efetivamente usada

#### Scenario: Máquina sem GPU sem modelo configurado

- **WHEN** uma transcrição é disparada numa máquina sem GPU compatível e sem modelo configurado
- **THEN** a transcrição usa o `large-v3-turbo` em CPU com `int8`
- **AND** o identificador registrado na reunião traz esse modelo, dispositivo e precisão

#### Scenario: GPU com pouca memória sem modelo configurado

- **WHEN** uma transcrição é disparada numa máquina cuja GPU não comporta o `large-v3` e sem modelo configurado
- **THEN** a transcrição usa o `large-v3-turbo` na GPU com `float16`

#### Scenario: Modelo configurado explicitamente em CPU

- **WHEN** o usuário configurou o `large-v3` e a máquina não tem GPU compatível
- **THEN** a transcrição usa o `large-v3` em CPU com `int8`
- **AND** o aviso de perda de desempenho é emitido

#### Scenario: Troca do padrão após a avaliação

- **WHEN** o padrão é alterado por configuração
- **THEN** as próximas transcrições passam a usar o novo padrão
- **AND** as transcrições existentes continuam registrando a configuração que as produziu
