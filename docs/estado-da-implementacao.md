# Estado da implementação

O que existe, como foi verificado, e o que continua em aberto. Escrito para ser
lido antes de confiar em qualquer parte disto.

A distinção que organiza o documento: **implementado** é código que existe;
**verificado** é código que foi executado e observado. Boa parte do sistema está
nas duas colunas. Onde não está, o motivo está dito.

## Resumo

| | |
|---|---|
| Testes | 436 em Python e 5 em Rust, todos passando; 9 pulados (exigem hardware ausente agora) |
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

**Em aberto:** as tarefas marcadas `(usuário)` — fornecer áudio de reunião real
e decidir entre local e provedor online. A bancada está pronta para rodar sobre
esse material assim que existir.

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

A máquina passou a recusar abrir qualquer dispositivo de áudio
(`REGDB_E_CLASSNOTREG`) durante o trabalho. Os dispositivos aparecem na
enumeração e não abrem — estado do serviço de áudio do Windows, não do VoxVault.

Ficaram sem verificação ao vivo:

- migração de trilha ao trocar o dispositivo padrão no meio da gravação
  (12 testes com dispositivos falsos cobrem a lógica inteira);
- reação à perda de dispositivo e o encerramento de trilha como incompleta;
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

## O que bloqueia o fechamento

1. **O áudio da máquina.** Enquanto os endpoints não abrirem, a verificação ao
   vivo de migração de dispositivo e de suspensão não é possível. Reiniciar o
   `Audiosrv` com elevação resolve.
2. **O registro do servidor MCP**, que é configuração de outro aplicativo.
3. **Áudio de reunião real**, para o veredito de qualidade que a Fase 0 deixou
   explicitamente para o usuário.
