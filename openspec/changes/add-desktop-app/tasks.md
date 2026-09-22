## 1. Serviço local no núcleo

- [ ] 1.1 Implementar o serviço local que expõe as operações já existentes do núcleo (gravação, importação, consulta, busca, notas, configuração, diagnóstico), ligado apenas à interface de rede local da máquina; verificar por teste que uma conexão de outra origem não é aceita
- [ ] 1.2 Implementar o segredo gerado por inicialização e a recusa de requisições que não o apresentem, e verificar por teste que uma requisição sem segredo é rejeitada sem executar operação
- [ ] 1.3 Implementar o canal de atualização contínua para estado de gravação, níveis de áudio por trilha, avisos e progresso de transcrição; verificar por teste de integração que um cliente conectado recebe as atualizações
- [ ] 1.4 Implementar o cálculo de nível por trilha no mesmo ponto em que o áudio já é processado pela captura, com taxa de envio limitada; verificar por medição que a captura não passa a processar o áudio duas vezes
- [ ] 1.5 Implementar a seleção de porta livre na inicialização e a publicação do endereço e do segredo no **ponto de encontro de escopo de usuário**, acessível igualmente por todas as superfícies e não apenas por quem iniciou o serviço; verificar por teste que duas inicializações sucessivas não colidem e que um cliente que não iniciou o serviço consegue localizá-lo e autenticar-se

## 2. Aplicativo hospedeiro

- [ ] 2.1 Criar o projeto Tauri com frontend Svelte e Vite, e verificar que o build de desenvolvimento abre uma janela
- [ ] 2.2 Implementar o preparo automático do ambiente de execução do núcleo na primeira abertura, com progresso visível; verificar apagando o ambiente e reabrindo o aplicativo
- [ ] 2.3 Implementar a apresentação da falha de preparo com causa e ação corretiva, sem abrir a interface principal em estado aparentemente funcional; verificar por teste com o preparo forçado a falhar
- [ ] 2.4 Implementar a conexão ao serviço residente, iniciando-o apenas quando não houver instância ativa para o mesmo diretório de dados; verificar abrindo o aplicativo com o serviço já em execução e conferindo que nenhuma segunda instância é criada
- [ ] 2.4.1 Verificar que uma gravação iniciada pela linha de comando aparece como ativa ao abrir o aplicativo, e que o aplicativo consegue encerrá-la
- [ ] 2.4.2 Implementar o fechamento do aplicativo **sem** encerrar o serviço quando há gravação ativa ou fila pendente; verificar fechando o aplicativo com duas transcrições pendentes, conferindo que elas concluem e que ao reabrir as reuniões aparecem prontas
- [ ] 2.4.3 Verificar que, sem gravação ativa e com a fila vazia, fechar o aplicativo deixa o serviço encerrar-se por ociosidade dentro do período configurado, sem processo remanescente
- [ ] 2.4.4 Verificar por teste que o aplicativo não abre dispositivos de áudio nem executa transcrição por conta própria, operando apenas como cliente do serviço
- [ ] 2.5 Implementar a detecção de queda do serviço com reinício automático e aviso ao usuário, e verificar matando o processo do serviço com o aplicativo aberto
- [ ] 2.6 Implementar a interrupção das tentativas após 3 falhas consecutivas de inicialização dentro de 60 segundos, apresentando o estado de falha com a causa; verificar por teste
- [ ] 2.7 Implementar a instância única com foco na janela existente numa segunda abertura, e verificar executando o aplicativo duas vezes
- [ ] 2.8 Implementar a confirmação ao fechar com gravação ativa, informando a duração corrente, com encerramento limpo ao confirmar e permanência ao cancelar; verificar nos dois caminhos com uma gravação real
- [ ] 2.9 Implementar o tratamento do pedido de encerramento do sistema operacional finalizando a gravação de forma limpa, e verificar que o áudio permanece utilizável
- [ ] 2.10 Implementar a barra de estado permanente com saúde do serviço local e resultado do diagnóstico, e verificar que ela reflete o serviço fora do ar

## 3. Interface de gravação

- [ ] 3.1 Implementar o controle de gravação com iniciar, pausar, retomar e encerrar, habilitando apenas as ações válidas para o estado corrente; verificar executando um ciclo completo
- [ ] 3.2 Implementar o campo de título opcional no início, com queda para o título padrão quando vazio; verificar nos dois caminhos
- [ ] 3.3 Implementar a apresentação do tempo decorrido excluindo períodos pausados, e verificar por teste com uma pausa no meio
- [ ] 3.4 Implementar os medidores de nível ao vivo por trilha, e verificar gravando com o microfone mudo que a trilha de entrada permanece visivelmente em zero enquanto a outra reage
- [ ] 3.5 Implementar a indicação por trilha de que está ou não capturando, e verificar com a perda definitiva de um dispositivo
- [ ] 3.6 Implementar a apresentação dos avisos de eco, trilha silenciosa, troca de dispositivo e desalinhamento no momento em que ocorrem, sem exigir interação; verificar com uma gravação iniciada em caixas de som
- [ ] 3.7 Garantir que os avisos apresentados durante a gravação permanecem consultáveis na reunião encerrada, e verificar após o fim de uma gravação com avisos
- [ ] 3.8 Implementar a apresentação da causa em linguagem natural quando o início da gravação falha, mantendo o estado ocioso; verificar com um dispositivo fixado inexistente
- [ ] 3.9 Implementar a importação por seleção e por arrastar e soltar, com múltiplos arquivos, progresso e resultado individual; verificar arrastando três arquivos sendo um inválido

## 4. Biblioteca de reuniões

- [ ] 4.1 Implementar a lista de reuniões da mais recente para a mais antiga com título, data, duração, estado, origem e indicação de notas; verificar após algumas gravações e importações
- [ ] 4.2 Implementar os filtros de período, estado e termo no título, e verificar cada um
- [ ] 4.3 Implementar a atualização do estado e do progresso de transcrição sem interação do usuário, e verificar mantendo a lista aberta durante uma transcrição até ela concluir
- [ ] 4.4 Implementar a indicação de falha na lista com o motivo consultável a partir da reunião, e verificar com uma reunião que falhou
- [ ] 4.5 Implementar a leitura da linha de tempo com instante, falante e texto, distinguindo visualmente usuário, demais participantes e falante desconhecido; verificar numa reunião gravada e numa importada
- [ ] 4.6 Implementar a apresentação de trechos com fala sobreposta mantendo os dois segmentos legíveis e a sobreposição perceptível; verificar numa reunião com interrupção real
- [ ] 4.7 Implementar a cópia de trecho selecionado com instante e atribuição de falante, e verificar colando o resultado
- [ ] 4.8 Implementar o reprodutor de áudio com posicionamento ao acionar um segmento, destaque e acompanhamento automático do segmento corrente; verificar sobre uma reunião real
- [ ] 4.9 Implementar a seleção de trilha na reprodução entre entrada, sistema e ambas mixadas, e verificar cada opção
- [ ] 4.10 Implementar a indisponibilidade das ações de reprodução com causa explicada quando o áudio foi removido, e verificar sobre uma reunião sem áudio
- [ ] 4.11 Implementar a busca com filtros de período e escopo, abrindo a reunião posicionada no trecho ao acionar um resultado; verificar com resultado de transcrição e com resultado de nota
- [ ] 4.12 Implementar a área de notas separada da linha de tempo, com tipo, autoria e instante, e verificar sobre uma reunião com nota gravada por agente
- [ ] 4.13 Implementar a criação, edição e remoção de notas pelo usuário, com autoria registrada como usuário, e verificar que a nota criada é encontrável pela busca
- [ ] 4.14 Implementar as ações de renomear, reprocessar, remover áudio, exportar e abrir o diretório no gerenciador de arquivos, e verificar cada uma
- [ ] 4.15 Implementar a confirmação de ações destrutivas descrevendo exatamente o que será perdido, e verificar na remoção de áudio
- [ ] 4.16 Implementar a apresentação desabilitada com causa das ações inválidas para o estado corrente, e verificar que reprocessar aparece desabilitado numa reunião sem áudio

## 5. Configurações

- [ ] 5.1 Implementar a apresentação dos dispositivos com identificadores persistentes e a marcação separada dos padrões de comunicações e de multimídia, e verificar numa máquina onde os dois padrões divergem
- [ ] 5.1.1 Implementar a seleção por trilha entre seguir o padrão de um papel — comunicações por omissão — ou fixar um dispositivo específico, com persistência entre aberturas; verificar reabrindo o aplicativo
- [ ] 5.1.2 Implementar a explicação em linguagem natural da diferença entre os papéis e o aviso de que a trilha do sistema captura toda a mistura do dispositivo de saída; verificar por inspeção da interface
- [ ] 5.2 Implementar a sinalização de dispositivo fixado indisponível com explicação de que a gravação falhará, e verificar desconectando o dispositivo
- [ ] 5.3 Implementar a indisponibilidade da alteração de dispositivos durante gravação ativa, e verificar com uma gravação em andamento
- [ ] 5.4 Implementar a seleção de motor e modelo apresentando exigência aproximada de memória e velocidade relativa, e verificar que a troca afeta apenas as próximas transcrições
- [ ] 5.5 Implementar a indicação de motor não executável no ambiente atual com a causa, antes de qualquer tentativa de transcrição; verificar selecionando um modelo grande demais
- [ ] 5.6 Implementar a apresentação do diretório de dados com espaço livre e a sinalização visível quando abaixo do limiar, informando o consumo aproximado por hora; verificar por teste com limiar elevado artificialmente
- [ ] 5.7 Implementar a alteração do diretório de dados com validação de escrita, gravada na configuração compartilhada e não apenas no estado da aplicação; verificar por teste que outro processo passa a resolver o novo caminho
- [ ] 5.7.1 Implementar o bloqueio da alteração durante gravação ativa, transcrição em andamento ou fila pendente, e verificar nos três casos
- [ ] 5.7.2 Implementar a apresentação em modo somente leitura quando o diretório efetivo vier de argumento ou variável de ambiente, nomeando a fonte e explicando o que fazer fora da aplicação; verificar por teste com a variável de ambiente definida que a alteração não é oferecida
- [ ] 5.7.3 Implementar o reinício conduzido do serviço residente após a alteração, com confirmação ao usuário de que ele voltou apontando para o novo diretório antes de concluir a operação; verificar executando a troca de ponta a ponta
- [ ] 5.7.4 Implementar o aviso explícito de que o conteúdo anterior permanece no caminho antigo e de que clientes MCP em execução continuam ligados ao diretório anterior até reiniciarem a sessão; verificar alterando o diretório com um cliente MCP aberto e conferindo que ele responde com o erro de sessão desatualizada em vez de operar no armazenamento antigo
- [ ] 5.8 Implementar a edição do vocabulário global e do vocabulário por reunião, sem afetar transcrições já produzidas; verificar nos dois níveis
- [ ] 5.9 Implementar a apresentação do diagnóstico item a item com ação corretiva e reexecução sob demanda, e verificar corrigindo um pré-requisito e reexecutando sem reiniciar o aplicativo
- [ ] 5.10 Implementar a apresentação copiável do trecho de configuração do servidor MCP quando o registro estiver ausente, e verificar removendo o registro do cliente

## 6. Verificação de ponta a ponta

- [ ] 6.1 Gerar o build empacotado do aplicativo e executar todas as verificações seguintes sobre ele, não sobre o servidor de desenvolvimento
- [ ] 6.2 Gravar uma reunião real de ponta a ponta pela interface e verificar que a transcrição aparece na biblioteca e é legível com atribuição correta de falante
- [ ] 6.3 Construir o procedimento de medição que agrega o conjunto completo de processos — hospedeiro, todos os processos do componente de navegação e descendentes, serviço residente e descendentes — com amostragem a cada 5 segundos, e verificar por inspeção que nenhum processo do conjunto fica de fora da soma
- [ ] 6.3.1 Medir durante uma gravação de 60 minutos com a janela aberta e verificar que o uso médio de processador fica em no máximo 8% do total da máquina, que nenhuma janela de 60 segundos ultrapassa 15%, e que a memória residente somada fica em no máximo 700 MB
- [ ] 6.3.2 Repetir a medição com a janela minimizada e verificar que a memória residente somada fica em no máximo 400 MB e que o uso de processador não aumenta
- [ ] 6.3.3 Verificar que a taxa de atualização dos medidores de nível está limitada a no máximo 20 atualizações por segundo por trilha
- [ ] 6.4 Verificar que nenhuma transcrição é executada enquanto há gravação ativa, acompanhando a fila durante uma gravação
- [ ] 6.4.1 Verificar que as ações de reprodução ficam indisponíveis durante uma gravação ativa, com a causa explicada, e que nenhum áudio reproduzido pelo aplicativo entra na gravação em curso
- [ ] 6.5 Conferir um trecho em dúvida acionando o segmento e ouvindo o áudio correspondente, verificando que o destaque acompanha a reprodução
- [ ] 6.6 Buscar um assunto no histórico e verificar que acionar o resultado abre a reunião posicionada no trecho
- [ ] 6.7 Fechar o aplicativo durante uma gravação, confirmar, e verificar que a reunião foi encerrada de forma limpa, transcrita e está legível
