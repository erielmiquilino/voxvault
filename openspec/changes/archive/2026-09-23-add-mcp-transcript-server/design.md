## Context

Ver `proposal.md — Why`. A fase anterior deixou o histórico gravado e consultável num armazenamento local que já suporta leitura concorrente. Esta fase adiciona uma segunda superfície de acesso a esse mesmo armazenamento, destinada a agentes.

O cliente principal é o Claude Desktop do usuário, com Codex como segundo cliente. Ambos falam MCP por entrada e saída padrão e lançam o servidor como processo filho.

## Goals / Non-Goals

**Goals:**

- Entregar, sem interface gráfica, todo o valor que motivou o projeto: perguntar ao agente sobre as próprias reuniões e receber resposta fundamentada no que foi efetivamente dito.
- Tornar o resumo um artefato persistente e pesquisável, não uma mensagem de chat que se perde.
- Impedir, por construção e não por disciplina, que um agente danifique o registro do que foi dito.

**Non-Goals:**

- Não gerar resumos. O servidor entrega matéria-prima; a síntese é do cliente.
- Não expor gravação, reprocessamento ou configuração. Decisão de produto registrada abaixo.
- Não autenticar nem autorizar. O servidor roda como o usuário, na máquina do usuário.
- Não servir por rede.

## Decisions

### O servidor lê o armazenamento diretamente, não a aplicação

Um agente precisa conseguir consultar reuniões com o VoxVault fechado, que é a situação mais comum — o usuário abre o Claude Desktop, não a ferramenta de gravação. Fazer o servidor falar com a aplicação principal criaria uma dependência de disponibilidade que nada justifica.

O modo de escrita adiantada do banco, já exigido na fase anterior, permite que o servidor leia enquanto a gravação escreve, sem bloqueio de nenhum dos lados.

*Alternativa considerada:* expor a API local da aplicação. Rejeitada — obrigaria a aplicação a estar sempre em execução para que o agente funcionasse.

### Processo e ponto de entrada próprios

O servidor é um executável separado da aplicação, com dependências mínimas. Ele não importa captura de áudio nem runtime de inferência, de modo que sobe rápido e não falha por causa de biblioteca de GPU ausente — o cliente MCP inicia o servidor a cada sessão, e um servidor que demora ou quebra na subida é um servidor que o usuário desativa.

### A escrita é restrita a notas, por construção

O usuário escolheu que o agente possa gravar resumos de volta, e não que possa gerenciar o acervo. A fronteira é estabelecida pela ausência das ferramentas correspondentes, não por checagem em tempo de execução: o que não é exposto não pode ser invocado.

Isso também protege contra o caso em que o conteúdo de uma transcrição — que é texto produzido por terceiros numa reunião — acabe interpretado como instrução por um agente. Mesmo que isso ocorra, a pior consequência possível é uma nota indevida, nunca a perda de uma gravação ou de uma transcrição.

### Nota é interpretação e nunca se mistura ao registro

Transcrição e nota nunca aparecem no mesmo bloco de uma exportação nem no mesmo tipo de resultado de busca. Um resumo gerado por modelo pode conter erro, e a distinção visual entre "isto foi dito" e "isto foi interpretado" precisa sobreviver à leitura apressada, meses depois.

### Paginação obrigatória com truncamento sempre explícito

Uma reunião de duas horas rende milhares de segmentos e não cabe no contexto útil de nenhum cliente. O risco real não é a resposta grande, é a resposta truncada em silêncio: o agente raciocina sobre metade da reunião acreditando tê-la lido inteira, e o usuário não tem como perceber.

Por isso toda resposta truncada declara o truncamento e diz como continuar, e a paginação da linha de tempo é por intervalo de tempo, que é o recorte com significado para quem consulta uma reunião.

### A autoria da nota registra o cliente de origem

Saber se um resumo veio do usuário, do Claude Desktop ou do Codex importa para julgar a confiança nele meses depois. O identificador do cliente é capturado na inicialização da sessão MCP.

### Verificação do registro no cliente vira item de diagnóstico

O modo de falha mais provável desta fase não é defeito de código, é o servidor não estar registrado na configuração do cliente, ou estar apontando para um caminho errado. Tratar isso como item do diagnóstico, com o trecho de configuração pronto para colar, transforma uma hora de confusão em um comando.

### Paginação por cursor, não por janela de tempo

Paginar a linha de tempo por intervalo parecia natural — é o recorte com significado para quem consulta uma reunião — mas quebra em dois pontos. Um segmento que cruza a fronteira da janela aparece em duas páginas adjacentes, porque o recorte inclui tudo que se sobrepõe ao intervalo. E avançar pelo próximo instante perde segmentos que começam no mesmo milissegundo, o que acontece toda vez que as duas trilhas falam juntas.

O cursor se apoia na ordenação total que o armazenamento garante — instante, desempatado por trilha e por identificador —, então percorrer as páginas devolve cada segmento exatamente uma vez. Recorte por intervalo continua existindo como operação própria, e é ele mesmo paginado.

O cursor é invalidado quando a revisão ativa muda no meio de uma leitura. Sem isso, um reprocessamento concorrente costuraria duas revisões numa mesma leitura sem que o agente percebesse.

### Limites em caracteres, além de itens, e item isolado nunca trava a paginação

Contar só itens não protege o contexto do cliente, porque segmentos variam muito de tamanho. Por isso o teto é duplo: 200 segmentos por página e 60.000 caracteres por resposta, o que vier primeiro.

O caso do item que sozinho estoura o teto foi tratado explicitamente: ele é devolvido truncado e marcado, e o cursor avança. Omiti-lo perderia conteúdo em silêncio; devolvê-lo inteiro estouraria o limite; e nenhuma das duas pode virar um laço que não progride.

### Escrever notas exige poder lê-las

Atualizar e remover nota dependem de identificador, e nenhuma ferramenta de leitura devolvia identificadores de nota — a listagem de reuniões só trazia a contagem. As operações de escrita eram, na prática, inalcançáveis.

A regra que fica registrada é geral: nenhuma operação de escrita é exposta sem um caminho de leitura que forneça os identificadores que ela exige.

### O servidor resolve a configuração compartilhada, não o diretório de trabalho

O cliente MCP lança o servidor de um diretório arbitrário. Derivar dali o diretório de dados faria o agente enxergar um histórico diferente do da aplicação, sem nenhum sinal de que isso aconteceu.

Como o servidor resolve a configuração na inicialização da sessão, uma troca de diretório feita depois o deixaria apontando para o lugar errado. A decisão é detectar a divergência e exigir reinício da sessão, em vez de seguir operando sobre um armazenamento que deixou de ser o corrente.

## Risks / Trade-offs

**Conteúdo de transcrição interpretado como instrução pelo agente** → A transcrição contém fala de terceiros, e um participante pode dizer qualquer coisa numa reunião. Mitigação estrutural: a superfície de escrita não contém nenhuma operação destrutiva, então o pior caso é uma nota indevida, que o usuário remove.

**Servidor lento ou instável na subida faz o usuário desativá-lo** → Mitigação: ponto de entrada enxuto, sem importar captura de áudio nem runtime de inferência, e falha de abertura de armazenamento reportada como mensagem acionável em vez de quebra.

**Paginação mal dimensionada degradando a experiência** → Página pequena demais obriga o agente a muitas chamadas; grande demais estoura o contexto. Mitigação: padrão conservador, teto configurável, e o truncamento sempre declarado para que o agente possa decidir.

**Notas acumulando versões contraditórias da mesma reunião** → Várias notas do mesmo tipo são permitidas de propósito, porque forçar sobrescrita destruiria trabalho anterior. Trade-off aceito: cabe ao usuário limpar, e a autoria com instante torna a ordem cronológica evidente.

**Migração do esquema para acomodar notas** → Mitigação: a migração apenas acrescenta, sem tocar em reuniões ou segmentos, e o requisito de não perda de dados já vale para toda migração.
