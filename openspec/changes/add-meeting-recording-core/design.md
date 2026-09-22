## Context

Ver `proposal.md — Why` para a motivação. A fase anterior deixou prontos o contrato `TranscriptionEngine`, a normalização de áudio via ffmpeg e o diagnóstico de ambiente; esta fase os consome sem alterá-los.

Três restrições herdadas moldam o que segue: a captura de áudio do sistema depende de uma interface exclusiva do Windows, o interpretador tem piso em Python 3.12 por causa do `numpy`, sem teto, o que o portão de captura confirmou ao escolher um backend sem extensão compilada, e os dados vivem em `D:\VoxVault\` porque o drive do código não tem espaço.

## Goals / Non-Goals

**Goals:**

- Núcleo completo e utilizável por linha de comando antes de existir qualquer interface gráfica.
- Alinhamento temporal correto entre as trilhas sob silêncio prolongado, que é a condição normal de uma reunião, não a excepcional.
- Sobrevivência a queda: nenhuma reunião perdida por processo morto, energia cortada ou dispositivo trocado.
- Armazenamento pronto para ser lido por outros processos, porque o servidor MCP e a interface gráfica vão fazer exatamente isso.

**Non-Goals:**

- Não transcrever ao vivo. Nada nesta fase processa áudio durante a reunião.
- Não separar participantes individualmente. A atribuição é por trilha, e isso é decisão de produto, não limitação temporária.
- Não cancelar eco. O risco é avisado, não tratado.
- Não suportar outros sistemas operacionais.
- Não oferecer interface gráfica; a linha de comando é a superfície desta fase.

## Decisions

### O backend de captura é escolhido pelos dados que expõe, não pela conveniência da API

A captura usa a interface de loopback nativa do Windows, que permite ler o áudio reproduzido pelo sistema sem instalar driver nem cabo de áudio virtual. O que define a escolha do backend, porém, não é o acesso ao loopback — vários o oferecem — e sim três dados que o alinhamento temporal exige:

1. a **posição de dispositivo** e o **timestamp de relógio de alta resolução** associados a cada pacote capturado;
2. a **sinalização de descontinuidade** que o Windows emite quando pacotes foram perdidos;
3. a **identidade persistente** do endpoint e a notificação de mudança de dispositivo padrão por papel.

Sem o primeiro item não há como distinguir atraso de entrega de lacuna real de captura, e o mecanismo de preenchimento de silêncio passa a fabricar erro em vez de corrigi-lo — que é exatamente o defeito que o alinhamento existe para evitar.

**`PyAudioWPatch` não atende ao contrato.** Foi verificado no fonte do backend WASAPI do PortAudio que ele o acompanha:

- todas as chamadas a `IAudioCaptureClient::GetBuffer` passam `NULL` para os parâmetros de posição de dispositivo e de timestamp, descartando os dois;
- `inputBufferAdcTime` é calculado como o relógio corrente no instante do callback somado a uma estimativa de latência, e portanto **carrega o atraso de escalonamento** em vez de eliminá-lo;
- a flag de descontinuidade de dados não é inspecionada em ponto algum.

A consequência é direta: ler `inputBufferAdcTime` satisfaz a letra do requisito e não o seu propósito. Construir o alinhamento sobre esse valor reproduziria o defeito original com aparência de correção.

**Decisão:** o backend de captura precisa acessar a interface WASAPI diretamente — por uma ligação nativa própria a partir do Python ou por um componente nativo auxiliar — de modo a obter posição de dispositivo, timestamp de alta resolução e sinalização de descontinuidade por pacote. A escolha concreta do mecanismo é uma tarefa com portão de validação explícito, anterior à construção do alinhamento: nenhum backend é adotado sem que se demonstre, por medição, que ele entrega os três dados.

*Alternativas consideradas:* cabo de áudio virtual, rejeitado por exigir instalação de driver e reconfiguração manual da saída do usuário; `sounddevice`, que não expõe loopback no Windows; `PyAudioWPatch`, rejeitado pelo motivo acima, embora continue sendo a opção mais simples para prototipar a abertura de dispositivos; captura em Rust no processo do hospedeiro, que atende ao contrato e passa a ser uma alternativa forte justamente porque o acesso direto ao WASAPI virou requisito, e não mais um ganho de desempenho opcional.

*Consequência aceita:* esta fase custa mais do que custaria com uma biblioteca pronta. O custo é deliberado — o alinhamento entre as trilhas é a propriedade da qual todo o produto depende, e ele não pode ser construído sobre um timestamp que não significa o que aparenta.

### O instante de aquisição é a fonte de verdade da posição — não a contagem de amostras, nem o instante de entrega

Esta é a decisão central da fase, e ela tem duas metades que é fácil confundir.

A primeira: o dispositivo de loopback para de entregar blocos quando a saída de áudio fica ociosa, o que acontece o tempo todo numa reunião real. Concatenar blocos na ordem de chegada faz a trilha do sistema encolher em relação ao tempo de parede, e as duas transcrições deixam de corresponder. Por isso a posição vem de um carimbo de tempo, e não da contagem acumulada de amostras.

A segunda, e é onde a implementação ingênua erra: o carimbo tem de ser o instante em que o **dispositivo adquiriu** a primeira amostra do bloco, não o instante em que o bloco chegou ao aplicativo. A interface de captura expõe os dois tempos separadamente justamente porque eles divergem. Usar o de entrega faz com que qualquer atraso de escalonamento sob carga seja lido como perda de áudio, e o mecanismo criado para corrigir deriva passa a fabricar silêncio onde não houve lacuna alguma.

Quando o instante de aquisição não estiver disponível para um bloco, a posição é derivada da contagem contínua de amostras desde a última referência válida — degradação local e registrada, não substituição sistemática.

*Alternativa considerada:* manter um fluxo de áudio silencioso tocando durante a gravação para impedir que o dispositivo de saída fique ocioso. Rejeitada por ser um efeito colateral sobre o áudio do usuário para resolver um problema que o carimbo de tempo resolve diretamente.

### Alinhamento se verifica por evento, não por duração de arquivo

Dois arquivos de mesma duração podem estar inteiramente deslocados entre si. O critério de aceite é o deslocamento medido de um evento sonoro presente nas duas trilhas, com teto de 250 ms em 60 minutos. Comparar durações continua útil como sanidade, mas não é evidência de alinhamento.

### A trilha do sistema é a mistura do endpoint, e a especificação diz isso

O loopback captura tudo que o dispositivo de saída reproduz, não a reunião. Um vídeo em outra aba entra na gravação e será transcrito como fala dos demais participantes. Isso não tem correção possível no nível de captura, então a decisão é declarar o comportamento em vez de prometer o contrário.

Duas consequências práticas foram transformadas em requisito: o usuário é informado de qual dispositivo está sendo capturado ao iniciar, e a reprodução de reuniões antigas fica bloqueada durante uma gravação — sem isso, o próprio VoxVault se realimentaria.

### Papel de comunicações como padrão, e políticas separadas para seguir e fixar

O Windows mantém padrões distintos para multimídia e para comunicações, e plataformas de videoconferência usam o de comunicações. Dizer apenas "o padrão do sistema" deixaria a escolha para quem implementa, com boa chance de errar.

"Seguir o padrão" e "dispositivo fixado" são políticas explícitas e incompatíveis: a primeira migra quando o padrão muda, a segunda nunca migra. Sem essa separação, conectar um fone produziria comportamento indefinido — e esse é o caso mais comum de todos.

Detectar a mudança exige observar o padrão do papel, e não apenas esperar erro do fluxo: conectar um fone muda o padrão sem que o dispositivo anterior desapareça, então o fluxo continua funcionando e gravando o dispositivo errado.

### Um serviço residente é dono da captura e da fila

`record start` e `record stop` são invocações separadas; sem um proprietário residente não há nada que segure a captura entre elas. O serviço é único por diretório de dados, sobe sob demanda, e sobrevive ao fechamento da aplicação enquanto houver gravação ativa ou fila pendente.

Isso também define quem encerra o quê: a aplicação é cliente, não dona. E como terminar um processo no Windows não encerra seus descendentes, o encerramento do serviço abrange explicitamente toda a árvore.

### Transcrição publica revisões, e revisão parcial não derruba resultado completo

Atomicidade por trilha e "substituir a reunião inteira" se contradizem: se o reprocessamento tem sucesso numa trilha e falha na outra, não fica definido o que acontece com os segmentos antigos da trilha que falhou.

A revisão resolve isso. Todo segmento pertence a uma revisão; a linha de tempo nunca mistura revisões, então nunca se lê um texto costurado por dois motores diferentes sem perceber. E uma revisão parcial só se torna ativa quando não há nada melhor — reprocessar não pode destruir a única transcrição utilizável de uma reunião.

### Contenção de escrita é tratada, e nunca no caminho da captura

O modo de escrita adiantada permite muitos leitores, mas continua admitindo um escritor por vez. Com o servidor MCP gravando notas, passam a existir vários escritores disputando. Transações curtas, espera limitada e falha explícita ao esgotar o prazo substituem a promessa vaga de "não bloquear ninguém".

A regra que mais importa é a última: a persistência é desacoplada da captura, de modo que nenhuma disputa pelo banco possa custar áudio de reunião.

### Falha de escrita encerra a gravação de forma limpa, em vez de degradar

Disco cheio, escrita recusada ou escritor que não acompanha a taxa de captura são situações em que continuar produz uma gravação silenciosamente furada. A decisão é encerrar de forma limpa preservando o que existe, com limiares declarados: aviso em 2 s de acumulação, degradada em 10 s, encerramento em 60 s; e encerramento preventivo quando restam menos de 500 MB em disco, antes que a escrita comece a falhar.

### Finalização é retomável, e o original só morre depois do comprimido validado

Fechar arquivos, comprimir, atualizar metadados e enfileirar são quatro passos, e morrer entre dois deles não pode custar a reunião. Cada passo é idempotente, o progresso é persistido, e o áudio original só é removido depois que o comprimido foi lido de volta e conferido.

### O callback de áudio nunca bloqueia

O callback recebe o bloco, carimba e o deposita numa fila. Uma thread de trabalho separada faz reamostragem, preenchimento de lacunas e escrita em disco. Bloquear o callback com trabalho de CPU ou de disco provoca perda de blocos, que é exatamente o tipo de defeito difícil de diagnosticar depois.

### Reamostragem para 16 kHz na entrada, não na transcrição

O motor consome 16 kHz mono. Gravar já nesse formato reduz o áudio de cerca de 1,4 GB por hora e por trilha para algo em torno de 115 MB, sem perda alguma para a transcrição, e elimina um passo de conversão antes de cada transcrição. A opção de preservar o áudio nativo existe para quem quiser arquivo, desligada por padrão.

Compressão sem perdas ao encerrar a sessão leva as duas trilhas para perto de 120 MB por hora somadas.

### Estado da fila no banco, não em memória

A fila precisa sobreviver ao processo, e o banco já é o ponto de consistência do sistema. Um estado por reunião com transição persistida entrega retomada, observabilidade e recuperação de queda sem infraestrutura adicional.

### A transcrição roda em processo separado

Ela precisa ser interrompida de forma limpa quando uma gravação começa. Um processo próprio permite encerrá-la sem risco de deixar o processo principal num estado inconsistente, e isola falhas do runtime de GPU do processo que está gravando.

*Consequência:* a interrupção descarta o trabalho parcial daquela trilha e o refaz do início. Aceitável — transcrição é barata em relação a perder uma reunião.

### Busca textual com índice próprio, insensível a acento e caixa

O índice de texto completo do próprio banco resolve a busca sem dependência externa, com normalização que remove diacríticos. Isso importa em português, onde a mesma palavra aparece escrita das duas formas na fala transcrita.

### Sessão exclusiva por marca de processo em disco

Duas gravações simultâneas disputariam os mesmos dispositivos e produziriam duas linhas de tempo sobrepostas. A marca em disco também é o que permite, na inicialização, distinguir uma sessão realmente ativa de uma que ficou marcada como ativa porque o processo morreu.

### Sobreposição de fala preserva os dois segmentos

Quando as duas trilhas produzem segmentos que se sobrepõem no tempo, ambos são mantidos e a sobreposição é sinalizada. Escolher um e descartar o outro perderia justamente o momento mais informativo de uma reunião, que é a interrupção.

## Risks / Trade-offs

**Reamostragem e escrita não acompanharem a taxa de chegada dos blocos** → Provoca perda de áudio silenciosa, o pior defeito possível aqui. Mitigação: fila entre o callback e a thread de escrita, com monitoramento de profundidade da fila e aviso registrado nos metadados quando ela cresce além de um limiar.

**Eco duplicando falas quando o usuário está sem fone** → Trade-off assumido. O aviso é emitido no início da gravação e registrado nos metadados, para que a duplicação na transcrição seja explicável depois. Cancelamento de eco fica fora do escopo.

**Troca de dispositivo produzir um buraco maior que o esperado** → A reabertura leva tempo e o intervalo é preenchido com silêncio. Mitigação: registrar instante e duração de cada troca nos metadados, para que um trecho perdido seja visível em vez de misterioso.

**Áudio acumulando indefinidamente em disco** → Decisão de produto é preservar sempre. Cerca de 120 MB por hora significa que os 100 GB livres comportam algo próximo de 800 horas. Mitigação: o diagnóstico de ambiente avisa quando o espaço livre cai abaixo do limiar, e a remoção de áudio por sessão existe como ação explícita do usuário.

**Interrupção da transcrição pela chegada de uma gravação pode retardar indefinidamente uma reunião longa na fila** → Em uso normal não acontece, porque gravações são intercaladas com períodos livres. Mitigação: o estado de cada reunião é observável, e a ordem da fila é por antiguidade.

**Gravação de terceiros em disco sem criptografia** → Todo o material fica local e nada sai da máquina, mas o disco não é cifrado pela ferramenta. Fora do escopo desta fase; se virar requisito, o ponto natural é o diretório de dados inteiro.
