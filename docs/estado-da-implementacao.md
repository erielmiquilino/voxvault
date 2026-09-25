# Estado da implementação

O que existe, como foi verificado, e o que continua em aberto. Escrito para ser
lido antes de confiar em qualquer parte disto.

A distinção que organiza o documento: **implementado** é código que existe;
**verificado** é código que foi executado e observado. Boa parte do sistema está
nas duas colunas. Onde não está, o motivo está dito.

## Resumo

| | |
|---|---|
| Testes | 551 em Python e 78 em Rust, todos passando, inclusive os de áudio e GPU; o CI roda os que não pedem áudio nem GPU |
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

O limite que esta fase declarou — a bandeja morria com a janela, e a detecção
só observava com o aplicativo aberto — caiu com a bandeja residente, descrita
abaixo.

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

## Bandeja residente

**Implementada e verificada executando, instalada e gravando.** Fechar ou
minimizar a janela a destrói, e o processo continua só com a bandeja, o atalho,
as notificações e uma linha de supervisão. Um WebView2 escondido não libera
nada, então a janela não é escondida: deixa de existir, e é refeita na última
tela quando volta. "Sair do VoxVault" é o único jeito de encerrar o processo, e
nenhum deles encerra o serviço, que é o dono da fila.

### Verificado executando

- **Recolher.** Fechar e minimizar deixaram zero processos do WebView2, nenhum
  botão na barra de tarefas e o aplicativo vivo com 13 MB. O aviso do primeiro
  recolhimento apareceu uma única vez, inclusive através de um reinício.
- **Voltar.** Reabrir restaurou a rota aberta — uma reunião — em 522 ms e
  878 ms até o primeiro desenho, contra o teto de 2 s. Uma segunda instância
  recriou a janela e não deixou processo novo.
- **Gravação sem janela.** Fechar a janela durante uma gravação não perguntou
  nada e a gravação seguiu (173 s → 181,5 s). Encerrar pelo menu da bandeja
  levou o tooltip a "ocioso" em cerca de 1 s, sem abrir janela, com exatamente
  uma notificação de gravação encerrada.
- **Ícone, tooltip e menu.** As quatro formas foram capturadas da bandeja real;
  o tooltip avança a cada segundo gravando; a habilitação do menu confere nos
  estados gravando, ocioso e falha ("Iniciar gravação (serviço indisponível)"
  desabilitado).
- **Notificações.** As seis categorias aparecem; uma categoria desligada é
  respeitada; uma transcrição concluída com o aplicativo na bandeja notifica, e
  abrir o aplicativo depois de transcrições concluídas não notifica nada.
- **Início com o Windows e atalho.** Ligar cria `HKCU\...\Run\VoxVault` com
  `--bandeja` e desligar remove; `--bandeja` sobe só na bandeja. Trocar o atalho
  vale na hora, uma combinação ocupada mantém a anterior, e a escolha sobrevive
  a um reinício.

### Custo medido

`tools/medir-custo.ps1 -Janela bandeja`, 10 minutos ocioso na bandeja, 114
amostras: **0,02% de processador em média e 0,10% de pico, 65,0 MB em média e
65,4 MB de pico** — o serviço com 51,8 MB, o aplicativo com 13,3 MB — contra
tetos de 1% e 100 MB. Com a janela aberta, a medição da Fase 3 continua valendo.

Instalado, o mesmo conjunto media **101,9 MB** ocioso na bandeja depois de a
janela ter sido usada, acima do teto (o defeito está logo abaixo). Corrigido,
a medição do aplicativo instalado, usado e recolhido ficou em **31,4 MB em
média e 0,01% de processador**: o aplicativo com 3,1 MB, o serviço com 9,9 MB,
e o resto nos dois lançadores e no console do serviço.

Gravando 60 minutos na bandeja, instalado e sem rede — com o microfone
silenciado duas vezes e o fone desconectado no meio —, 685 amostras:
**0,20% de processador em média, 0,34% na pior janela de um minuto, 65,9 MB em
média e 70,2 MB de pico**, contra 1,14% e 1,66% medidos com a janela aberta e
o teto de 250 MB. As duas trilhas decodificam inteiras até o fim, e a hora foi
transcrita na GPU em cerca de 70 s depois de encerrar. A trilha do sistema,
porém, terminou mais longa que a reunião — o primeiro defeito logo abaixo.
Corrigido, uma gravação com dois bipes tocados em instantes conhecidos teve a
trilha do sistema igual aos metadados (+0,001 s) e os bipes a no máximo
0,14 s do esperado.

### Quatro defeitos encontrados medindo

**Uma conexão SQLite vazada por requisição.** A primeira medição subiu de
84,6 MB para 106,1 MB em 10 minutos. O servidor HTTP do serviço abre uma linha
de execução por requisição, e o armazenamento por linha de execução abria uma
conexão nova em cada uma sem nunca fechá-la: uns 2 MB por minuto sob a consulta
de 5 s do aplicativo. Cada requisição agora libera a sua ao terminar, e um
teste que falha na versão anterior confere que 80 consultas, em linhas de
execução novas como as do servidor, não deixam nenhuma conexão aberta.

**Um início que dava certo era relatado como falha.** O cliente do aplicativo
esperava 1,5 s por qualquer resposta; abrir os dispositivos passa disso, e o
aplicativo dizia "falhou" enquanto a gravação começava. Cada rota tem agora o
seu prazo — 15 s para iniciar, 180 s para encerrar — e um prazo esgotado é dito
como tal, não como recusa.

**Três inícios empilhados.** Com o serviço de áudio do Windows travado, pedidos
repetidos ficavam todos presos em `IAudioClient::Initialize`. O serviço recusa
agora um segundo início enquanto o primeiro abre os dispositivos, e o aplicativo
não dispara outro enquanto um está em curso.

**A memória que a janela deixava.** Recolher destrói a janela, mas o processo
continuava com as páginas de tudo o que o WebView2 tinha carregado — 36,7 MB,
dos quais só 6,5 MB privados —, e o serviço com as da partida e das
transcrições, 46,4 MB: o aplicativo instalado, usado e recolhido, passava do
teto. Esvaziados por fora, os dois ficaram em 2,6 MB e 10,2 MB e não voltaram
a crescer. O aplicativo devolve agora essas páginas ao Windows 10 s depois de
recolher, e o serviço, uma vez, 30 s depois de o trabalho acabar; elas voltam
se forem usadas, como o Windows faz com uma janela minimizada. A ferramenta de
medição, por sua vez, procurava o serviço só no caminho do checkout, e media o
aplicativo instalado sem ele.

### Gravando, com o aplicativo instalado

Depois que o áudio voltou, no aplicativo instalado e recolhido na bandeja:

- **Pelo menu.** Iniciar pela bandeja gravou em cerca de 1 s sem criar janela
  nem processo do WebView2, com exatamente uma notificação de início; encerrar
  trouxe exatamente uma de encerramento. Com a gravação ativa, o menu mostra
  "Encerrar gravação" e "Pausar" habilitados.
- **Por outra superfície.** `voxvault record` com o aplicativo na bandeja mudou
  o tooltip para "gravando 00:00:02 · …" em 3,6 s, contra o teto de 5 s, e
  gerou uma notificação de início; o relógio do tooltip avança a cada segundo.
- **Pelo atalho em tela cheia.** Com uma janela em tela cheia e sempre no topo
  à frente, Alt+Shift+R iniciou a gravação e, de novo, a encerrou, sem que a
  janela do VoxVault aparecesse.
- **Categoria desligada.** Sem "Gravação iniciada", iniciar não notificou, e o
  encerramento continuou notificando.
- **Sair durante a gravação.** A confirmação nativa mostra há quanto tempo se
  grava; cancelar mantém a gravação e o aplicativo; confirmar encerra a
  gravação de forma limpa, que vai para a fila e é transcrita pelo serviço com
  o aplicativo já fechado, e o aplicativo sai em cerca de 1 s.
- **Microfone mudo pelo Windows.** Numa gravação de 60 minutos pela bandeja,
  sem rede, o microfone silenciado duas vezes pelo painel de som: nos dois
  episódios, um único "Aviso de captura" entre 60 e 62 s depois de silenciar —
  o primeiro durou quase 9 minutos sem aviso novo — e nenhum com o sinal de
  volta. Com o microfone vivo e a sala quieta, o pico ficou no piso de ruído,
  0,0001, e nenhum aviso em 10 minutos.
- **Fone desconectado no meio da gravação.** Tirar o fone com microfone levou
  as duas trilhas a trocar de dispositivo em 2 s — o microfone para o da
  webcam, o áudio do sistema para o monitor —, com um aviso da perda e um da
  troca em cada trilha, e a gravação seguiu sem divergência entre elas.
- **Clique numa notificação.** Com o aplicativo na bandeja, o usuário clicou
  em "Transcrição concluída" na central de notificações, e a janela abriu
  direto na reunião. O mesmo endereço aberto pelo Explorer, como o Windows faz
  no clique, abriu a reunião no processo que já rodava, sem outro.

### Dois defeitos da verificação instalada

**Um pacote silencioso ocupava o triplo do seu tempo.** A trilha do sistema da
gravação de uma hora terminou 16,66 s mais longa que a reunião, com os
metadados dizendo divergência final de 0 ms. O som de uma notificação das
16:32:17 estava no lugar certo do arquivo; o de uma das 17:10:17, 16,6 s
adiante. Entre os dois, o Windows entregou uns 8 s de pacotes marcados como
silenciosos — o que acontece quando um programa mantém a saída aberta depois
de um som —, e cada um era escrito com o número de quadros do dispositivo, a
48 kHz, tomado como quadros de 16 kHz. A linha do tempo contava certo e o
arquivo não, e o alinhamento final compara linhas do tempo. Com um monitor
HDMI ou um aplicativo que segura o áudio em silêncio, a fala dos outros
participantes iria sendo empurrada para depois. Corrigido: o silêncio é
sintetizado nos quadros do dispositivo e passa pela mesma conversão de
qualquer áudio; dois testes que falham na versão anterior ("1 s de pacotes
silenciosos virou 3.00 s") conferem a duração e a posição do áudio seguinte.
Entre as gravações guardadas, a reunião real de 39 minutos não foi afetada
(+0,06 s); duas de teste, sim. O caso não se deixou provocar sob demanda num
dispositivo real; com bipes tocados em instantes conhecidos, a trilha do
sistema os pôs a no máximo 0,12 s do esperado.

**Clicar numa notificação não abria nada.** O clique dependia de um aviso
dentro do processo, que um aplicativo sem pacote só recebe enquanto o Windows
ainda segura a notificação mostrada; clicada na central — para onde todas vão
com o "Não incomodar" ligado —, ela não chegava a lugar algum. Só a decisão do
que abrir tinha teste, e foi o usuário quem clicou. Cada notificação leva agora
um endereço `voxvault://`, registrado para o usuário pelo próprio aplicativo:
o Windows o abre de onde a notificação for clicada, a instância única o
entrega ao processo que já roda, e a janela abre na rota que ele nomeia. O
"Gravar" de uma reunião detectada leva um token de uso único, para que
nenhum outro programa possa gravar abrindo um endereço.

### Em aberto

- **A sugestão de reunião detectada** com o botão "Gravar": pedia uma chamada de
  teste num aplicativo reconhecido, e ficou para depois, por decisão do usuário.
- **Quatro avisos por troca de fone.** Tirar um fone com microfone gera, de uma
  vez, a perda e a troca de cada trilha; juntar as duas de cada trilha num aviso
  só, e pôr acentos nas mensagens que vêm do núcleo ("recuperando por ate
  30s"), deixaria a troca menos ruidosa.
- **A trilha do sistema segue o padrão de comunicações**, por projeto, e o
  diagnóstico avisa quando os padrões de saída se dividem — aqui, depois de
  tirar o fone, comunicações no monitor e multimídia na saída digital — e
  aponta `device_role` para trocar o papel. Nesse caso, um som tocado só no
  padrão multimídia, como o de uma chamada no navegador, fica fora da
  gravação.

### O áudio que parou era o antivírus

Das 09:46 às 14:10 de 23/09 nenhum processo desta máquina abria dispositivo de
áudio — primeiro com `0x80040154`, depois, reiniciado o serviço de áudio, com
fluxos que nunca armavam. A causa era a proteção de acesso ao microfone do
Kaspersky, que segura o áudio de um processo "com restrições" até alguém
responder ao aviso dela. Com as exclusões, todos os dispositivos voltaram a
abrir. O ambiente instalado roda com outro `python.exe`, que pediu a mesma
permissão na primeira gravação. O erro de fluxo que não arma agora aponta essa
causa, e o README e as notas da release explicam o que fazer.

## Distribuição pública

**Implementada, verificada instalada de ponta a ponta e publicada como
0.1.0 e, com a primeira correção, 0.1.1.** O instalador leva o núcleo e o `uv`, não Python: o preparo do primeiro uso monta
o ambiente em `%USERPROFILE%\.voxvault\runtime` a partir do `uv.lock`
versionado. O repositório é público em `github.com/erielmiquilino/voxvault`,
com o CI verde e a `main` protegida como a do `ia-monitor`.

### Verificado executando

- **Instalador.** 13,06 MB contra o teto de 40 MB, instalação por usuário, em
  português. O conteúdo, conferido no próprio pacote: `recursos\nucleo` com os
  55 `.py` na estrutura do pacote, `pyproject.toml`, `uv.lock`, `README.md` e
  `LICENSE`, nenhum `__pycache__`; `recursos\uv\uv.exe`, `recursos\preparo.json`,
  `LICENSE` e `THIRD-PARTY-NOTICES.md` na raiz. O executável de release não
  contém mais o caminho de compilação.
- **Lock reproduzível.** `uv.lock` gerado com o uv 0.12.18 fixado; `uv lock
  --check` passa; um ambiente novo feito dele, com o Python 3.12.14 gerenciado,
  roda a seleção do CI: 527 testes passando.
- **Sem ffmpeg.** A suíte inteira passa com o ffmpeg fora do `PATH`, e o
  núcleo não o menciona mais. `.opus`, `.m4a`, `.mp4` com vídeo e `.wav` a
  44,1 kHz, sintetizados pelo próprio PyAV, decodificam para 16 kHz mono com o
  tom no lugar; 30 minutos de AAC estéreo decodificam em 7 s acrescentando
  9 MB ao processo; um FLAC com uma amostra alterada é reprovado.
- **Sem rede.** Uma transcrição real, na GPU, conclui com `HTTPS_PROXY` e
  `HTTP_PROXY` apontando para uma porta morta, e um modelo ausente vira erro
  tipado com a ação, sem nenhuma tentativa de conexão.
- **Download de modelo retomável.** O `large-v3-turbo`, baixado de verdade
  para uma pasta vazia e interrompido com 563 MB, retomou desses 563 MB,
  terminou com o SHA-256 conferido, sem sobra em disco, e carregou no motor sem
  rede. A troca de modelo nas Configurações baixou o `medium` com progresso
  (258 MB, 961 MB de 1,4 GB) e só gravou a escolha ao terminar.
- **Tela de preparo.** Lida pela automação de interface do Windows, num perfil
  temporário: a GPU encontrada e o `large-v3`; a pasta `D:\VoxVault` mantida
  como padrão anterior porque tem dados; 21,0 MB de interpretador, 95,9 MB de
  dependências e 1,3 GB de CUDA, com o modelo já presente; o espaço pedido por
  volume. Com `VOXVAULT_FORCAR_CPU=1`, sem CUDA e com o turbo na CPU. Com a
  pasta no volume E:, de 2,3 GB livres, a recusa "faltam 1,5 GB no volume E:"
  e o botão desabilitado.
- **Recursos fixados.** Uma soma SHA-256 adulterada em `recursos.json`
  interrompe o build nomeando o uv, sem extrair nada; a correta baixa, confere
  e extrai. `conferir-versao.ps1` aprova `v0.1.0` e reprova `v0.2.0` nomeando
  os cinco componentes.
- **Capturas.** `docs/imagens/` tem Gravação, Biblioteca e Reunião, tiradas do
  aplicativo apontado para reuniões fictícias geradas por
  `tools/dados-de-demonstracao.py`, e os quatro ícones da bandeja.

### Revisão pré-publicação

Sem achados, sobre o histórico inteiro — todas as revisões, não só a última:
todos os commits com autor e committer `erielmiquilino@hotmail.com`; nenhum
token (`gho_`, `ghp_`, `github_pat_`, `sk-`, `AKIA`, `xox*-`, `AIza`) nem chave
privada; nenhum banco, WAL, FLAC, WAV, Opus, MP3 ou vídeo em commit algum;
nenhuma menção ao domínio ou ao e-mail corporativo; nenhum caminho de perfil
pessoal nos arquivos publicados, inclusive dentro das 25 imagens. Do `.claude/`
vão só os comandos e as skills do OpenSpec. Uma observação sem ser achado: a
seção [A primeira reunião real](#a-primeira-reunião-real) descreve a duração, o
fone e os horários de uma reunião verdadeira e cita duas frases genéricas dela,
sem nome, empresa ou conteúdo.

### Três defeitos encontrados ao executar

**A retomada do download não existia mais.** O `huggingface_hub` 1.x passou a
baixar cada arquivo para um temporário de nome único e a apagá-lo na falha: o
modelo interrompido recomeçava do zero, e um processo morto deixava 1,5 GB
órfãos. O núcleo agora baixa por conta própria, com `Range` sobre um parcial de
nome fixo e o hash do repositório conferido.

**O mapeamento dos recursos achatava o núcleo.** Um glob no `bundle.resources`
pôs os 55 `.py` numa pasta só, com dez `__init__.py` sobrepostos. O build agora
monta uma cópia limpa do núcleo e mapeia a pasta.

**O plano rodava duas vezes.** A tela de preparo se replanejava ao preencher a
própria pasta, perdia a origem "padrão anterior" e, numa atualização,
dispararia dois preparos seguidos.

### Instalado, de ponta a ponta

- **Instalação.** Silenciosa, por usuário, sem elevação: `%LOCALAPPDATA%\VoxVault`,
  atalho no menu Iniciar com o AUMID `com.erielmiquilino.voxvault` — as
  notificações do aplicativo instalado chegam por ele — e a entrada em
  Programas.
- **Primeiro uso, máquina limpa (10.1).** Com o perfil do VoxVault zerado e o
  `PATH` só com as pastas do Windows: o preparo completo com GPU —
  interpretador, dependências e CUDA, o modelo já presente na pasta anterior —
  em cerca de 1 minuto; um diálogo sintetizado em `.opus` importado, transcrito
  pelo serviço com `large-v3` na GPU, achado pela busca e excluído; gravação
  pela bandeja. Repetir o preparo com o carimbo alterado levou 2 s e não
  baixou nada.
- **CPU (10.2).** Num perfil temporário com `VOXVAULT_FORCAR_CPU=1`: nenhum
  pacote da NVIDIA no ambiente, o turbo escolhido, e a transcrição registrada
  como `large-v3-turbo/cpu/int8`. Com todos os proxies numa porta morta, o preparo
  parou em "Sem conexão com a internet: o preparo precisa baixar o
  interpretador Python", com Retomar; retomado com rede, o progresso por etapa
  mostrou os bytes ("639 KB de 275 MB" até "275 MB de 275 MB").
- **Sem rede depois do preparo (10.3).** Com todos os proxies mortos:
  importação, transcrição, busca sem acento, exclusão, e o servidor MCP
  instalado respondendo a `buscar`, `criar_nota` e `listar_notas`. Depois, uma
  gravação de uma hora pela bandeja com o aplicativo aberto sem rede,
  transcrita na GPU, achada pela busca da linha de comando e pelo MCP, que
  respondeu também a `estado_do_acervo` e gravou e listou uma nota nela.
- **Atualização.** Da 0.1.0 para uma 0.1.1 de ensaio, com todos os proxies
  mortos: ambiente, modelo, configuração e início com o Windows preservados, o
  núcleo novo instalado no ambiente e a interface aberta em 7 s, sem download.
- **Desinstalação.** Com uma gravação real em andamento, recusada com "Há uma
  gravação em andamento no VoxVault. Encerre-a antes de desinstalar", nada
  removido e a gravação seguindo. Sem gravação: `%USERPROFILE%\.voxvault`, o
  valor `Run`, o atalho e a entrada em Programas removidos, o serviço parado,
  e `D:\VoxVault` intacta. Depois da correção do gancho (7.3), a mensagem
  final diz "O VoxVault foi removido. Suas gravações foram mantidas em
  D:\VoxVault.", e saem também o esquema `voxvault:`, os dois caches do
  WebView2 e a pasta de instalação — as seis reuniões, os modelos e o banco
  ficam onde estavam.
- **Publicação.** O ensaio por dispatch produz o instalador e o
  `SHA256SUMS.txt` como artefatos, sem tag nem versão; a soma confere com o
  arquivo baixado.
- **O instalador do CI, de ponta a ponta (10.1 e 11.3).** O artefato do
  ensaio, instalado no perfil real com o estado do VoxVault ausente e o `PATH`
  só com as pastas do Windows — o próprio perfil, e não um `USERPROFILE` vazio
  como na decisão 15, por escolha do usuário: preparo completo com GPU em
  cerca de 45 s; uma gravação de 30 s iniciada e encerrada pelo menu da
  bandeja, sem janela e com uma notificação de cada, transcrita pelo
  `large-v3` na GPU 18 s depois de encerrar; um `.opus` importado e transcrito
  pelo serviço em 14 s; os dois achados pela busca sem acento e excluídos,
  com a prévia antes, sem sobrar pasta.
- **Atualização sobre o mesmo ambiente, sem rede.** Um segundo artefato
  instalado por cima, com o carimbo do ambiente envelhecido e todos os
  proxies mortos: o aplicativo refez o ambiente pelo cache, pôs o núcleo novo
  e abriu a interface em cerca de 8 s, sem download.
- **A 0.1.0 publicada (11.4).** A tag `v0.1.0`, enviada com a confirmação do
  usuário, gerou a release "VoxVault 0.1.0" em
  `github.com/erielmiquilino/voxvault/releases/tag/v0.1.0`, com
  `VoxVault-0.1.0-setup.exe` (13,1 MB) e `SHA256SUMS.txt`: a soma confere com
  o arquivo baixado da própria release, e as notas trazem o texto permanente.
  Esse instalador, posto no perfil real depois da desinstalação, fez o preparo
  do primeiro uso em cerca de 1 minuto e é o que fica instalado.

### Quatro defeitos que só a instalação mostrou

**O loopback passava pelo proxy.** Com `HTTP_PROXY` na máquina, o núcleo
entregava ao proxy até a chamada para 127.0.0.1: `serve --status` dizia não
haver serviço, e o desinstalador deixaria passar uma gravação em andamento.

**A atualização parava o serviço pelo núcleo antigo.** O aplicativo novo pede a
parada ao ambiente antigo, que tinha o defeito anterior; e toda falha de parada
era dita como "está gravando". Os processos do núcleo recebem agora `NO_PROXY`
com o loopback, e só a recusa de verdade é dita como gravação.

**O núcleo novo não chegava ao ambiente.** O uv só percebe mudança num projeto
local pelo `pyproject.toml`, e o instalador preserva as datas dos arquivos. A
etapa de dependências reinstala agora o pacote do núcleo, e cada `sync` tenta
primeiro pelo cache — sem isso, a atualização falhava offline revalidando no
PyPI o *build backend* que já tinha.

**A mensagem final da desinstalação não dizia a pasta.** A busca do gancho do
instalador devolvia dois registradores trocados.

### Mais quatro, na verificação final

**O horário do `list` e do `show` era UTC.** Uma gravação das 15:42 aparecia
como 18:42, enquanto a prévia da exclusão, as notas e as exportações já
usavam o horário local. Um teste com fuso fixo, que vale também no CI em UTC,
falha na versão anterior.

**A importação mandava rodar a fila à mão** mesmo com o serviço residente de
pé, que a transcreve sozinho em segundos. Agora só manda quando nenhum
serviço serve aquela pasta de dados.

**"Volume C:: precisa de 2,7 GB."** A raiz de uma unidade já termina no seu
dois-pontos, e o preparo acrescentava outro.

**Um teste que falhava só nesta máquina.** O de prazo da migração abria uma
conexão crua logo depois de matar o processo que segurava a trava, e o Windows
ainda liberava as travas do processo morto: `disk I/O error`. O leitor do
próprio banco se recupera disso na primeira tentativa — conferido em sete
rodadas —, e o teste passou a ler por ele.

### Depois da publicação

**Uma gravação sem sessão não podia ser excluída.** O usuário tentou excluir uma
reunião da madrugada de 23/09, transcrita, e cada clique respondia "está sendo
gravada". O encerramento tinha terminado os arquivos e enfileirado a
transcrição, mas a linha no banco nunca saiu de "gravando" — o que acontece
quando o processo morre entre os arquivos e o banco, quando a escrita do
encerramento falha ou quando a máquina suspende no meio —, e a recuperação só
olhava finalizações interrompidas no disco. Agora, na partida e na retomada da
suspensão, toda reunião aberta que não é a sessão viva é fechada pelo que os
seus arquivos dizem; três testes cobrem o fechamento, a reunião sem áudio e a
gravação viva, que fica intocada. A interface também deixou de empilhar a
mesma mensagem: repetida, renova a que está na tela, em vez das oito cópias
que o usuário viu. A reunião dele foi fechada à mão do mesmo jeito que a
recuperação faria, e a correção saiu na 0.1.1 — com o servidor MCP passando a
informar a versão do núcleo em vez de um "0.1.0" escrito no código. A 0.1.1,
instalada aqui por cima da 0.1.0, refez o ambiente pelo cache sozinha e
responde 0.1.1 no serviço e no MCP; as somas dela já saem com LF, e o
`sha256sum -c` as confere.

**Falas de minutos depois na mesma linha.** Na primeira reunião real gravada
com a 0.1.1 — um stand-up de 13 minutos —, 148 dos 181 trechos saíram marcados
como "fala sobreposta". Um trecho do microfone ia de 16 s a 318 s: a primeira
palavra dita aos 16,6 s e as outras quatro aos 333 s. A detecção de voz entrega
ao modelo a fala sem os silêncios, falas separadas por minutos ficam lado a
lado, e o modelo as devolvia como uma linha só — com o texto no lugar errado e
tudo o que foi dito entre elas marcado como sobreposto. Os tempos por palavra
passam a ser calculados, e um trecho é partido onde duas palavras seguidas
estão a mais de 2 s; dentro de um trecho, 95% das palavras ficam a menos de
0,2 s. Reprocessada pelo motor corrigido, a mesma reunião ficou sem nenhum
trecho acima de 30 s e com 19 sobreposições, as de verdade, sem perder palavra
(278 e 692 antes, 297 e 686 depois). O custo, medido nela: nenhum na GPU, 8%
na CPU. Saiu na 0.1.2, que instalada aqui refez a mesma reunião sem nenhum
trecho acima de 9 s e com 23 sobreposições, as de verdade.

**O vocabulário de domínio só valia para os primeiros 30 s.** Ele ia ao modelo
como prompt inicial, e com o texto anterior não levado adiante o prompt é
zerado depois da primeira janela. Vai agora como *hotwords*, que entram em
toda janela: na mesma reunião, com o vocabulário do usuário, um nome de
produto antes ouvido errado apareceu certo duas vezes e mais dois termos
foram corrigidos, com a contagem de palavras igual (984 contra 993). Saiu na
0.1.3, que instalada aqui refez a reunião com esses três acertos — 180
trechos, 20 sobreposições, nenhum acima de 10 s.

**A gravação seguinte herdava o título da anterior.** Durante a gravação, o
campo de título mostra o título dela, que o serviço gera com a hora do início;
ao encerrar, o estado era zerado menos o título, e uma gravação que começou às
19:37 ficou chamada "Reunião de 24/09/2026 às 19:31". O título passou a ser
limpo ao fim da gravação — e só ali, então o que se digita para a próxima, com
o aplicativo ocioso, fica.

**`voxvault` pelo nome.** Um agente do usuário, trabalhando num terminal, se
batia para achar a linha de comando: ela só existia em
`%USERPROFILE%\.voxvault\runtime\ambiente\Scripts`, e a especificação
proibia mexer no `PATH`, para que o Python do ambiente nunca respondesse por
`python`. A mudança `add-cli-on-path` refez esse requisito. O preparo copia
`voxvault.exe` e `voxvault-mcp.exe` para `%USERPROFILE%\.voxvault\bin` — os
lançadores do uv levam o caminho do interpretador e rodam de qualquer pasta — e
põe só essa pasta, uma vez, no fim do `PATH` do usuário. O valor é lido e
gravado pela API do registro, sem expandir variáveis, com o tipo que tinha e no
tamanho que tiver, e uma leitura que falha por qualquer motivo que não seja a
ausência do valor interrompe a integração: tomada por vazia, gravaria de volta
um `PATH` só com a pasta. A desinstalação a tira pelo próprio aplicativo
(`--remover-do-path`), fora do modo de atualização.

Instalado aqui por cima da 0.1.3, o ensaio acrescentou a pasta ao fim do `PATH`
com as outras 22 entradas iguais, na mesma ordem, e o tipo mantido. Num terminal
aberto depois, `voxvault list` respondeu pelo nome no cmd e no Git Bash, e
`python` continuou sendo o Python 3.14 do usuário, com o `hf` e o `uvicorn` do
ambiente fora do alcance. O `--remover-do-path` devolveu o `PATH` ao que era —
menos o `;` vazio do fim — com o aplicativo aberto e a janela intacta, e o
preparo seguinte pôs a pasta de volta. O desinstalador compilado do ensaio traz
o comando; o da 0.1.3, não.

### Em aberto

- **As somas da 0.1.0 saíram com CRLF**, e o `sha256sum -c` do Linux não as
  lê — o `Get-FileHash` que as notas indicam lê. O workflow grava LF a partir
  da próxima versão.
- **As notas publicadas da 0.1.0 não citam o endereço `voxvault:`**, que o
  clique nas notificações passou a registrar; o texto permanente e o README já
  citam.

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
4. **Resolvido: o áudio desta máquina**, que desde as 09:46 de 23/09 recusava
   abrir qualquer dispositivo, em qualquer processo. Reiniciar o serviço de
   áudio mudou o sintoma de erro COM para fluxos que nunca armavam; a causa era
   a proteção de acesso ao microfone do Kaspersky, que segura o áudio de um
   processo até alguém responder ao aviso dela. Com o VoxVault nas exclusões,
   todos os dispositivos voltaram a abrir. O erro de fluxo que não arma agora
   aponta essa causa.
5. **O preparo completo, a instalação e a publicação**, cada um esperando uma
   decisão: ver [Distribuição pública](#distribuição-pública).
