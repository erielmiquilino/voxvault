# environment-check Specification

## Purpose
Verificar, antes de qualquer trabalho pesado, se a máquina reúne os pré-requisitos que a transcrição local exige, e dizer exatamente o que falta e como resolver quando algo não está no lugar. Este é o ponto onde o stack de GPU mais falha no Windows, e falhar cedo e com clareza economiza horas de diagnóstico.

## Requirements

### Requirement: Diagnóstico de ambiente sob demanda

O sistema SHALL oferecer um comando de diagnóstico que inspeciona e reporta o estado de cada pré-requisito de execução: versão do interpretador Python, presença e modelo da GPU, disponibilidade de aceleração por GPU para inferência, presença das bibliotecas de álgebra linear e de redes neurais exigidas pelo runtime de inferência, presença do decodificador de mídia externo, e espaço livre no diretório de dados configurado.

Cada item verificado SHALL ser reportado individualmente com um de três estados: `ok`, `aviso` ou `falha`. O comando SHALL terminar com código de saída zero quando nenhum item estiver em `falha`, e diferente de zero caso contrário.

Nenhum item em estado `falha` ou `aviso` SHALL ser reportado sem uma mensagem que indique a ação corretiva.

A verificação das bibliotecas de inferência acelerada SHALL tentar o **carregamento real** das bibliotecas e uma inferência mínima, e MUST NOT se limitar a constatar que o pacote está instalado. Instalar o pacote e conseguir carregá-lo são coisas distintas no Windows, e a diferença entre as duas é exatamente o modo de falha mais comum deste stack.

A verificação SHALL ser executada no mesmo ambiente de processo que executará a transcrição — mesmo interpretador, mesmas variáveis de ambiente e mesmo diretório de trabalho —, de modo que a resolução de bibliotecas dinâmicas observada no diagnóstico seja a mesma da execução real.

#### Scenario: Ambiente completo e funcional

- **WHEN** o usuário executa o diagnóstico numa máquina com GPU compatível, runtime de inferência acelerado e decodificador de mídia instalados
- **THEN** todos os itens são reportados como `ok`
- **AND** o relatório inclui o modelo da GPU, a VRAM total e o espaço livre do diretório de dados
- **AND** o comando termina com código de saída zero

#### Scenario: Pacote instalado mas biblioteca não carregável

- **WHEN** os pacotes de inferência acelerada estão instalados mas o carregamento da biblioteca dinâmica falha
- **THEN** o item é reportado como `falha`
- **AND** a mensagem distingue explicitamente instalação de carregamento e nomeia a biblioteca que falhou

#### Scenario: Decodificador de mídia ausente

- **WHEN** o diagnóstico roda numa máquina sem o decodificador de mídia externo disponível no `PATH`
- **THEN** o item é reportado como `falha` com instrução de instalação
- **AND** o comando termina com código de saída diferente de zero

#### Scenario: Versão de Python incompatível

- **WHEN** o diagnóstico roda sob um interpretador fora da faixa suportada
- **THEN** o item da versão de Python é reportado como `falha`
- **AND** a mensagem informa a faixa de versões suportada e a versão encontrada

### Requirement: Matriz de disponibilidade por capacidade

Cada pré-requisito SHALL declarar quais capacidades ele condiciona, e o diagnóstico SHALL reportar a disponibilidade de cada capacidade separadamente. Um pré-requisito ausente SHALL tornar indisponíveis apenas as capacidades que dependem dele.

A relação entre o estado do ambiente de inferência e a disponibilidade da transcrição SHALL seguir exatamente esta matriz:

| Situação | Estado do item | Transcrição | Gravação e importação |
|---|---|---|---|
| GPU compatível e bibliotecas carregáveis | `ok` | disponível, acelerada | disponível |
| Nenhuma GPU compatível presente no hardware | `aviso` | disponível em CPU, com aviso de desempenho | disponível |
| GPU presente mas bibliotecas ausentes ou não carregáveis | `falha` | **indisponível**, salvo execução em CPU habilitada explicitamente | disponível |
| Modelo selecionado não cabe na memória de GPU disponível | `falha` | indisponível para esse modelo; outros modelos seguem a matriz | disponível |
| Decodificador de mídia ausente | `falha` | indisponível | gravação disponível, importação indisponível |

O sistema MUST NOT degradar para CPU por conta própria quando existe GPU e as bibliotecas estão quebradas: nessa situação a degradação silenciosa transformaria uma reunião de uma hora em horas de processamento e esconderia um defeito que o usuário precisa corrigir.

O sistema SHALL oferecer uma configuração explícita que habilita a execução em CPU mesmo com GPU presente e bibliotecas quebradas, desativada por padrão.

Este requisito existe para eliminar a ambiguidade entre "pré-requisito ausente bloqueia a operação" e "aceleração indisponível degrada para CPU": as duas regras valem, e esta matriz define qual se aplica em cada caso.

#### Scenario: Máquina sem GPU

- **WHEN** o diagnóstico roda numa máquina sem GPU compatível no hardware
- **THEN** o item é reportado como `aviso`
- **AND** a transcrição permanece disponível em CPU, com aviso explícito de perda de desempenho
- **AND** o comando termina com código de saída zero

#### Scenario: GPU presente com bibliotecas quebradas

- **WHEN** existe GPU compatível mas as bibliotecas de inferência não carregam
- **THEN** o item é reportado como `falha`
- **AND** a transcrição fica indisponível, sem degradação automática para CPU
- **AND** a gravação e a importação permanecem disponíveis
- **AND** a mensagem apresenta tanto a correção das bibliotecas quanto a configuração que habilita CPU explicitamente

#### Scenario: Execução em CPU habilitada explicitamente

- **WHEN** a configuração de execução em CPU está habilitada e as bibliotecas de GPU estão quebradas
- **THEN** a transcrição fica disponível em CPU
- **AND** o aviso de desempenho é emitido a cada transcrição

#### Scenario: Decodificador ausente não bloqueia a gravação

- **WHEN** o decodificador de mídia está ausente
- **THEN** a gravação permanece disponível
- **AND** a importação e a transcrição ficam indisponíveis com a causa nomeada

### Requirement: Configuração compartilhada e precedência

O sistema SHALL manter a configuração num local fixo por usuário, independente do diretório de trabalho de qualquer processo, de modo que todos os processos — serviço residente, comandos de linha de comando, servidores MCP e aplicação de desktop — resolvam a mesma configuração.

O sistema MUST NOT derivar o diretório de dados do diretório de trabalho corrente do processo.

A precedência SHALL ser, do mais forte ao mais fraco: parâmetro explícito da invocação, variável de ambiente, arquivo de configuração do usuário, padrão embutido.

O sistema SHALL registrar, no diagnóstico, qual fonte forneceu cada valor efetivo, de modo que uma divergência entre processos seja diagnosticável.

O sistema SHALL validar a configuração ao carregá-la e SHALL falhar com erro que nomeia o campo inválido e a fonte de onde ele veio, em vez de aplicar um valor padrão silenciosamente.

#### Scenario: Processos diferentes resolvem o mesmo diretório de dados

- **WHEN** o serviço residente e um servidor MCP são iniciados a partir de diretórios de trabalho distintos, sem parâmetro nem variável de ambiente
- **THEN** ambos resolvem o mesmo diretório de dados a partir do arquivo de configuração do usuário

#### Scenario: Variável de ambiente sobrepõe o arquivo

- **WHEN** a variável de ambiente do diretório de dados está definida e difere do arquivo de configuração
- **THEN** o valor da variável de ambiente prevalece
- **AND** o diagnóstico informa que a origem do valor efetivo foi a variável de ambiente

#### Scenario: Configuração inválida

- **WHEN** o arquivo de configuração contém um valor inválido
- **THEN** o carregamento falha nomeando o campo e a fonte
- **AND** nenhum valor padrão é aplicado silenciosamente em seu lugar

### Requirement: Validação do diretório de dados

O sistema SHALL resolver o diretório de dados pela precedência de configuração definida acima, com padrão embutido em `D:\VoxVault`, e SHALL validar que esse diretório é gravável e tem espaço livre suficiente antes de qualquer operação que escreva nele.

O sistema SHALL tratar espaço livre abaixo de 5 GB como `aviso`, e a impossibilidade de escrita como `falha`.

O diagnóstico SHALL apresentar, junto ao espaço livre, o consumo aproximado por hora de gravação, de modo que o número tenha significado prático.

#### Scenario: Diretório de dados gravável com espaço

- **WHEN** o diretório de dados existe, é gravável e tem mais de 5 GB livres
- **THEN** o item é reportado como `ok` com o espaço livre e o consumo aproximado por hora

#### Scenario: Diretório de dados inexistente

- **WHEN** o diretório de dados configurado não existe
- **THEN** o sistema o cria, incluindo diretórios pais
- **AND** o item é reportado como `ok`

#### Scenario: Diretório de dados sem permissão de escrita

- **WHEN** o diretório de dados existe mas não aceita escrita
- **THEN** o item é reportado como `falha` nomeando o caminho exato que falhou

#### Scenario: Espaço em disco abaixo do limiar

- **WHEN** o diretório de dados tem menos de 5 GB livres
- **THEN** o item é reportado como `aviso` informando o espaço disponível e o limiar

### Requirement: Bloqueio antecipado de execução incompatível

Qualquer operação que dependa de um pré-requisito ausente, segundo a matriz de disponibilidade, SHALL falhar imediatamente, antes de carregar modelos ou processar áudio, com um erro que nomeia o pré-requisito e a ação corretiva.

O sistema MUST NOT iniciar carregamento de modelo, alocação de VRAM ou leitura de áudio quando um pré-requisito obrigatório para aquela operação está ausente.

Operações cujas dependências estão satisfeitas MUST NOT ser bloqueadas por pré-requisitos de outras capacidades.

#### Scenario: Transcrição iniciada sem decodificador de mídia

- **WHEN** o usuário dispara uma transcrição numa máquina sem o decodificador de mídia
- **THEN** a operação falha antes de qualquer carregamento de modelo
- **AND** o erro nomeia o pré-requisito ausente e a ação corretiva
- **AND** nenhum arquivo parcial é deixado no diretório de dados

#### Scenario: Gravação iniciada com ambiente de inferência quebrado

- **WHEN** o usuário inicia uma gravação numa máquina cujas bibliotecas de inferência não carregam
- **THEN** a gravação inicia normalmente
- **AND** a sessão é enfileirada e permanece aguardando até que a transcrição volte a estar disponível
