## Context

Ver `proposal.md — Why`. O núcleo já está pronto e exercitado por linha de comando; esta fase acrescenta uma superfície gráfica sobre ele, sem reimplementar comportamento.

Duas restrições vêm de fora e definem quase tudo: o núcleo é Python com dependências de GPU pesadas, e o produto existe porque uma ferramenta equivalente pesa demais na máquina durante a reunião. A segunda é a mais importante — a interface é o lugar onde esse defeito costuma reaparecer.

## Goals / Non-Goals

**Goals:**

- Tornar imediatas as duas operações que o terminal atrapalha: gravar ao começar a reunião, e reler um trecho ouvindo o áudio correspondente.
- Manter o custo da aplicação desprezível durante a gravação, como requisito medido e não como intenção.
- Não duplicar regra de negócio: tudo que a interface faz é operação já especificada no núcleo.

**Non-Goals:**

- Não gerar instalador, não assinar código, não atualizar automaticamente. A aplicação roda do build local nesta máquina.
- Não implementar transcrição ao vivo.
- Não reimplementar busca, fusão de linha de tempo ou qualquer lógica do núcleo no frontend.
- Não suportar múltiplos perfis ou múltiplos usuários.

## Decisions

### Tauri com webview do sistema, não Electron

O produto nasce da reclamação de que uma ferramenta equivalente é pesada demais. Embarcar um navegador inteiro para desenhar uma lista e um reprodutor contradiz a premissa. Tauri usa o webview já presente no sistema, e o processo hospedeiro é nativo e pequeno.

*Alternativa considerada:* Electron, com ecossistema maior e mais exemplos. Rejeitada pelo custo de memória em repouso, que é justamente o que se pretende evitar durante a reunião.

### Svelte sem framework de componentes pesado

Escolha do usuário. Compila para JavaScript direto, sem DOM virtual, e a superfície de interface aqui é pequena — lista, leitor, reprodutor e configurações. O peso do framework apareceria em repouso, que é o estado em que a aplicação passa a maior parte da reunião.

### O ambiente Python é preparado na primeira abertura, não empacotado

Empacotar interpretador, runtime de inferência e bibliotecas de GPU gera um instalável de vários gigabytes, quebra com frequência e dispara falso positivo de antivírus. Como o gerenciador de ambientes já está presente na máquina e sabe baixar o próprio interpretador, o aplicativo o invoca para preparar o ambiente na primeira abertura.

*Consequência aceita:* a primeira abertura demora e exige rede. É apresentada como etapa de preparo com progresso visível, e só acontece uma vez.

*Alternativa considerada:* empacotamento com ferramenta de congelamento de aplicações Python. Rejeitada pelos motivos acima. Esta decisão é viável porque a distribuição se limita a esta máquina; se isso mudar, ela precisa ser revisitada.

### Serviço local em processo separado, com segredo por sessão

O núcleo já roda como processo próprio e expõe suas operações à interface por um serviço local restrito à máquina. O segredo gerado a cada inicialização impede que outro processo qualquer da máquina opere a gravação, o que importa porque a aplicação controla microfone e áudio do sistema.

*Alternativa considerada:* comunicação por entrada e saída padrão com o processo filho. Rejeitada porque a interface precisa de atualização contínua de níveis de áudio e progresso, e um canal bidirecional persistente resolve isso de forma mais direta.

### Níveis de áudio derivam da captura existente, sem processamento adicional

O medidor de nível é calculado no mesmo ponto em que o áudio já passa para ser gravado, e enviado à interface com taxa limitada. Calcular nível separadamente significaria processar o áudio duas vezes durante a reunião, exatamente onde o orçamento é mais apertado.

### A reprodução usa o áudio de 16 kHz já gravado

A trilha usada pela transcrição é a mesma usada pela reprodução. É suficiente para conferir um trecho em dúvida, que é o propósito, e evita manter uma segunda cópia do áudio só para escuta.

### Nota nunca se mistura à linha de tempo, também na interface

A mesma separação exigida na exportação vale aqui. Um resumo gerado por modelo pode estar errado, e a distinção entre "isto foi dito" e "isto foi interpretado" precisa resistir à leitura apressada meses depois.

### Ações indisponíveis aparecem desabilitadas com a causa, não falham no clique

Vale para todo o aplicativo: reprocessar sem áudio, transcrever sem runtime de inferência, trocar dispositivo durante gravação. Descobrir a impossibilidade só ao acionar é o padrão que torna uma ferramenta irritante.

### A aplicação é cliente do serviço residente, não dona dele

O núcleo já roda como serviço residente desde a fase 1, porque os comandos de linha de comando precisavam de alguém que segurasse a captura entre `start` e `stop`. A aplicação se conecta a esse mesmo serviço em vez de subir um próprio — caso contrário existiriam dois donos dos dispositivos de áudio e da fila.

A consequência prática: fechar a aplicação não pode matar o serviço quando há gravação ativa ou fila pendente. Uma reunião fechada às pressas precisa terminar de ser transcrita mesmo com a janela fechada, e reabrir a aplicação precisa reencontrá-la pronta. Sem trabalho pendente, o serviço se encerra sozinho por ociosidade, e a aplicação não precisa matá-lo.

Isso também significa que uma gravação iniciada pela linha de comando aparece como ativa ao abrir a aplicação, o que é o comportamento correto: é a mesma gravação, do mesmo dono.

### O orçamento de recursos vale para a árvore inteira de processos

O componente de navegação embutido executa em processos separados do hospedeiro, e é justamente ali que o custo de uma interface mal construída aparece. Medir apenas o processo principal produziria um número bonito e falso.

Por isso o conjunto medido é declarado: hospedeiro, todos os processos do componente de navegação e descendentes, serviço residente e descendentes. E os limites são números fixos — 8% de processador em média, 15% em qualquer janela de um minuto, 700 MB com janela aberta, 400 MB minimizada —, não "limites configurados", que qualquer implementação passaria elevando o próprio gabarito.

### Reprodução bloqueada durante gravação

A trilha do sistema captura a mistura do dispositivo de saída. Reproduzir uma reunião antiga durante uma gravação a incorporaria à gravação em curso como se fosse fala de participante. A reprodução fica indisponível enquanto grava, com a causa explicada — restrição herdada da captura, não escolha de interface.

## Risks / Trade-offs

**A interface reintroduzir o peso que motivou o projeto** → É o risco central desta fase. Mitigação: limite de consumo durante gravação é requisito verificável, com medição explícita nas tarefas; nenhum processamento pesado é permitido com gravação ativa; e a atualização de níveis tem taxa limitada.

**Preparo do ambiente na primeira abertura falhar de forma opaca** → É o pior primeiro contato possível. Mitigação: progresso visível, causa em linguagem natural, ação corretiva, e recusa de abrir a interface principal num estado que apenas aparenta funcionar.

**Divergência entre o que o núcleo faz e o que a interface mostra** → Duas superfícies sobre o mesmo núcleo tendem a divergir. Mitigação: a interface não reimplementa nenhuma regra, e o estado apresentado vem sempre do núcleo, nunca de cópia local.

**Fechar o aplicativo perder uma reunião em andamento** → Mitigação: confirmação explícita informando a duração corrente, encerramento limpo antes de terminar, e tratamento do pedido de desligamento do sistema operacional.

**Webview do sistema com comportamento diferente do navegador de desenvolvimento** → Mitigação: a verificação de ponta a ponta acontece na aplicação empacotada, não apenas no servidor de desenvolvimento.

**Ausência de instalador limita o uso a esta máquina** → Trade-off assumido por decisão de produto. A consequência é que caminhos e pressupostos de ambiente podem ficar específicos demais; se a distribuição mudar, esta decisão e a do preparo de ambiente precisam ser revisitadas juntas.
