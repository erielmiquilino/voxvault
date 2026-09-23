# Estado da implementação

O que existe, como foi verificado, e o que continua em aberto. Escrito para ser
lido antes de confiar em qualquer parte disto.

A distinção que organiza o documento: **implementado** é código que existe;
**verificado** é código que foi executado e observado. Boa parte do sistema está
nas duas colunas. Onde não está, o motivo está dito.

## Resumo

| | |
|---|---|
| Testes | 488 em Python e 7 em Rust, todos passando, inclusive os de hardware |
| Lint | `ruff` limpo em `src` e `tests` |
| Código | 21.281 linhas de fonte entre núcleo e aplicativo, 8.331 de teste |
| Subida da linha de comando | 213 ms |
| Subida do servidor MCP | 1,0 s, sem carregar numpy, soxr, soundfile nem runtime de inferência |

## Fase 0 — motor e bancada

**Completa.** O portão de decisão foi atravessado: a transcrição local atende.

Medido com `large-v3` em `float16` na RTX 4060 Ti: **22x tempo real** na
decodificação, 4221 MB de pico, e as cinco frases da referência transcritas
palavra por palavra, com acentuação, pontuação e jargão técnico preservados.
Nenhuma alucinação nos silêncios de seis segundos — que era o risco principal.

Robustez de canal verificada em três degradações (ruído rosa, banda telefônica,
e as duas juntas): 5/5 corretas em todas.

**O que esta evidência não cobre:** o áudio de referência é sintetizado, e fala
sintetizada é mais limpa que fala humana. As degradações testaram robustez de
*canal*, não de *falante*. O veredito definitivo vem da primeira reunião real.
Detalhes em [avaliacao-fase-0.md](avaliacao-fase-0.md).

**Em aberto:** o veredito de qualidade e a escolha entre local e provedor
online, que são do usuário. O áudio de reunião real que faltava agora existe —
ver [A primeira reunião real](#a-primeira-reunião-real).

## Fase 1 — núcleo de gravação

**Implementada por inteiro.** Verificação parcialmente bloqueada pelo ambiente.

### Verificado executando

- Gravação real de duas trilhas: **35,2 s em ambas, divergência de 0 ms**,
  comprimidas sem perdas, transcritas na GPU, com a fala atribuída corretamente
  a `outros` por ter vindo da trilha do sistema.
- Portão de captura aprovado. A medição que o decide, sobre os mesmos pacotes na
  mesma execução: erro de passo de **21,3 µs** pelo WASAPI direto contra
  **115.566 µs** pelo método do PortAudio, sob carga. Fator de 50.362x.
  Detalhes em [portao-captura.md](portao-captura.md).
- Alinhamento com pacotes sintéticos: **0,5 ms de desvio em 30 segundos** de
  loopback ocioso. O requisito permite 250 ms em uma hora.
- Mínimo durável de suspensão: **9 ms**, contra o teto de 1000 ms.
- Ciclo completo pela API do serviço: início em 252 ms, segunda gravação
  simultânea recusada, pausa, retomada, encerramento.

### Implementado e coberto por teste, sem verificação em hardware

Durante parte do trabalho a máquina recusou abrir qualquer dispositivo de áudio
(`REGDB_E_CLASSNOTREG`) — estado do serviço de áudio do Windows, não do
VoxVault. Passou: uma reunião real de 39 minutos foi gravada depois disso.

Continuam sem verificação ao vivo, porque exigem alguém agindo na máquina:

- migração de trilha ao trocar o dispositivo padrão no meio da gravação
  (os testes com dispositivos falsos cobrem a lógica inteira);
- reação à perda de dispositivo — desligar e religar o Bluetooth com a versão
  atual gravando — e o encerramento de trilha como incompleta;
- suspensão real do sistema operacional com gravação ativa.

### Dois defeitos encontrados medindo, não lendo

**A posição de dispositivo nem sempre vem nos quadros entregues.** Em modo
compartilhado o Windows conta a posição nos quadros *do dispositivo* e entrega
o buffer no formato de mixagem. O microfone da webcam roda a 16 kHz atrás de uma
mixagem de 48 kHz: a posição avança 160 por pacote e o pacote traz 480 quadros.
Tratá-los como iguais fazia o alinhador ler tudo como sobreposição e descartar
**158.364 quadros de 238.560** — em silêncio. Uma gravação de 30 segundos virava
10. Agora a escala é medida e encaixada na razão exata entre duas taxas reais.

**A cauda do resampler era perdida.** O `soxr` emite em rajadas e retém a saída
final do filtro até saber que o fluxo acabou. Sem esvaziar, toda gravação perdia
~28 ms no fim.

## Fase 2 — servidor MCP e notas

**Completa e verificada.** Dez ferramentas sobre stdio, sem porta de rede.

- Ciclo de notas verificado de ponta a ponta: criar, listar, ler, atualizar,
  remover, com autoria registrada por cliente.
- Busca com escopo verificada nos três modos, insensível a acentuação nos dois
  sentidos.
- Paginação por cursor com ordenação total por ferramenta; um cursor de uma
  ferramenta é recusado por outra, e cursores de linha de tempo caem quando a
  revisão ativa muda.
- Um teste percorre a lista de ferramentas expostas e falha se aparecer qualquer
  coisa que apague reunião, altere segmento, remova áudio ou dispare gravação.

**Em aberto:** as verificações de ponta a ponta com um cliente MCP real
(tarefas 6.1–6.6) dependem de registrar o servidor no Claude Desktop, que é
mudança na configuração de outro aplicativo e ficou com o usuário:

```
voxvault mcp --apply
```

O diagnóstico avisa enquanto isso não for feito.

## Fase 3 — aplicativo desktop

**Construído, ligado ao núcleo e verificado.** Tauri 2 + Svelte 5, 39 pacotes
npm, 5 dependências Rust, binário de 3,59 MB mais instalador.

Verificado dirigindo a janela real sobre o depurador do WebView2, contra os
dados de teste: cinco reuniões listadas com estado real, linha de tempo com
atribuição correta, clicar num segmento move o áudio para o instante certo,
busca achando transcrição e nota, renomear aplicando e a lista atualizando,
remoção de nota pelo diálogo. Nenhum erro de página.

Custo medido em 81 amostras sobre 9 processos: **1,14% de CPU em média, 1,66%
na pior janela de um minuto, 435 MB em média e 556 MB de pico** — contra tetos
de 8%, 15% e 700 MB.

Ressalvas que continuam válidas: a medição correu sem gravação em andamento,
então o custo da captura não está representado; a carga da interface foi gerada
no teto de 20 Hz por trilha, o que faz dessa fatia um limite superior; e a
contagem de processos variou entre 9 e 17 porque o serviço do teste paralelo
trabalhava durante a janela.

## Fase 4 — conveniências

**Completa.** Detecção de reunião no núcleo, bandeja e atalho global no
aplicativo.

A bandeja abre, alterna gravação e fecha; o atalho **Alt+Shift+R** passa pelo
serviço, então uma gravação iniciada pela linha de comando é encerrada pelo
mesmo atalho. A sugestão de início nunca começa a gravar sozinha.

Limite declarado: a bandeja morre com a janela, então a detecção só observa
enquanto o aplicativo está aberto.

O sinal vem do mesmo registro que o Windows usa para o indicador "um aplicativo
está usando seu microfone". Ele dá caminho de executável e dois instantes, e
nada mais: **nenhum áudio, nenhum título de janela, nenhum endereço, nenhum
conteúdo**. Um teste inspeciona o próprio fonte e falha se aparecer chamada a
qualquer um deles.

A regra que separa útil de irritante: não basta um aplicativo de reunião estar
em execução e algo estar capturando — o processo que detém o microfone tem de
ser o próprio aplicativo reconhecido.

## Exclusão de reuniões

**Completa.** Uma reunião sai de vez — áudio, revisões, segmentos, notas,
exportações, as entradas dos dois índices de busca e a pasta — pela tela da
reunião, em lote pela Biblioteca, ou por `voxvault delete <uid>... [--yes]
[--json]`. O servidor MCP não ganhou ferramenta nenhuma, e o teste da fronteira
agora falha diante de qualquer nome com `excluir`, `delete`, `apagar` ou
`remover_reuniao`, o que um teste negativo confirma registrando uma de propósito.

A ordem é o desenho inteiro: a pasta é renomeada para `.excluindo-<uid>` antes
de o banco ser tocado, porque a única falha que uma exclusão encontra na prática
— um arquivo aberto por outro programa — faz a renomeação falhar, e ali ainda
não mudou nada. Depois vem uma transação `BEGIN IMMEDIATE` que confere de novo
gravação e transcrição, e só então a lápide é apagada. Um processo morto no meio
deixa uma reunião inteira ou nenhuma: a inicialização seguinte do serviço
restaura a lápide se a reunião ainda consta, e a apaga se não consta.

A corrida com a fila é decidida pelo SQLite, sem trava própria: a fila agora
reivindica a reunião com um `UPDATE … WHERE attempt_state = 'na_fila'` e só
segue se uma linha mudou. Quem chega primeiro ao bloqueio de escrita vence.

### Verificado executando

- **Com o áudio tocando.** Pela janela real, dirigida pelo depurador do WebView2
  contra um diretório de reuniões fictícias: o reprodutor é parado e tirado da
  página antes da chamada, e a exclusão concluiu em 327 ms sem recusa, sumiu da
  lista e da busca. O diálogo abre com o foco em "Cancelar".
- **Em lote.** Cinco reuniões, uma marcada em transcrição no banco: ela aparece
  não selecionável com a causa; "Marcar todas as visíveis" com filtro marcou só
  as duas do filtro; a barra somou duração e espaço pela prévia do núcleo. Uma
  terceira reunião passou a ser transcrita entre a seleção e a confirmação: as
  outras duas foram excluídas e a recusa apareceu com o nome dela e o motivo.
  `Esc` e "Cancelar" saem do modo sem alterar nada.
- **Ponta a ponta, com o motor real.** Três áudios importados pelo aplicativo,
  transcritos pelo serviço com o `large-v3` na GPU; um excluído pela tela da
  reunião e dois em lote. As pastas deixaram de existir, a busca por um termo de
  cada transcrição passou a voltar vazia, e os três originais importados ficaram
  com a mesma SHA-256 de antes.

### Coberto por teste

`tests/store/test_deletion.py` (prévia sem escrita, zero linhas em cada tabela e
índice, transação interrompida no meio, arquivo aberto sem compartilhamento,
recusa na transação restaurando a pasta, gravação, pausa, transcrição, fila,
importação, lote e as duas interrupções), `tests/cli/test_delete.py` (contrato
JSON exato e códigos de saída 0, 2 e 1), a fila real em
`tests/pipeline/test_queue.py`, as duas recuperações em
`tests/service/test_service.py`, e em Rust a desserialização de uma saída real
do comando.

### Dois defeitos encontrados ao desenhar a exclusão

**As exportações recriavam a pasta de uma reunião excluída.** Regerar as
exportações criava o diretório da reunião se ele não existisse. Uma exclusão que
caísse entre a publicação de uma transcrição e a regeração deixaria
`transcricao.md` numa pasta nova — uma transcrição que ninguém pediu para
guardar, sobrevivendo à exclusão. A regeração agora recusa quando a pasta não
existe.

**A fila morria se a reunião sumisse no meio.** O laço tratava uma falha
marcando a reunião como falha; se a reunião já não existia, essa marcação
também falhava, e a exceção derrubava a linha de execução da fila. Agora uma
reunião que sumiu é simplesmente deixada para trás. Os dois casos têm teste que
falha na versão anterior.

## Decisões que valem ser lembradas

**Inferência roda em subprocesso.** Quando uma gravação começa com transcrição
em andamento, o trabalhador é morto. Descarregar o modelo no próprio processo
não devolve memória de GPU em prazo que se possa prometer, e o orçamento de
2000 ms é da especificação. Sair do processo devolve sempre, e quem garante é o
sistema operacional.

**Exclusividade é por máquina, não por diretório de dados.** Dois serviços
apontando para diretórios diferentes continuariam disputando o mesmo microfone e
a mesma GPU.

**Compressão é sem perdas.** O áudio é preservado para permitir retranscrever
com um modelo melhor depois; um codec com perdas gravaria o teto de qualidade de
hoje em toda transcrição futura daquela reunião.

**Disponibilidade da transcrição e estado da tentativa são campos separados.**
Uma reunião sendo reprocessada tem transcrição completa disponível *e* uma
tentativa em andamento. Um estado único obrigaria a mentir sobre um dos dois.

## Um defeito que só apareceu ao ligar as peças

O serviço **nunca podia ser encerrado pela própria rota de encerramento**.
Encontrado pelo agente que construiu o aplicativo, ao notar que `/saude` dizia
`fila_pendente: 0` enquanto `serve --stop` recusava alegando reuniões na fila.

A causa: o despachante HTTP marca presença de cliente em toda requisição,
incluindo a própria `/encerrar`. A verificação de "está ocupado" contava um
cliente conectado como motivo para recusar, então o pedido se invalidava
sozinho — sempre — e, como não havia gravação, a mensagem caía no ramo errado e
culpava a fila vazia.

Eram duas perguntas coladas numa só. "Não encerre por ociosidade" inclui um
cliente conectado, e deve mesmo. "Não honre um pedido explícito" inclui apenas
gravação ativa ou fila pendente. Agora são duas verificações, e a recusa nomeia
o motivo real.

Vale registrar como foi encontrado: nenhum teste unitário o pegaria, porque
cada peça estava certa isolada. Apareceu porque duas superfícies discordaram
sobre o mesmo fato.

## A primeira reunião real

Uma reunião de 39 minutos no Teams, com fone Bluetooth JBL Tune Flex 2, gravada
e transcrita: 890 segmentos, cada fala atribuída a `você` ou `outros` pela
trilha de onde veio.

**O que deu errado e era do VoxVault.** O serviço residente tinha se encerrado
por ociosidade, e `record` caiu num caminho de gravação direto, anterior ao
serviço e sem supervisor de dispositivo. Quando o fone saiu do ar, as duas
trilhas morreram sem aviso e o contador ficou parado por três horas e meia. O
caminho direto foi removido: `record` grava sempre pelo serviço, e sobe um se
não houver.

**O alarme de divergência era falso.** Media a diferença de comprimento entre as
trilhas, e um loopback ocioso — ninguém falando, nada tocando — não entrega
pacote algum. Isso aparecia como desalinhamento crescente, a cada segundo de
silêncio. Agora mede quanto cada trilha se afasta do relógio do sistema; ao
vivo, na mesma máquina, deu 0 ms.

**O que deu errado e não era do VoxVault.** Por volta dos 38:20 o som parou de
chegar ao ouvido de quem gravava. A mensagem do commit `5823ed4` atribui isso à bateria do
fone, e está errada: o fone continuava ligado, e foi preciso desligar e religar
o Bluetooth. A própria gravação mostra onde o som se perdeu:

- a trilha do sistema, que é o que o Windows entrega ao fone, tem a voz dos
  outros o tempo todo — inclusive o "alô, tá ouvindo?" que não chegou ao ouvido;
- a trilha do microfone continuou com a voz de quem gravava, e os outros
  responderam "eu te ouço": só o sentido computador → fone falhou;
- nenhum log do Windows registra nada entre 20:11 e 20:12:52; o primeiro evento
  é o Bluetooth sendo desligado;
- as duas trilhas têm zero descontinuidades: o VoxVault só lia.

O som se perdeu depois do mecanismo de áudio do Windows: no driver Bluetooth,
no rádio ou no fone. O que esta evidência não exclui é uma falha que só ocorra
com o VoxVault gravando; decide isso saber se ela já ocorria sem ele.

**Um fato medido que confirma o detector de travamento.** Ao desligar o
Bluetooth, o microfone do fone parou de entregar pacotes 6 s antes de o Windows
remover o dispositivo, sem erro algum. É o caso que o supervisor agora trata
como perda — 4 s sem pacote —, onde antes só um erro contava.

## Três defeitos que só apareceram fora do laboratório

**O serviço ficava invisível quando nascia dentro de outro aplicativo.** O
Claude Desktop é instalado como pacote do Windows (MSIX), e tudo o que um
processo iniciado de dentro dele grava em `%APPDATA%` vai para uma pasta
privada do pacote. Um serviço iniciado assim — pelo terminal do Claude, ou por
um servidor MCP que ele sobe — publicava o endereço onde nenhum outro processo
enxergava, mas continuava segurando a trava da máquina, que é objeto do kernel
e não é redirecionada. O aplicativo aberto pelo Explorer não achava o serviço e
não conseguia subir outro: "falhou ao iniciar 3 vezes seguidas".

Verificado, e não suposto: uma escrita em `%APPDATA%` aparece em
`…\Packages\Claude_…\LocalCache\Roaming\`, uma escrita na raiz do perfil não; o
redirecionamento vale para a árvore inteira de processos, e um neto criado com
política de desprendimento continua redirecionado. O estado por usuário —
configuração, ponto de encontro e log do serviço — agora mora em
`%USERPROFILE%\.voxvault`. A configuração antiga é copiada na primeira leitura,
e não movida, porque uma versão anterior do aplicativo ainda a lê de lá.

O registro do servidor MCP não é afetado: o pacote do Claude exclui a própria
pasta de configuração do redirecionamento, e o arquivo que ele lê é o
`%APPDATA%\Claude` real — conferido antes de mexer, e por isso não mexido.

O que continua valendo: um serviço iniciado de dentro do Claude roda dentro do
contêiner dele, e fechar ou atualizar o Claude pode encerrá-lo. Iniciado pelo
aplicativo ou por um terminal comum, não.

**O serviço nunca encerrava por ociosidade.** O laço de supervisão renovava o
"último cliente visto" sempre que um cliente tinha sido visto havia pouco — e
assim cada verificação rearmava a janela que estava verificando. Bastava um
cliente ter falado com o serviço uma vez para ele nunca mais encerrar: medido,
38 minutos residente sem nada a fazer. Agora só trabalho de verdade — gravação
ou fila — empurra o relógio; um cliente conectado continua segurando o serviço
porque as próprias consultas dele o renovam. Os testes cobriam `busy()` e
`has_work()` isoladamente, e cada um estava certo; o defeito estava no laço que
os combinava. O teste novo roda o laço real com relógios curtos e falha na
versão antiga.

**Uma pausa no fone Bluetooth sumia da linha do tempo.** Em modo estéreo
(A2DP), o relógio do fone para quando nada toca: medido, 12 s de silêncio
avançaram 20 ms de posição. O posicionador confiava só na posição, então o
áudio seguinte era colado logo depois do anterior, e tudo dali em diante ficava
12 s adiantado em relação ao microfone. Em reunião o fone fica em modo de
chamada (HFP), que transmite sem parar — por isso os 39 minutos da reunião real
bateram e o defeito não apareceu antes. Agora, quando o instante de aquisição se
afasta da posição além do limiar de 200 ms, o instante decide onde o pacote
fica, e a pausa é registrada como lacuna. Com os pacotes reais do fone: linha
do tempo de 17,312 s contra 17,328 s de relógio de parede, onde antes seriam
4,79 s.

O teste de hardware que exigia menos de 1 ms em todo passo falhava nesse fone
por outro motivo, legítimo: os primeiros ~15 pacotes chegam a cada 20 ms
carregando 10 ms cada — o enlace acordando, ~150 ms no total, abaixo do
limiar. Ele agora mede o fluxo já estabilizado e exige que a partida fique
abaixo do limiar, que é o que garante que ela nunca vire silêncio inventado.

## O que bloqueia o fechamento

1. **Verificação ao vivo de perda e migração de dispositivo**, e de suspensão.
   Exigem alguém desligando o Bluetooth ou suspendendo a máquina durante uma
   gravação.
2. **O registro do servidor MCP**, que é configuração de outro aplicativo.
3. **O veredito de qualidade sobre a reunião real**, que a Fase 0 deixou
   explicitamente para o usuário.
