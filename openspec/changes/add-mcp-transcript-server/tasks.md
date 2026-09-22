## 1. Notas de reunião

- [ ] 1.1 Estender o esquema com a tabela de notas (identificador, reunião, tipo, conteúdo, autoria, instantes de criação e alteração) por migração aditiva, e verificar por teste que um banco existente é migrado sem perder reuniões nem segmentos
- [ ] 1.2 Implementar a criação de nota com validação do tipo e da existência da reunião, falhando com identificação da reunião ausente; verificar por testes do caminho feliz e do caminho de falha
- [ ] 1.3 Implementar a coexistência de várias notas do mesmo tipo numa reunião, e verificar por teste que a segunda não sobrescreve a primeira
- [ ] 1.4 Implementar a atualização de nota preservando identificador e autoria original e atualizando o instante de alteração, e verificar por teste
- [ ] 1.5 Implementar a remoção de nota por identificador sem afetar as demais, e verificar por teste com três notas
- [ ] 1.6 Estender o índice de busca para cobrir o conteúdo das notas, com resultados distinguindo nota de transcrição e trazendo tipo e autoria; verificar por teste com um termo presente nos dois lugares
- [ ] 1.7 Implementar a restrição de escopo da busca a transcrições, a notas ou a ambos, e verificar por testes dos três escopos
- [ ] 1.8 Verificar por teste que reprocessar a transcrição de uma reunião com notas substitui os segmentos e preserva todas as notas
- [ ] 1.9 Estender a exportação legível com seção própria de notas, separada da linha de tempo, e a estruturada com todos os campos das notas; verificar abrindo a exportação de uma reunião com resumo e pendências
- [ ] 1.10 Expor os comandos de linha de comando para listar, criar, atualizar e remover notas, e verificar executando cada um

## 2. Servidor MCP — esqueleto e leitura

- [ ] 2.1 Criar o ponto de entrada `voxvault-mcp` como executável separado, sem importar captura de áudio nem runtime de inferência; verificar medindo o tempo de subida e conferindo por inspeção de importações que os módulos pesados não são carregados
- [ ] 2.2 Implementar a abertura do armazenamento em modo compatível com leitura concorrente, e verificar por teste de integração que o servidor lê enquanto outro processo escreve
- [ ] 2.2.1 Implementar a resolução do diretório de dados pela configuração compartilhada, independente do diretório de trabalho com que o cliente MCP lançou o servidor; verificar por teste lançando o servidor de dois diretórios distintos e obtendo o mesmo armazenamento
- [ ] 2.2.2 Implementar a detecção de divergência quando o diretório de dados é alterado com a sessão ativa, respondendo com erro que instrui a reiniciar a sessão do cliente; verificar por teste que nenhuma escrita é feita no armazenamento anterior
- [ ] 2.2.3 Implementar a aderência da escrita do servidor à política de contenção do armazenamento — transações curtas, espera de até 5 segundos, falha explícita —, e verificar por teste gravando notas enquanto o serviço residente publica uma transcrição
- [ ] 2.3 Implementar a ferramenta de listagem de reuniões com filtros de período, estado e termo no título, ordenada da mais recente para a mais antiga, trazendo todos os campos exigidos incluindo contagem de notas; verificar por teste
- [ ] 2.4 Implementar a ferramenta de leitura da linha de tempo de uma reunião com instante, atribuição de falante e texto; verificar por teste sobre uma reunião com segmentos das duas trilhas
- [ ] 2.5 Implementar a ferramenta de recorte por intervalo de tempo devolvendo os segmentos que se sobrepõem ao intervalo, e verificar por teste incluindo segmentos parcialmente sobrepostos nas bordas
- [ ] 2.6 Implementar a resposta que devolve, separadamente, a disponibilidade da revisão ativa e o estado da tentativa corrente; verificar por testes com reunião nunca transcrita na fila, e com reunião já transcrita sendo reprocessada — nesta última, os segmentos da revisão ativa devem vir normalmente junto com a indicação do reprocessamento em andamento
- [ ] 2.7 Implementar a ferramenta de busca com os filtros e escopos da especificação, devolvendo reunião, título, instante, natureza e recorte com contexto; verificar por teste

## 3. Paginação e limites

- [ ] 3.1 Implementar paginação por cursor opaco com uma ordenação total declarada **por ferramenta**, conforme a tabela da especificação — linha de tempo, listagem de reuniões, listagem de notas, busca e leitura integral de nota têm chaves distintas; verificar por teste que cada ferramenta percorre seu conjunto sem perda nem duplicação, com página padrão de 200 e teto de 1000
- [ ] 3.1.1 Implementar a recusa de um cursor emitido por uma ferramenta quando apresentado a outra, e verificar por teste
- [ ] 3.1.2 Implementar as regras diferenciadas de invalidação — cursores de linha de tempo, busca e leitura de nota invalidam quando sua base muda; cursores de listagem não invalidam por inclusão ou remoção durante a leitura; verificar por testes de cada caso, inclusive nota editada entre páginas e reunião nova criada durante a listagem
- [ ] 3.2 Implementar a declaração explícita de continuação e o cursor da próxima página em toda resposta paginada, e verificar por teste que a indicação está presente sempre que há continuação
- [ ] 3.3 Verificar por teste, com uma reunião sintética contendo segmentos de trilhas diferentes começando no mesmo milissegundo, que percorrer todas as páginas devolve cada segmento **exatamente uma vez** — sem perda entre páginas e sem duplicação na fronteira
- [ ] 3.4 Implementar o recorte por intervalo de tempo como operação distinta da paginação, ela própria paginada por cursor, e verificar por teste com segmentos parcialmente sobrepostos nas bordas do intervalo
- [ ] 3.5 Implementar o limite de 60.000 caracteres por resposta com encerramento da página no último item completo, e verificar por teste com segmentos longos
- [ ] 3.6 Implementar o tratamento do item isolado que excede sozinho o limite — devolvido truncado com marcação explícita e cursor avançando —, e verificar por teste que a paginação não trava sem progredir
- [ ] 3.7 Implementar a invalidação do cursor quando a revisão ativa muda entre duas chamadas, com erro que instrui a reiniciar a leitura; verificar por teste reprocessando uma reunião no meio de uma leitura paginada
- [ ] 3.8 Verificar, com uma reunião real de mais de uma hora, que a leitura paginada percorre a transcrição inteira sem lacunas e sem duplicação entre páginas

## 4. Escrita de notas pelo MCP

- [ ] 4.0 Implementar a ferramenta de listagem paginada das notas de uma reunião, trazendo identificador, tipo, autoria, instantes e recorte inicial do conteúdo; verificar por teste sobre uma reunião com resumo e pendências
- [ ] 4.0.1 Implementar a ferramenta de leitura integral de uma nota por identificador, paginada quando o conteúdo excede os limites; verificar por teste com uma nota longa
- [ ] 4.0.2 Verificar por teste que o identificador devolvido na criação de uma nota é suficiente para atualizá-la e removê-la em seguida, e que resultados de busca em nota trazem o identificador — fechando o ciclo entre escrita e leitura
- [ ] 4.1 Implementar as ferramentas de criar, atualizar e remover nota, registrando como autoria o cliente de origem capturado na inicialização da sessão; verificar por teste que a autoria chega corretamente à nota
- [ ] 4.2 Verificar, por inspeção automatizada da lista de ferramentas expostas, que nenhuma permite remover reunião, alterar segmento, remover áudio, disparar gravação ou disparar reprocessamento
- [ ] 4.3 Verificar por teste que criar, atualizar ou remover nota deixa segmentos, metadados de processamento, título e áudio da reunião inalterados

## 5. Erros e diagnóstico

- [ ] 5.1 Implementar mensagens de erro em linguagem natural nomeando identificador ou parâmetro envolvido, sem rastro de pilha no conteúdo da resposta; verificar por teste com identificador inexistente
- [ ] 5.2 Implementar a mensagem específica de armazenamento inacessível nomeando o caminho esperado e a causa provável, e verificar por teste apontando para um caminho inválido
- [ ] 5.3 Implementar a verificação do registro do servidor na configuração do cliente MCP, reportando o arquivo inspecionado e o resultado; verificar por teste com configuração presente e ausente
- [ ] 5.4 Adicionar essa verificação como item do diagnóstico de ambiente com estado `aviso` quando ausente, apresentando o trecho de configuração a colar; verificar executando o diagnóstico nas duas situações

## 6. Verificação de ponta a ponta

- [ ] 6.1 Registrar o servidor no Claude Desktop da máquina e verificar que as ferramentas aparecem e respondem
- [ ] 6.2 Verificar, com o VoxVault completamente fechado, que é possível listar reuniões, ler uma transcrição e buscar no histórico pelo cliente MCP
- [ ] 6.3 Pedir ao cliente um resumo de uma reunião real e gravá-lo como nota, verificando em seguida pela linha de comando que a nota existe com a autoria correta
- [ ] 6.4 Verificar que o resumo gravado é encontrável pela busca e aparece em seção própria na exportação da reunião
- [ ] 6.5 Verificar, com uma gravação em andamento, que o cliente MCP consulta o histórico sem afetar a gravação e vê a reunião corrente com estado de gravação
- [ ] 6.5.1 Verificar, com os dois clientes MCP gravando notas simultaneamente enquanto o serviço residente publica uma transcrição durante uma gravação ativa, que todas as escritas se completam e que nenhum áudio é perdido
- [ ] 6.6 Repetir a verificação de registro e leitura com o Codex como segundo cliente
