## 1. Armazenamento e modelo de dados

- [ ] 1.1 Criar o esquema do banco com as tabelas de reunião e de segmento, incluindo estado de processamento, origem, trilha e atribuição de falante; verificar por teste que um banco novo é criado com todas as tabelas esperadas
- [ ] 1.2 Habilitar o modo de escrita adiantada no banco e verificar, com dois processos simultâneos num teste de integração, que a leitura não bloqueia durante uma escrita
- [ ] 1.3 Implementar o controle de versão do esquema com migrações aplicadas na abertura, e verificar por teste que um banco de versão anterior é migrado sem perder registros
- [ ] 1.4 Implementar a recusa de abrir banco de versão superior à conhecida, e verificar por teste que a abertura falha informando as duas versões e sem escrever nada
- [ ] 1.5 Criar o índice de texto completo sobre o texto dos segmentos com remoção de diacríticos, e verificar por teste que buscar sem acento encontra ocorrências acentuadas e vice-versa
- [ ] 1.6 Implementar o modelo de revisões — toda execução de transcrição produz uma revisão identificada por motor, configuração e vocabulário, e todo segmento pertence a exatamente uma revisão; verificar por teste que a linha de tempo nunca combina segmentos de revisões diferentes
- [ ] 1.6.1 Implementar a publicação atômica de uma revisão, com os segmentos de todas as trilhas passando a valer de uma só vez e a revisão em construção invisível a consultas, busca e exportação; verificar por teste consultando durante uma publicação
- [ ] 1.6.2 Implementar a política de revisão parcial — só se torna ativa quando não existe revisão anterior mais completa —, e verificar por testes dos três casos: primeira transcrição parcial, reprocessamento parcial sobre revisão completa, e falha em todas as trilhas
- [ ] 1.6.3 Implementar a ordenação total e determinística dos segmentos por instante de início desempatado por trilha e identificador, e verificar por teste que duas consultas sucessivas sobre a mesma revisão devolvem exatamente a mesma ordem, inclusive com segmentos de instante idêntico
- [ ] 1.6.4 Implementar o congelamento de motor, configuração e vocabulário no início da tentativa, imutáveis durante toda ela; verificar por teste alterando a configuração entre a transcrição da primeira e da segunda trilha e conferindo que ambas usaram a configuração congelada
- [ ] 1.6.5 Implementar a atualização do índice de busca na mesma transação que publica a revisão, e verificar por teste que o índice nunca aponta para revisão que não é a ativa
- [ ] 1.6.6 Implementar o registro do identificador de revisão nos arquivos de exportação e a regeração automática quando ele divergir da revisão ativa, inclusive na inicialização; verificar por teste matando o processo entre a publicação e a regeração e conferindo que as exportações são refeitas e que nenhuma superfície apresenta a divergente como corrente
- [ ] 1.7 Implementar a política de contenção de escrita — transações curtas, espera de até 5 segundos, falha explícita ao esgotar —, e verificar por teste de integração com três escritores concorrentes que todas as escritas se completam sem perda
- [ ] 1.8 Garantir que a espera por contenção não ocorre no caminho de execução da captura, desacoplando persistência de captura; verificar por teste que uma disputa prolongada pelo banco durante uma gravação não provoca perda de áudio
- [ ] 1.9 Implementar a serialização de migrações entre processos, com espera de até 30 segundos e falha explícita ao esgotar; verificar por teste de integração abrindo simultaneamente um banco desatualizado a partir de dois processos e conferindo que as migrações são aplicadas uma única vez

## 2. Captura de áudio

> **Portão de validação.** As tarefas 2.0.x decidem o backend de captura e precisam ser concluídas **antes** de qualquer trabalho de alinhamento. Está verificado que o backend WASAPI do PortAudio, usado pelo PyAudioWPatch, não atende: ele passa `NULL` para posição de dispositivo e timestamp em todas as chamadas a `IAudioCaptureClient::GetBuffer`, calcula `inputBufferAdcTime` como relógio corrente somado à latência estimada, e não inspeciona a flag de descontinuidade. Construir o alinhamento sobre ele reproduziria o defeito com aparência de correção.

- [ ] 2.0 Levantar as opções de acesso à captura WASAPI que expõem posição de dispositivo, timestamp de alta resolução e sinalização de descontinuidade por pacote — ligação nativa direta a partir do Python, componente nativo auxiliar, ou captura no processo hospedeiro —, e registrar a comparação em `design.md`
- [ ] 2.0.1 Construir uma prova de conceito mínima com a opção escolhida que abra uma trilha de entrada e uma de loopback e imprima, por pacote, posição de dispositivo, timestamp e flags; verificar que os três valores chegam preenchidos
- [ ] 2.0.2 Submeter a prova de conceito a carga de processador durante a captura e verificar que os instantes reportados **não** se deslocam com o atraso de entrega — este é o teste que reprova o backend do PortAudio e aprova um backend adequado
- [ ] 2.0.3 Provocar perda de pacotes e verificar que a sinalização de descontinuidade do sistema operacional é recebida
- [ ] 2.0.4 Registrar a decisão de backend e o resultado das medições, **incluindo a faixa de versão de Python que o backend escolhido impõe** — confirmando ou revisando o `requires-python` fixado na Fase 0, cujo teto `<3.13` perdeu a origem quando o `PyAudioWPatch` foi reprovado; nenhuma tarefa de alinhamento começa antes disso

- [ ] 2.1 Implementar a enumeração de dispositivos de entrada e de saída com **identificadores persistentes entre reinicializações e reconexões**, marcando separadamente o padrão de comunicações e o padrão de multimídia; verificar executando na máquina que a lista corresponde ao painel de som do Windows e que os identificadores sobrevivem a uma reconexão do dispositivo
- [ ] 2.1.1 Implementar a seleção por papel, com comunicações como padrão, independentemente para cada trilha; verificar por teste com padrões de papéis divergentes que o dispositivo escolhido é o do papel configurado
- [ ] 2.2 Implementar a abertura da trilha de entrada e da trilha de loopback do dispositivo de saída, e verificar gravando dez segundos que os dois arquivos contêm áudio audível e distinto
- [ ] 2.3 Implementar o posicionamento por **posição de dispositivo ancorada no timestamp de aquisição**, depositando em fila sem executar trabalho de CPU ou disco no callback; verificar por teste que o callback retorna em tempo desprezível sob carga simulada
- [ ] 2.3.1 Implementar a âncora por trilha (posição de dispositivo inicial mais timestamp) e a expressão das duas trilhas na mesma referência comum, registrando nos metadados a latência de cada fluxo; verificar por teste com latências distintas simuladas
- [ ] 2.3.2 Implementar o estabelecimento da âncora da trilha de loopback no primeiro pacote recebido quando ela fica ociosa no início, preenchendo com silêncio o intervalo desde o início da sessão; verificar por teste com loopback ocioso nos primeiros trinta segundos
- [ ] 2.3.3 Verificar, por teste com atraso de entrega simulado sem perda de captura, que **nenhum silêncio é inserido** — este teste é o que distingue a implementação correta da ingênua
- [ ] 2.3.4 Implementar o limiar de 200 ms abaixo do qual nenhum silêncio é inserido, e verificar por teste com jitter simulado dentro e fora do limiar
- [ ] 2.3.5 Implementar o tratamento de pacotes com posições sobrepostas escrevendo apenas o excedente, sem recuar a posição nem duplicar áudio; verificar por teste com pacotes sobrepostos
- [ ] 2.3.6 Implementar a derivação pela contagem contínua de quadros quando posição ou timestamp forem inválidos, com registro nos metadados e restabelecimento da âncora no próximo pacote válido; verificar por teste
- [ ] 2.3.7 Implementar o consumo da sinalização de descontinuidade do sistema operacional como indicação de lacuna real, e verificar por teste com descontinuidade provocada
- [ ] 2.4 Implementar a thread de trabalho que consome a fila, reamostra para 16 kHz mono 16 bits e escreve incrementalmente em disco; verificar por teste que o arquivo cresce durante a captura
- [ ] 2.5 Implementar o preenchimento de lacunas comparando a posição escrita com a posição indicada pelo carimbo, injetando silêncio pela diferença; verificar por teste com blocos sintéticos contendo lacunas de tamanhos variados que a posição final corresponde ao tempo decorrido
- [ ] 2.6 Construir o procedimento de verificação de alinhamento por evento sonoro conhecido presente nas duas trilhas, e verificar numa gravação real de 60 minutos com pelo menos dois minutos de silêncio total que o deslocamento medido do evento é de no máximo 250 ms
- [ ] 2.6.1 Verificar explicitamente que a comparação de durações dos arquivos **não** é usada como critério de aprovação do alinhamento, permanecendo apenas como checagem de sanidade
- [ ] 2.7 Implementar a medição contínua do desvio de alinhamento com registro nos metadados e aviso acima de 500 ms; verificar por teste que o aviso aparece nos metadados e é propagado ao usuário
- [ ] 2.7.1 Implementar a medição do atraso entre o comando de iniciar e a primeira amostra capturada, com teto de 1500 ms, definindo o início da sessão pela primeira amostra; verificar por teste que o instante de início corresponde ao áudio e não ao comando
- [ ] 2.8 Implementar a durabilidade forçada a cada 2 segundos de áudio por trilha, e verificar por teste matando o processo em operação normal que a perda não excede 2 segundos por trilha
- [ ] 2.8.0 Implementar os três limiares de acumulação pendente por trilha — aviso em 2 s, sessão degradada em 10 s, encerramento limpo em 60 s —, e verificar por testes com escritor artificialmente lento que cada limiar dispara o comportamento correto
- [ ] 2.8.0.1 Implementar o encerramento limpo imediato quando nenhuma escrita é concluída por mais de 10 segundos com acumulação crescente, sem aguardar o patamar de 60 segundos; verificar por teste com escritor paralisado
- [ ] 2.8.0.2 Implementar o registro nos metadados da acumulação pendente máxima observada na sessão, e verificar por teste que o valor reflete o pico real
- [ ] 2.8.1 Implementar a contabilização e o registro nos metadados de blocos não escritos, com instante e duração, garantindo que nenhum áudio é descartado silenciosamente; verificar por teste
- [ ] 2.8.2 Implementar o encerramento limpo da gravação quando o espaço livre cai abaixo de 500 MB e quando a escrita é recusada pelo sistema de arquivos, preservando o áudio já escrito e informando a causa; verificar por testes com disco cheio e com escrita recusada simulados
- [ ] 2.9 Implementar a falha explícita ao iniciar com dispositivo configurado ausente, nomeando o dispositivo e listando os disponíveis, sem cair para o padrão; verificar por teste com identificador inexistente
- [ ] 2.10 Implementar as duas políticas por trilha — seguir o padrão do papel e dispositivo fixado — como comportamentos explícitos e incompatíveis, e verificar por testes que a primeira migra na troca de padrão e a segunda nunca migra
- [ ] 2.10.1 Implementar a detecção, em no máximo 5 segundos, tanto da perda do dispositivo quanto da **mudança do dispositivo padrão do papel sem desconexão do anterior**; verificar conectando um fone durante uma gravação real e conferindo que a migração ocorre mesmo sem o fluxo apresentar erro
- [ ] 2.10.2 Implementar a migração com preenchimento do intervalo por silêncio e registro de instante e duração nos metadados, preservando o alinhamento; verificar medindo o alinhamento após uma troca real de dispositivo
- [ ] 2.11 Implementar, para trilha com dispositivo fixado, a tentativa de reabertura por até 30 segundos e o encerramento da trilha como incompleta ao esgotar o prazo, mantendo a outra trilha ativa; verificar por testes de reconexão dentro e fora do prazo
- [ ] 2.12 Implementar o monitoramento de nível por trilha com aviso de trilha silenciosa além do limiar, e verificar gravando com o microfone mudo
- [ ] 2.13 Implementar a detecção heurística de saída que não aparenta ser fone e o aviso de risco de eco no início da gravação, sem bloquear o início; verificar que o aviso consta nos metadados
- [ ] 2.13.1 Implementar o aviso, no início da gravação, identificando qual dispositivo de saída está sendo capturado e que **toda** a mistura reproduzida nele será gravada; verificar que o aviso é emitido e registrado nos metadados
- [ ] 2.13.2 Implementar o bloqueio da reprodução de áudio de reuniões enquanto houver gravação ativa, com a causa explicada, e verificar por teste que a reprodução é recusada nessa condição
- [ ] 2.14 Implementar a compressão sem perdas das trilhas ao encerrar e verificar que o resultado reabre com a mesma duração e conteúdo
- [ ] 2.15 Implementar a opção desativada por padrão de preservar as trilhas em formato nativo, e verificar por teste que ativá-la produz os arquivos adicionais sem alterar os de 16 kHz

## 3. Sessão de gravação

- [ ] 3.0 Implementar o serviço residente como único proprietário da captura e da fila, com instância única por diretório de dados, iniciado sob demanda pela primeira superfície que precisar dele; verificar por teste que uma segunda tentativa de iniciá-lo conecta-se à instância existente em vez de criar outra
- [ ] 3.0.1 Implementar o ponto de encontro de escopo de usuário que publica endereço e segredo da instância corrente, independente do diretório de trabalho, acessível igualmente por todas as superfícies; verificar por teste que a linha de comando e um segundo cliente localizam a mesma instância sem que nenhum deles a tenha iniciado
- [ ] 3.0.1.1 Implementar a comunicação da linha de comando com o serviço residente, de modo que iniciar e encerrar gravação sejam invocações distintas atendidas pela mesma instância; verificar executando `record start`, aguardando, e executando `record stop` como processos separados
- [ ] 3.0.1.2 Implementar a exclusividade de escopo de máquina para captura e transcrição, independente do diretório de dados, com erro que identifica o serviço detentor e o diretório dele; verificar por teste iniciando dois serviços apontados para diretórios diferentes e tentando gravar nos dois
- [ ] 3.0.2 Implementar a política de ociosidade do serviço — permanecer vivo enquanto houver gravação ativa, fila pendente ou ao menos um cliente conectado, encerrar-se após 300 segundos sem nada disso, removendo sua publicação do ponto de encontro antes de terminar; verificar por testes das três condições de permanência e da remoção da publicação
- [ ] 3.0.3 Implementar o encerramento do serviço abrangendo toda a árvore de processos criada por ele, sem presumir que terminar o processo pai encerra os descendentes no Windows; verificar encerrando o serviço durante uma transcrição e conferindo a lista de processos
- [ ] 3.0.4 Verificar por teste que nenhuma superfície além do serviço residente abre dispositivos de áudio ou executa transcrição por conta própria
- [ ] 3.1 Implementar a criação da sessão em disco e no banco antes da primeira amostra capturada, e verificar por teste que uma falha imediata de captura deixa a sessão registrada como falha
- [ ] 3.2 Implementar o layout do diretório da sessão nomeado por instante de início e identificador, independente do título, e verificar por teste que renomear não move arquivos
- [ ] 3.3 Implementar a gravação dos metadados da sessão com todos os campos exigidos pela especificação, e verificar por teste que nenhum campo obrigatório fica ausente ao fim de uma gravação
- [ ] 3.4 Implementar o título opcional no início com padrão derivado do instante, e a renomeação posterior preservando identificador e caminhos; verificar por testes dos dois caminhos
- [ ] 3.5 Implementar a exclusividade de sessão por marca de processo em disco, falhando ao iniciar uma segunda gravação e identificando a sessão em andamento; verificar por teste
- [ ] 3.6 Implementar pausa e retomada preenchendo o período pausado com silêncio nas duas trilhas e registrando os intervalos nos metadados; verificar por teste que o alinhamento é preservado após uma pausa
- [ ] 3.7 Implementar o encerramento a partir do estado pausado excluindo o período final pausado da duração, e verificar por teste
- [ ] 3.7.1 Implementar a finalização em quatro passos idempotentes e retomáveis — fechar arquivos, comprimir, atualizar metadados, enfileirar — com o progresso persistido em disco; verificar por testes que interromper após cada um dos passos é recuperável na inicialização seguinte
- [ ] 3.7.2 Garantir que o áudio original só é removido após o arquivo comprimido ser lido de volta e validado como legível e de duração equivalente; verificar por teste com compressão que produz arquivo inválido
- [ ] 3.7.3 Implementar a preservação do áudio original e a marcação da sessão quando a compressão falhar, prosseguindo com a transcrição; verificar por teste com compressão forçada a falhar
- [ ] 3.7.4 Implementar o **mínimo durável** para suspensão — interromper captura, forçar escrita do pendente, fechar arquivos e persistir metadados com o motivo —, sem compressão nem exportações, concluído em no máximo 1000 ms; verificar por medição do tempo gasto e suspendendo a máquina durante uma gravação real
- [ ] 3.7.5 Implementar a conclusão dos passos restantes da finalização na retomada, pelo mesmo processo quando ele sobrevive e pela recuperação de sessão interrompida quando não; verificar nos dois cenários
- [ ] 3.7.6 Implementar a recusa de remoção de áudio fora dos estados pronta, parcial e falha, com a validação residindo no serviço; verificar por testes que a linha de comando e um segundo cliente recebem a mesma recusa para uma sessão sendo transcrita
- [ ] 3.8 Implementar a detecção de sessões marcadas como ativas ou com finalização pendente sem processo correspondente na inicialização, e verificar por teste com marca de processo órfã em cada um dos estados
- [ ] 3.9 Implementar a retomada da finalização a partir do passo em que parou, o registro da duração real, a marcação como recuperada e o enfileiramento para transcrição; verificar matando o processo durante uma gravação real e reiniciando
- [ ] 3.10 Implementar a marcação como falha, em vez de recuperada, quando a sessão interrompida tem áudio de duração desprezível; verificar por teste
- [ ] 3.11 Implementar a remoção explícita do áudio de uma sessão preservando transcrição e metadados e marcando a sessão como não reprocessável; verificar por teste

## 4. Importação de mídia

- [ ] 4.1 Implementar a criação de sessão a partir de arquivo de mídia, extraindo o áudio para o formato do armazenamento sem copiar nem alterar o original; verificar por teste com um vídeo e conferindo que o original fica byte a byte idêntico
- [ ] 4.2 Implementar o registro nos metadados do caminho de origem, do instante de importação e da origem como importação, e verificar por teste
- [ ] 4.3 Implementar o título a partir do parâmetro informado, caindo para o nome do arquivo, e verificar por testes dos dois caminhos
- [ ] 4.4 Implementar a atribuição de falante desconhecida para segmentos de sessão importada, distinguível na leitura e na exportação; verificar por teste que nenhum segmento importado é atribuído ao usuário
- [ ] 4.5 Implementar o registro da existência de trilhas de áudio adicionais quando o arquivo tiver mais de uma, extraindo a primeira; verificar por teste com arquivo multi-trilha
- [ ] 4.6 Implementar a integridade da sessão importada após o original ser movido ou apagado, com indicação nos metadados de que o caminho não está mais acessível; verificar por teste
- [ ] 4.7 Implementar a importação em lote com isolamento de falha por arquivo e relatório final de importados e falhos, saindo com código diferente de zero quando houver falha; verificar por teste com pasta mista

## 5. Fila de transcrição

- [ ] 5.1 Implementar os **dois atributos independentes** por reunião — disponibilidade da transcrição (revisão ativa, completa ou parcial) e estado da tentativa corrente —, com as transições persistidas; verificar por teste que o reprocessamento de uma reunião já transcrita mantém a disponibilidade completa durante toda a tentativa e que o estado sobrevive ao reinício do processo
- [ ] 5.1.1 Verificar por teste que nenhuma superfície indica ausência de transcrição quando existe revisão ativa, inclusive durante reprocessamento e após reprocessamento falho
- [ ] 5.1.2 Implementar a recusa de enfileirar uma segunda tentativa para a mesma reunião, informando a tentativa em andamento e sem interromper nem duplicar a corrente; verificar por teste com dois pedidos sucessivos
- [ ] 5.2 Implementar o enfileiramento automático ao encerrar gravação, ao recuperar sessão interrompida e ao concluir importação; verificar por testes dos três caminhos
- [ ] 5.3 Implementar o executor de transcrição em processo separado, consumindo a fila por antiguidade; verificar transcrevendo duas sessões enfileiradas
- [ ] 5.4 Implementar o bloqueio da execução da fila enquanto houver gravação ativa, e verificar por teste que uma sessão enfileirada durante gravação só começa depois do encerramento
- [ ] 5.5 Implementar a interrupção limpa da transcrição em andamento quando uma gravação inicia, descartando a revisão em construção e devolvendo a sessão à fila; verificar iniciando uma gravação durante uma transcrição real e medindo que a primeira amostra é capturada em no máximo 2000 ms contados do comando, incluindo a liberação de memória de GPU
- [ ] 5.6 Implementar a devolução à fila, na inicialização, de sessões que ficaram marcadas como em processamento; verificar por teste com estado órfão
- [ ] 5.7 Implementar a transcrição independente por trilha com isolamento de falha, compondo uma revisão parcial cuja publicação segue a política de revisões; verificar por testes com uma trilha propositalmente corrompida, tanto numa reunião sem revisão anterior quanto numa com revisão completa existente
- [ ] 5.7.1 Verificar por teste que reprocessar uma reunião com revisão completa e obter falha em uma trilha **preserva** a revisão anterior e informa o usuário, em vez de substituí-la por resultado pior
- [ ] 5.8 Implementar o registro legível do motivo da falha no estado da reunião, e verificar por teste
- [ ] 5.9 Implementar o reprocessamento produzindo uma nova revisão que, ao ser publicada, substitui integralmente a anterior e regera as exportações; verificar por teste que nenhum segmento da revisão anterior permanece e que nenhuma revisão mistura resultados de motores diferentes
- [ ] 5.10 Implementar a recusa de reprocessar reunião sem áudio disponível, preservando a transcrição existente; verificar por teste
- [ ] 5.11 Implementar o vocabulário de domínio global com sobrescrita por reunião, repassado ao motor e registrado nos metadados; verificar por testes dos dois níveis

## 6. Linha de tempo, busca e exportação

- [ ] 6.1 Implementar a fusão das trilhas numa linha de tempo ordenada por instante de início com atribuição de falante por trilha, e verificar por teste com segmentos sintéticos das duas trilhas
- [ ] 6.2 Implementar a detecção e sinalização de sobreposição entre segmentos de trilhas diferentes, preservando ambos integralmente; verificar por teste com segmentos sobrepostos
- [ ] 6.3 Implementar a busca textual devolvendo reunião, instante, atribuição de falante e recorte com contexto, insensível a caixa e acento; verificar por teste com termos acentuados e não acentuados
- [ ] 6.4 Implementar a restrição da busca por intervalo de datas e por reunião, e verificar por testes dos dois filtros
- [ ] 6.5 Implementar a exportação legível com título, data, duração e linha de tempo com instante, falante e texto; verificar abrindo a exportação de uma reunião real
- [ ] 6.6 Implementar a exportação estruturada contendo todos os campos suficientes para reconstruir a linha de tempo, e verificar por teste de ida e volta que a reconstrução é idêntica
- [ ] 6.7 Implementar a gravação das exportações no diretório da reunião e sua regeração após reprocessamento; verificar por teste

## 7. Linha de comando

- [ ] 7.1 Implementar `voxvault devices` listando dispositivos com identificadores e padrões do sistema, e verificar executando na máquina
- [ ] 7.2 Implementar `voxvault record start` com título e dispositivos opcionais, `voxvault record pause`, `voxvault record resume` e `voxvault record stop`; verificar executando um ciclo completo
- [ ] 7.3 Implementar `voxvault import` para arquivo único e para lote, e verificar executando com um arquivo e com uma pasta
- [ ] 7.4 Implementar `voxvault list` exibindo reuniões com título, data, duração e estado, e verificar executando após algumas gravações
- [ ] 7.5 Implementar `voxvault show` exibindo a linha de tempo de uma reunião, e verificar executando sobre uma reunião transcrita
- [ ] 7.6 Implementar `voxvault search` com filtros de período e reunião, e verificar executando sobre um histórico com várias reuniões
- [ ] 7.7 Implementar `voxvault rename`, `voxvault retranscribe` e `voxvault forget-audio`, e verificar executando cada um
- [ ] 7.8 Implementar a exibição do estado da fila e do progresso de transcrição, e verificar executando durante uma transcrição em andamento

## 8. Verificação de ponta a ponta

- [ ] 8.1 Gravar uma reunião real de pelo menos trinta minutos numa plataforma de videoconferência e verificar que a transcrição final identifica corretamente as falas do usuário e as dos demais participantes
- [ ] 8.2 Repetir a verificação numa segunda plataforma diferente e confirmar que nenhuma configuração específica de plataforma foi necessária
- [ ] 8.3 Verificar, numa gravação com silêncios prolongados, que uma fala ocorrida após um longo silêncio aparece no mesmo instante nas duas trilhas da linha de tempo fundida
- [ ] 8.4 Matar o processo no meio de uma gravação real, reiniciar, e verificar que a sessão é recuperada, transcrita e legível
- [ ] 8.5 Iniciar uma gravação enquanto uma transcrição longa está em andamento e verificar que a gravação inicia sem atraso perceptível e que a transcrição é retomada depois
- [ ] 8.6 Medir o uso de CPU e de memória do serviço residente e de toda a sua árvore de processos durante uma reunião de uma hora, com amostragem a cada 5 segundos, e registrar o resultado como linha de base para os limites verificados na fase da interface
- [ ] 8.7 Gravar uma reunião com um vídeo tocando em paralelo no mesmo dispositivo de saída e verificar que o áudio do vídeo aparece na trilha do sistema, confirmando que o comportamento documentado corresponde ao real
- [ ] 8.8 Verificar, num ciclo completo conduzido apenas por invocações separadas de linha de comando, que o serviço residente mantém a captura entre `record start` e `record stop` e que nenhum processo permanece após a política de ociosidade
