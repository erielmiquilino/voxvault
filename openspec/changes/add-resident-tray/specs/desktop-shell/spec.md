## MODIFIED Requirements

### Requirement: Instância única

O aplicativo SHALL permitir apenas uma instância em execução.

Uma segunda tentativa de abertura SHALL trazer a janela existente para primeiro plano em vez de abrir outra instância; com o aplicativo recolhido na bandeja, SHALL recriar a janela principal na última tela vista e trazê-la para primeiro plano.

#### Scenario: Segunda abertura do aplicativo

- **WHEN** o usuário abre o aplicativo com uma instância já em execução e a janela aberta
- **THEN** a janela existente é trazida para primeiro plano
- **AND** nenhuma segunda instância é criada

#### Scenario: Segunda abertura com o aplicativo na bandeja

- **WHEN** o usuário abre o aplicativo pelo menu Iniciar com a instância em execução recolhida na bandeja
- **THEN** a janela principal é recriada na última tela vista e trazida para primeiro plano
- **AND** nenhuma segunda instância é criada

### Requirement: Custo desprezível durante a gravação

Com uma gravação em andamento, o conjunto de processos do VoxVault SHALL permanecer dentro dos limites abaixo, verificáveis por medição.

O **conjunto medido** SHALL compreender: o processo hospedeiro do aplicativo, todos os processos do componente de navegação embutido e seus descendentes, o serviço residente e todos os seus descendentes. Medir apenas o processo hospedeiro MUST NOT ser aceito, porque o componente de navegação executa em processos separados e é justamente onde o custo de uma interface mal construída aparece.

Os limites de gravação, medidos ao longo de uma gravação de 60 minutos com amostragem a cada 5 segundos, na máquina de referência do projeto:

| Métrica | Limite |
|---|---|
| Uso médio de processador, somado no conjunto | 8% do total da máquina |
| Uso de processador em qualquer janela de 60 segundos | 15% do total da máquina |
| Memória residente somada, janela aberta | 700 MB |
| Memória residente somada, aplicativo recolhido na bandeja | 250 MB |

Sem gravação, com o aplicativo recolhido na bandeja, a memória residente somada do conjunto SHALL permanecer em no máximo 100 MB e o uso médio de processador em no máximo 1% do total da máquina, medidos ao longo de 10 minutos com amostragem a cada 5 segundos.

A interface MUST NOT executar transcrição, indexação pesada ou qualquer processamento de áudio além da captura e da apresentação de níveis enquanto houver gravação ativa.

A taxa de atualização dos medidores de nível SHALL ser limitada a no máximo 20 atualizações por segundo por trilha.

Este requisito existe porque a lentidão de ferramentas equivalentes durante a reunião é a razão declarada do projeto, e uma interface mal construída reintroduz exatamente esse defeito. Limites autodefinidos pelo implementador não serviriam de critério.

#### Scenario: Medição durante uma reunião de uma hora

- **WHEN** o consumo do conjunto de processos é medido ao longo de uma gravação de 60 minutos com a janela aberta
- **THEN** o uso médio de processador permanece em no máximo 8% do total da máquina
- **AND** nenhuma janela de 60 segundos ultrapassa 15%
- **AND** a memória residente somada permanece em no máximo 700 MB
- **AND** nenhuma transcrição é executada durante o período

#### Scenario: Aplicativo recolhido na bandeja durante a gravação

- **WHEN** o aplicativo é recolhido para a bandeja durante uma gravação e o consumo é medido ao longo de 60 minutos
- **THEN** a gravação continua sem interrupção
- **AND** a memória residente somada permanece em no máximo 250 MB
- **AND** o uso de processador não aumenta em relação à janela aberta

#### Scenario: Aplicativo ocioso na bandeja

- **WHEN** o aplicativo está recolhido na bandeja sem gravação e o consumo é medido ao longo de 10 minutos
- **THEN** a memória residente somada permanece em no máximo 100 MB
- **AND** o uso médio de processador permanece em no máximo 1% do total da máquina
