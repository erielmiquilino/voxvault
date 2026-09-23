# desktop-shell Specification

## Purpose
Hospedar a interface do VoxVault e gerenciar o serviço local que executa o núcleo: prepará-lo na primeira abertura, mantê-lo vivo, comunicar-se com ele de forma restrita à máquina e encerrá-lo sem jamais colocar em risco uma gravação em andamento.

## Requirements

### Requirement: Preparo do ambiente de execução na primeira abertura

Na primeira abertura, ou sempre que o ambiente de execução do núcleo estiver ausente ou incompleto, o aplicativo SHALL prepará-lo automaticamente, informando o progresso ao usuário.

O aplicativo MUST NOT exigir que o usuário prepare o ambiente manualmente antes do primeiro uso.

Quando o preparo falhar, o aplicativo SHALL apresentar a causa em linguagem natural e a ação corretiva, e MUST NOT abrir a interface principal num estado em que os comandos aparentem funcionar.

#### Scenario: Primeira abertura numa máquina preparada apenas com os pré-requisitos do sistema

- **WHEN** o usuário abre o aplicativo pela primeira vez
- **THEN** o ambiente de execução do núcleo é preparado automaticamente com progresso visível
- **AND** a interface principal abre ao fim do preparo

#### Scenario: Falha no preparo do ambiente

- **WHEN** o preparo do ambiente falha
- **THEN** a causa e a ação corretiva são apresentadas em linguagem natural
- **AND** a interface principal não abre em estado aparentemente funcional

#### Scenario: Abertura subsequente com ambiente já preparado

- **WHEN** o usuário abre o aplicativo com o ambiente já preparado
- **THEN** nenhum preparo é refeito
- **AND** a interface principal abre diretamente

### Requirement: Ciclo de vida do serviço residente

O aplicativo SHALL conectar-se ao serviço residente do núcleo ao abrir, iniciando-o quando ele não estiver em execução, e MUST NOT criar uma segunda instância quando já houver uma ativa para o mesmo diretório de dados.

O aplicativo MUST NOT abrir dispositivos de áudio nem executar transcrição por conta própria; ele opera exclusivamente como cliente do serviço.

Ao fechar, o aplicativo MUST NOT encerrar o serviço quando houver gravação ativa ou sessões pendentes na fila. Nessas condições o serviço SHALL permanecer em execução até concluir o trabalho e, depois, encerrar-se pela sua própria política de ociosidade.

Sem gravação ativa e com a fila vazia, fechar o aplicativo SHALL deixar o serviço encerrar-se pela política de ociosidade, sem que o aplicativo precise matá-lo.

Quando o serviço terminar inesperadamente, o aplicativo SHALL detectar a queda, tentar reiniciá-lo e informar o usuário. Após 3 falhas consecutivas de inicialização dentro de 60 segundos, o aplicativo SHALL parar de tentar e apresentar o estado de falha com a causa registrada.

#### Scenario: Aplicativo fechado com transcrições pendentes

- **WHEN** o usuário fecha o aplicativo com duas sessões aguardando transcrição
- **THEN** o serviço permanece em execução
- **AND** as transcrições são concluídas
- **AND** ao reabrir o aplicativo, as reuniões aparecem prontas

#### Scenario: Aplicativo fechado sem trabalho pendente

- **WHEN** o usuário fecha o aplicativo sem gravação ativa e com a fila vazia
- **THEN** o serviço encerra-se por ociosidade dentro do período configurado
- **AND** nenhum processo do núcleo permanece em execução depois disso

#### Scenario: Aplicativo aberto com serviço já em execução

- **WHEN** o usuário abre o aplicativo enquanto o serviço já está em execução, iniciado por um comando de linha de comando
- **THEN** o aplicativo conecta-se à instância existente
- **AND** uma gravação iniciada pela linha de comando aparece como ativa na interface

#### Scenario: Serviço encerra inesperadamente

- **WHEN** o serviço termina enquanto o aplicativo está aberto e sem gravação ativa
- **THEN** a queda é detectada e o serviço é reiniciado
- **AND** o usuário é informado do ocorrido

#### Scenario: Reinícios sucessivos sem sucesso

- **WHEN** o serviço falha ao subir 3 vezes consecutivas dentro de 60 segundos
- **THEN** o aplicativo interrompe as tentativas
- **AND** apresenta o estado de falha com a causa registrada

### Requirement: Comunicação restrita à máquina

A comunicação entre a interface e o serviço local SHALL ocorrer exclusivamente pela interface de rede local da própria máquina.

O serviço local MUST NOT aceitar conexões originadas fora da máquina.

O serviço local SHALL exigir um segredo gerado a cada inicialização, e SHALL recusar requisições que não o apresentem, de modo que outro processo na mesma máquina não consiga operá-lo.

#### Scenario: Requisição sem o segredo da sessão

- **WHEN** um processo qualquer da máquina envia uma requisição ao serviço local sem o segredo
- **THEN** a requisição é recusada
- **AND** nenhuma operação é executada

#### Scenario: Conexão originada de fora da máquina

- **WHEN** uma conexão é tentada a partir de outro computador da rede
- **THEN** a conexão não é aceita

### Requirement: Estado de saúde visível

O aplicativo SHALL apresentar de forma permanente e discreta o estado do serviço local e o resultado do diagnóstico de ambiente.

Quando um pré-requisito obrigatório estiver ausente, as ações que dependem dele SHALL ficar indisponíveis com a causa explicada, e MUST NOT falhar apenas no momento do clique.

#### Scenario: Pré-requisito de transcrição ausente

- **WHEN** o diagnóstico indica que o runtime de inferência não está utilizável
- **THEN** as ações que dependem de transcrição ficam indisponíveis com a causa explicada
- **AND** a gravação continua disponível, já que não depende desse pré-requisito

#### Scenario: Serviço local indisponível

- **WHEN** o serviço local está fora do ar
- **THEN** o estado é apresentado de forma visível
- **AND** as ações que dependem dele ficam indisponíveis

### Requirement: Encerramento seguro com gravação ativa

Quando o usuário tentar fechar o aplicativo com uma gravação ativa, o aplicativo SHALL pedir confirmação explícita, informando que há gravação em andamento e qual a sua duração até o momento.

Ao confirmar, o aplicativo SHALL encerrar a gravação de forma limpa — finalizando o áudio e submetendo a sessão para transcrição — antes de terminar.

O aplicativo MUST NOT terminar o serviço local abruptamente com gravação ativa.

#### Scenario: Fechamento durante uma gravação

- **WHEN** o usuário fecha o aplicativo com uma gravação de quarenta minutos em andamento
- **THEN** é pedida confirmação informando a gravação em andamento e sua duração
- **AND** ao confirmar, a gravação é encerrada de forma limpa e submetida para transcrição
- **AND** ao cancelar, o aplicativo permanece aberto e a gravação continua

#### Scenario: Desligamento do sistema durante uma gravação

- **WHEN** o sistema operacional solicita o encerramento do aplicativo durante uma gravação
- **THEN** a gravação é encerrada de forma limpa dentro do tempo disponível
- **AND** o áudio capturado permanece utilizável

### Requirement: Instância única

O aplicativo SHALL permitir apenas uma instância em execução.

Uma segunda tentativa de abertura SHALL trazer a janela existente para primeiro plano em vez de abrir outra instância.

#### Scenario: Segunda abertura do aplicativo

- **WHEN** o usuário abre o aplicativo com uma instância já em execução
- **THEN** a janela existente é trazida para primeiro plano
- **AND** nenhuma segunda instância é criada

### Requirement: Custo desprezível durante a gravação

Com uma gravação em andamento, o conjunto de processos do VoxVault SHALL permanecer dentro dos limites abaixo, verificáveis por medição.

O **conjunto medido** SHALL compreender: o processo hospedeiro do aplicativo, todos os processos do componente de navegação embutido e seus descendentes, o serviço residente e todos os seus descendentes. Medir apenas o processo hospedeiro MUST NOT ser aceito, porque o componente de navegação executa em processos separados e é justamente onde o custo de uma interface mal construída aparece.

Os limites, medidos ao longo de uma gravação de 60 minutos com amostragem a cada 5 segundos, na máquina de referência do projeto:

| Métrica | Limite |
|---|---|
| Uso médio de processador, somado no conjunto | 8% do total da máquina |
| Uso de processador em qualquer janela de 60 segundos | 15% do total da máquina |
| Memória residente somada, janela aberta | 700 MB |
| Memória residente somada, janela minimizada ou oculta | 400 MB |

A interface MUST NOT executar transcrição, indexação pesada ou qualquer processamento de áudio além da captura e da apresentação de níveis enquanto houver gravação ativa.

A taxa de atualização dos medidores de nível SHALL ser limitada a no máximo 20 atualizações por segundo por trilha.

Este requisito existe porque a lentidão de ferramentas equivalentes durante a reunião é a razão declarada do projeto, e uma interface mal construída reintroduz exatamente esse defeito. Limites autodefinidos pelo implementador não serviriam de critério.

#### Scenario: Medição durante uma reunião de uma hora

- **WHEN** o consumo do conjunto de processos é medido ao longo de uma gravação de 60 minutos com a janela aberta
- **THEN** o uso médio de processador permanece em no máximo 8% do total da máquina
- **AND** nenhuma janela de 60 segundos ultrapassa 15%
- **AND** a memória residente somada permanece em no máximo 700 MB
- **AND** nenhuma transcrição é executada durante o período

#### Scenario: Janela minimizada durante a gravação

- **WHEN** a janela é minimizada durante uma gravação
- **THEN** a gravação continua sem interrupção
- **AND** a memória residente somada permanece em no máximo 400 MB
- **AND** o uso de processador não aumenta em relação à janela aberta
