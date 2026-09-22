## Purpose

Expor o histórico de reuniões do VoxVault para clientes MCP, de modo que um agente possa ler transcrições, buscar no histórico e gravar notas de volta — sem que a aplicação principal precise estar aberta e sem que o agente possa danificar o registro do que foi dito.

## ADDED Requirements

### Requirement: Operação independente da aplicação principal

O servidor MCP SHALL operar lendo e escrevendo diretamente o armazenamento, sem depender de que a aplicação principal do VoxVault esteja em execução.

O servidor SHALL funcionar corretamente de forma concorrente com uma gravação ou transcrição em andamento, sem bloqueá-las e sem ser bloqueado por elas.

O servidor MUST NOT abrir porta de rede.

#### Scenario: Consulta com a aplicação fechada

- **WHEN** um cliente MCP consulta o histórico com a aplicação principal do VoxVault encerrada
- **THEN** a consulta responde normalmente com os dados gravados

#### Scenario: Consulta durante uma gravação

- **WHEN** um cliente MCP consulta o histórico enquanto uma gravação está em andamento
- **THEN** a consulta responde sem bloquear a gravação
- **AND** a reunião em andamento aparece com estado de gravação

### Requirement: Leitura do histórico de reuniões

O servidor SHALL expor uma ferramenta que lista reuniões, aceitando filtros por intervalo de datas, por estado de processamento e por termo no título, com ordenação da mais recente para a mais antiga.

Cada reunião listada SHALL trazer identificador, título, instante de início, duração, estado de processamento, origem e a contagem de notas associadas.

O servidor SHALL expor uma ferramenta que devolve a linha de tempo completa de uma reunião, com instante, atribuição de falante e texto de cada segmento, e outra que devolve apenas os segmentos contidos num intervalo de tempo informado.

#### Scenario: Listagem das reuniões recentes

- **WHEN** um cliente solicita as reuniões dos últimos sete dias
- **THEN** recebe as reuniões do período, da mais recente para a mais antiga
- **AND** cada uma traz identificador, título, início, duração, estado, origem e contagem de notas

#### Scenario: Leitura da linha de tempo de uma reunião

- **WHEN** um cliente solicita a transcrição de uma reunião pelo identificador
- **THEN** recebe a linha de tempo com instante, atribuição de falante e texto de cada segmento

#### Scenario: Recorte por intervalo de tempo

- **WHEN** um cliente solicita os segmentos entre dois instantes de uma reunião
- **THEN** recebe apenas os segmentos que se sobrepõem ao intervalo informado

#### Scenario: Reunião sem transcrição alguma

- **WHEN** um cliente solicita a transcrição de uma reunião que nunca foi transcrita e está na fila
- **THEN** recebe uma resposta que informa a ausência de revisão ativa e o estado da tentativa corrente
- **AND** a operação não é tratada como erro

#### Scenario: Reunião transcrita e sendo reprocessada

- **WHEN** um cliente solicita a transcrição de uma reunião que tem revisão ativa e está sendo reprocessada
- **THEN** recebe os segmentos da revisão ativa normalmente
- **AND** a resposta informa, separadamente, que há uma tentativa de reprocessamento em andamento
- **AND** a leitura do resultado anterior não é bloqueada pela tentativa em curso

### Requirement: Busca no histórico

O servidor SHALL expor uma ferramenta de busca textual sobre todo o histórico, com os mesmos filtros e a mesma insensibilidade a caixa e acentuação da busca do armazenamento, e com a possibilidade de restringir o escopo a transcrições, a notas ou a ambos.

Cada resultado SHALL identificar a reunião, seu título, o instante da ocorrência quando aplicável, a natureza do conteúdo e um recorte com contexto suficiente para o agente decidir se vale abrir a reunião inteira.

#### Scenario: Busca por assunto no histórico

- **WHEN** um cliente busca por um termo sem restringir escopo
- **THEN** recebe ocorrências em transcrições e em notas
- **AND** cada resultado traz reunião, título, instante quando aplicável, natureza e recorte com contexto

### Requirement: Paginação por cursor

Toda ferramenta que possa devolver volume grande SHALL ser paginada por **cursor opaco**, e MUST NOT ser paginada por instante de tempo, porque segmentos que cruzam a fronteira de uma janela temporal apareceriam em duas páginas e segmentos com o mesmo instante de início poderiam ser perdidos entre elas.

Cada ferramenta paginada SHALL declarar uma ordenação total e determinística própria, sobre a qual seu cursor é construído, de modo que percorrer todas as páginas devolva cada item **exatamente uma vez**, sem perda e sem duplicação:

| Ferramenta | Ordenação total |
|---|---|
| Linha de tempo e recorte por intervalo | instante de início, desempatado por trilha e por identificador do segmento |
| Listagem de reuniões | instante de início da reunião decrescente, desempatado por identificador da reunião |
| Listagem de notas de uma reunião | instante de criação, desempatado por identificador da nota |
| Busca no histórico | relevância decrescente, desempatada por identificador da reunião e por identificador do item |
| Leitura integral de uma nota | posição em caracteres dentro do conteúdo |

Nenhuma ferramenta paginada SHALL ser construída sobre uma ordenação que não conste desta tabela ou de uma extensão explícita dela.

O cursor de uma ferramenta MUST NOT ser aceito por outra.

Paginação e recorte por intervalo de tempo são operações distintas: o recorte devolve segmentos que se sobrepõem ao intervalo pedido, e SHALL ser ele próprio paginado por cursor quando exceder os limites.

Cada resposta paginada SHALL declarar se há continuação, e SHALL trazer o cursor da próxima página quando houver.

Um cursor SHALL ser invalidado quando a base sobre a qual ele foi emitido mudar entre duas chamadas:

- cursores de linha de tempo e de recorte, quando a revisão ativa da reunião mudar;
- cursores de leitura integral de nota, quando o conteúdo daquela nota for alterado ou a nota for removida;
- cursores de busca, quando a revisão ativa de qualquer reunião do conjunto de resultados mudar.

Em qualquer desses casos o servidor SHALL responder com erro que informa a invalidação e instrui a reiniciar a leitura, e MUST NOT devolver páginas construídas sobre bases diferentes.

Cursores de listagem de reuniões e de listagem de notas MUST NOT ser invalidados por inclusões ou remoções ocorridas durante a leitura; itens incluídos depois podem não aparecer e itens removidos podem desaparecer, sem que isso quebre a leitura em curso.

#### Scenario: Leitura completa de uma transcrição longa

- **WHEN** um cliente percorre todas as páginas da transcrição de uma reunião de duas horas
- **THEN** cada segmento aparece exatamente uma vez no conjunto das páginas
- **AND** nenhum segmento é perdido entre páginas adjacentes

#### Scenario: Segmentos com o mesmo instante de início na fronteira

- **WHEN** dois segmentos de trilhas diferentes começam no mesmo milissegundo e caem na fronteira entre duas páginas
- **THEN** ambos são devolvidos
- **AND** nenhum deles aparece nas duas páginas

#### Scenario: Recorte por intervalo com muitos segmentos

- **WHEN** um recorte por intervalo de tempo contém mais segmentos do que cabe numa página
- **THEN** a resposta traz a primeira página e o cursor de continuação
- **AND** percorrer o cursor devolve todos os segmentos do intervalo sem duplicação

#### Scenario: Reprocessamento durante uma leitura paginada

- **WHEN** a revisão ativa de uma reunião muda entre duas chamadas paginadas da linha de tempo
- **THEN** o cursor é recusado com erro que informa a invalidação
- **AND** nenhuma página mistura segmentos de revisões diferentes

#### Scenario: Nota editada durante a leitura integral

- **WHEN** o conteúdo de uma nota é alterado entre duas chamadas paginadas de leitura dessa nota
- **THEN** o cursor é recusado com erro que informa a invalidação

#### Scenario: Nova reunião criada durante a listagem paginada

- **WHEN** uma reunião nova é criada enquanto um cliente percorre a listagem paginada
- **THEN** o cursor continua válido
- **AND** a leitura em curso prossegue sem erro

#### Scenario: Cursor usado na ferramenta errada

- **WHEN** um cursor emitido pela listagem de reuniões é apresentado à leitura de linha de tempo
- **THEN** ele é recusado com erro

### Requirement: Limites de tamanho das respostas

O tamanho padrão de página SHALL ser de 200 segmentos, com teto configurável de 1000 segmentos por página.

Independentemente da contagem de itens, uma resposta SHALL ser limitada a 60.000 caracteres de texto. Quando esse limite for atingido antes do fim da página, a página SHALL ser encerrada no último item completo e a continuação SHALL ser sinalizada.

Quando um único item ultrapassar sozinho o limite de caracteres, ele SHALL ser devolvido com seu texto truncado e uma marcação explícita de truncamento, e MUST NOT ser omitido nem fazer a paginação travar sem avançar.

Nenhuma resposta SHALL ser truncada sem que o truncamento seja declarado. Este requisito existe porque uma resposta cortada em silêncio faria o agente raciocinar sobre metade de uma reunião acreditando tê-la lido inteira.

#### Scenario: Página encerrada pelo limite de caracteres

- **WHEN** 200 segmentos ultrapassariam 60.000 caracteres
- **THEN** a página é encerrada no último segmento completo dentro do limite
- **AND** a continuação é sinalizada com o cursor correspondente

#### Scenario: Segmento isolado maior que o limite

- **WHEN** um único segmento tem texto maior que 60.000 caracteres
- **THEN** ele é devolvido com o texto truncado e marcação explícita de truncamento
- **AND** o cursor avança para o segmento seguinte

#### Scenario: Listagem com mais reuniões que o tamanho da página

- **WHEN** o histórico tem mais reuniões do que cabe na página
- **THEN** a resposta traz a primeira página e o cursor de continuação

### Requirement: Leitura de notas

O servidor SHALL expor uma ferramenta que lista as notas de uma reunião, paginada por cursor, trazendo para cada nota seu **identificador**, tipo, autoria, instantes de criação e de última alteração, e um recorte inicial do conteúdo.

O servidor SHALL expor uma ferramenta que devolve o conteúdo integral de uma nota pelo seu identificador, paginada quando o conteúdo exceder os limites de tamanho.

Toda ferramenta que crie uma nota SHALL devolver o identificador da nota criada. Todo resultado de busca que corresponda a uma nota SHALL trazer seu identificador.

O servidor MUST NOT expor operações de atualização e remoção de nota sem que exista um caminho de leitura que forneça os identificadores exigidos por elas, sob pena de essas operações serem inalcançáveis na prática.

#### Scenario: Listagem das notas de uma reunião

- **WHEN** um cliente lista as notas de uma reunião que possui resumo e pendências
- **THEN** recebe as duas notas com identificador, tipo, autoria, instantes e recorte inicial do conteúdo

#### Scenario: Leitura integral de uma nota

- **WHEN** um cliente solicita o conteúdo de uma nota pelo identificador obtido na listagem
- **THEN** recebe o conteúdo integral da nota
- **AND** quando o conteúdo excede os limites de tamanho, recebe a primeira página e o cursor de continuação

#### Scenario: Identificador devolvido na criação

- **WHEN** um cliente cria uma nota
- **THEN** a resposta traz o identificador da nota criada
- **AND** esse identificador é suficiente para atualizá-la ou removê-la em seguida

#### Scenario: Identificador presente no resultado de busca

- **WHEN** uma busca devolve uma ocorrência em nota
- **THEN** o resultado traz o identificador daquela nota

### Requirement: Resolução do diretório de dados e contenção de escrita

O servidor SHALL resolver o diretório de dados pela mesma configuração compartilhada e pela mesma precedência que as demais superfícies do sistema, e MUST NOT derivá-lo do diretório de trabalho com que o cliente MCP o iniciou.

O servidor resolve a configuração na inicialização da sessão. Quando o diretório de dados for alterado enquanto o servidor está em execução, ele SHALL detectar a divergência antes de operar e SHALL responder com erro que informa a necessidade de reiniciar a sessão do cliente MCP, em vez de continuar operando sobre um armazenamento que deixou de ser o corrente.

As escritas do servidor SHALL respeitar a política de contenção do armazenamento: transações curtas, espera por até 5 segundos, e falha explícita ao esgotar o prazo.

#### Scenario: Servidor iniciado de diretório de trabalho arbitrário

- **WHEN** o cliente MCP inicia o servidor a partir de um diretório de trabalho qualquer
- **THEN** o servidor resolve o mesmo diretório de dados que o serviço residente

#### Scenario: Diretório de dados alterado com o servidor em execução

- **WHEN** o diretório de dados é alterado pela aplicação enquanto uma sessão MCP está ativa
- **THEN** a próxima operação responde com erro informando a necessidade de reiniciar a sessão
- **AND** nenhuma escrita é feita no armazenamento anterior

#### Scenario: Escrita de nota sob contenção

- **WHEN** um cliente grava uma nota enquanto o serviço residente publica uma transcrição
- **THEN** a nota é gravada assim que o acesso de escrita fica disponível, dentro do prazo
- **AND** a publicação da transcrição não é corrompida nem perdida

### Requirement: Fronteira de escrita restrita a notas

O servidor SHALL expor ferramentas para criar, atualizar e remover notas de reunião.

O servidor MUST NOT expor qualquer operação que renomeie ou remova reuniões, altere ou remova segmentos transcritos, remova áudio, dispare gravação, dispare reprocessamento de transcrição ou altere configuração do sistema.

Toda nota criada pelo servidor SHALL registrar como autoria o cliente que a originou.

#### Scenario: Gravação de resumo por um agente

- **WHEN** um cliente grava um resumo para uma reunião
- **THEN** a nota é criada com a autoria identificando aquele cliente
- **AND** a transcrição da reunião permanece inalterada

#### Scenario: Superfície de escrita não inclui destruição de dados

- **WHEN** as ferramentas expostas pelo servidor são inspecionadas
- **THEN** nenhuma delas permite remover reunião, alterar segmento, remover áudio ou disparar reprocessamento

#### Scenario: Remoção de nota criada anteriormente

- **WHEN** um cliente remove uma nota pelo identificador
- **THEN** apenas a nota é removida
- **AND** a reunião, seus segmentos e suas demais notas permanecem intactos

### Requirement: Erros legíveis e acionáveis

Falhas SHALL ser devolvidas com mensagem que descreve a causa em linguagem natural e, quando aplicável, nomeia o identificador ou parâmetro envolvido.

Quando o armazenamento não pode ser aberto, a mensagem SHALL nomear o caminho esperado e a causa provável, para que o usuário corrija a configuração do cliente.

O servidor MUST NOT devolver rastro de pilha como conteúdo de resposta de ferramenta.

#### Scenario: Identificador de reunião inexistente

- **WHEN** um cliente solicita uma reunião por um identificador que não existe
- **THEN** recebe erro que nomeia o identificador solicitado

#### Scenario: Armazenamento inacessível

- **WHEN** o servidor não consegue abrir o armazenamento no caminho configurado
- **THEN** o erro nomeia o caminho esperado e a causa provável
- **AND** nenhum rastro de pilha é devolvido

### Requirement: Verificação do registro no cliente MCP

O sistema SHALL oferecer uma forma de verificar se o servidor está registrado corretamente na configuração do cliente MCP do usuário, reportando o caminho do arquivo de configuração inspecionado e o resultado.

O diagnóstico de ambiente SHALL incluir essa verificação como um item, com estado `aviso` quando o registro estiver ausente.

O sistema SHALL apresentar o trecho de configuração exato a ser adicionado quando o registro estiver ausente.

#### Scenario: Servidor não registrado no cliente

- **WHEN** o diagnóstico roda com o servidor ausente da configuração do cliente MCP
- **THEN** o item é reportado como `aviso`
- **AND** o trecho de configuração a adicionar é apresentado

#### Scenario: Servidor registrado corretamente

- **WHEN** o diagnóstico roda com o servidor presente e apontando para um executável válido
- **THEN** o item é reportado como `ok`
