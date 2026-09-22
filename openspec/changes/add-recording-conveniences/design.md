## Context

Ver `proposal.md — Why`. As fases anteriores entregaram gravação, transcrição, acesso por agente e interface gráfica. O que resta é o intervalo entre a reunião começar e o usuário lembrar de gravar.

O aplicativo hospedeiro já existe e é o lugar natural para bandeja, atalho global e notificações, porque é o processo nativo com acesso às interfaces do sistema operacional.

## Goals / Non-Goals

**Goals:**

- Reduzir o custo de iniciar uma gravação a um atalho, alcançável com a plataforma de reunião em primeiro plano.
- Fazer a ferramenta perceber o começo da reunião sem que isso vire captura involuntária de conversas.
- Informar o usuário sobre o que a ferramenta está fazendo quando ele não está olhando para ela.

**Non-Goals:**

- Não vincular gravações a eventos da agenda. Foi considerado e deixado de fora porque o usuário não o selecionou entre as saídas do produto. Continua viável como mudança futura, apoiado nos metadados de sessão que já existem.
- Não exportar para ferramentas externas de notas. Mesma justificativa.
- Não identificar participantes nem ler convites de reunião.
- Não inspecionar conteúdo de áudio, tela ou comunicação para detectar reunião.
- Não transcrever ao vivo, o que permanece fora do produto.

## Decisions

### A detecção observa sessões de áudio do sistema, não apenas processos em execução

Verificar se um aplicativo de reunião está aberto produz falso positivo constante: o cliente de reunião fica residente o dia inteiro sem haver reunião alguma. O sinal que realmente distingue é outro processo manter uma captura de microfone ativa.

Combinar os dois sinais — captura ativa somada a um aplicativo reconhecido como plataforma de reunião — reduz tanto o falso positivo quanto o falso negativo, sem inspecionar nada do conteúdo.

*Alternativa considerada:* consultar a agenda do usuário para saber quando há reunião marcada. Rejeitada neste momento porque integração com agenda está fora do escopo por decisão de produto, e porque reunião não marcada é justamente o caso em que o usuário mais esquece de gravar.

### Sustentação mínima antes de considerar a detecção válida

Abrir o microfone por um instante é comum — teste de som, mensagem de voz, atalho acionado por engano. Exigir que o sinal se mantenha por um período configurável elimina esses casos sem perder reunião real, que dura minutos.

### O padrão é sugerir, nunca iniciar

Gravação iniciada sem intenção explícita pode registrar uma conversa que o usuário não pretendia guardar, e isso envolve terceiros. O custo de errar para o lado permissivo é muito maior do que o de errar para o lado conservador, então o comportamento permissivo existe, é configurável, e não é o padrão.

Quando o usuário opta pelo início automático, o descarte imediato pela notificação apaga sessão e áudio sem passar pela transcrição, para que o erro seja reversível em um acionamento.

### O atalho global vive no processo hospedeiro e sempre confirma

O atalho é acionado justamente quando o usuário não está vendo a aplicação, então um acionamento sem retorno perceptível gera a dúvida de "gravou ou não?" que destrói a confiança na ferramenta. Toda ativação produz confirmação visível.

A falha de registro do atalho por conflito com outro aplicativo é reportada, nunca silenciosa: um atalho que não funciona sem avisar é pior que atalho nenhum.

### Fechar a janela recolhe para a bandeja, por configuração

Durante uma reunião o usuário fecha janelas para liberar espaço, e fechar a janela do VoxVault nesse momento não pode custar a gravação. Com o recolhimento configurado, fechar não interrompe nem interroga; o encerramento real permanece como ação distinta e explícita.

### Notificações por categoria, sem repetição

O evento mais valioso é a transcrição concluída, porque acontece quando o usuário já mudou de contexto. Os demais são úteis mas incomodam se excessivos, então cada categoria é desligável e nenhum evento notifica duas vezes.

## Risks / Trade-offs

**Gravação involuntária de conversa de terceiros** → É o risco de maior consequência desta fase. Mitigação: padrão conservador, sustentação mínima, descarte em um acionamento, lista de aplicativos inspecionável e excluível, e desligamento completo da detecção.

**Falso positivo em uso legítimo do microfone** → Ditado de texto, gravação de nota de voz ou chamada pessoal podem parecer reunião. Mitigação: combinação de dois sinais, período mínimo de sustentação, e exclusão por aplicativo.

**Falso negativo em plataforma não reconhecida** → Uma plataforma fora da lista não dispara detecção. Mitigação: o sinal de captura de microfone ativa vale para qualquer aplicativo, e a lista é apresentada ao usuário em vez de ficar implícita no código.

**Atalho global capturado por outro aplicativo** → Conflito é comum em máquina de trabalho. Mitigação: atalho configurável e falha de registro reportada explicitamente na configuração.

**Início automático interagindo com a exclusividade de sessão** → A detecção pode disparar durante uma gravação manual em andamento. Mitigação: requisito explícito de não interferência, verificado por teste.

**Detecção rodando continuamente consumir recursos** → Contradiz a premissa do produto se mal implementada. Mitigação: a observação é por consulta periódica de baixa frequência às interfaces do sistema, sem processamento de áudio, e o consumo entra na mesma medição de custo em repouso já exigida pela fase anterior.

## Open Questions

- Qual combinação de teclas usar como atalho padrão. Depende de conflitos observados na máquina do usuário e não altera specs, abordagem nem tarefas — basta ser configurável, o que já é requisito.
- Quais aplicativos entram na lista inicial de plataformas reconhecidas. A lista é apresentada e editável pelo usuário, então acertá-la na primeira tentativa não é condição para nada.
