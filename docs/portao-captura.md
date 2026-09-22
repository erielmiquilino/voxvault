# Portão de validação do backend de captura

Tarefas `2.0`, `2.0.1`, `2.0.2`, `2.0.3` e `2.0.4` de `openspec/changes/add-meeting-recording-core/tasks.md`.

**Veredito: APROVADO**, com uma metade da validação bloqueada por ausência de hardware
nesta máquina — detalhada e delimitada abaixo, não escondida.

---

## 1. Resumo

| Item | Resultado |
|---|---|
| Mecanismo adotado | Ligação nativa direta ao WASAPI a partir do Python, via `ctypes` (COM), sem `comtypes` e sem `pywin32` |
| Dependências de terceiros no pacote de captura | **nenhuma** |
| 2.0.1 — os três dados chegam preenchidos | **Aprovado** (medido em captura real de loopback) |
| 2.0.2 — os instantes não se deslocam com o atraso de entrega | **Aprovado** (erro de passo estável em ~21 µs sob carga, contra ~115 ms do método do PortAudio) |
| 2.0.3 — a sinalização de descontinuidade é recebida | **Aprovado**, com ressalva importante: a flag **não é determinística** |
| 2.0.4 — faixa de versão de Python imposta | **Nenhuma.** O backend não impõe teto. O `<3.13` atual não se sustenta (§9) |
| Trilha de microfone | **Não verificada** — não existe dispositivo de entrada nesta sessão (§2) |

---

## 2. Ambiente da medição — leia antes dos números

O processo executa na sessão `rdp-tcp#0` (Área de Trabalho Remota). Nessa condição o
Windows expõe à sessão apenas o endpoint redirecionado, e a enumeração com máscara de
**todos** os estados devolve exatamente um dispositivo:

```
ENTRADA (0):
  (nenhum)

SAIDA (1):
  Áudio Remoto [saida] padrao de: comunicacoes, console, multimidia
    | RemoteNetworkDevice | ativo
    {3.0.0.00000001}.{6C26BA7D-F0B2-4225-B422-8168C5261E45}
```

A máquina **tem** hardware de áudio (Realtek, NVIDIA HDMI, microfone da webcam C920,
fones JBL), mas esses endpoints pertencem à sessão de console e não são visíveis aqui.
A redirecção de gravação está permitida no servidor (`fDisableAudioCapture = 0`), de modo
que a ausência de microfone vem do cliente RDP, que não redireciona um.

Consequências, declaradas com precisão:

- **O que foi medido de verdade:** a captura de *loopback* sobre um endpoint de
  renderização real, que é o caminho mais difícil dos dois — é ele que fica ocioso, que
  exige `AUDCLNT_STREAMFLAGS_LOOPBACK` e onde a disciplina de carimbo de tempo importa.
  As chamadas `GetBuffer` / `ReleaseBuffer` / `GetNextPacketSize` e o preenchimento dos
  três dados são idênticos para um endpoint de captura; a diferença entre as duas
  trilhas é a flag passada a `Initialize` e o sentido do endpoint.
- **O que ficou bloqueado:** abrir um microfone, e portanto abrir as duas trilhas
  simultaneamente e medir o deslocamento entre elas. Os testes correspondentes existem,
  estão marcados `@pytest.mark.hardware` e **pulam com motivo explícito** em vez de
  passarem vazios.

### 2.1 O endpoint caiu durante a sessão — e isso virou um teste involuntário

Depois de todas as medições estarem colhidas, o canal de áudio do RDP caiu sozinho. A
degradação passou por três estados distintos, e o código reportou cada um corretamente:

1. `DeviceLostError: IAudioClient::Initialize falhou: 0x88890004` — exatamente a
   classificação que a política de recuperação de 30 s das tarefas 2.10/2.11 precisa
   distinguir de uma falha comum;
2. enumeração devolvendo **zero** dispositivos, em ambos os sentidos;
3. o endpoint reaparecendo na lista mas **inutilizável**: `GetMixFormat` respondendo
   `0x80040154` (`REGDB_E_CLASSNOTREG`).

O terceiro estado é o mais traiçoeiro, porque "enumerável" e "utilizável" deixam de ser a
mesma coisa — uma política de dispositivo que só verifique presença escolheria um endpoint
que não abre. Duas providências tomadas:

- o portão reporta a falha e segue para os passos seguintes, em vez de abortar com
  *traceback*;
- os testes marcados `hardware` tentam abrir o endpoint antes de usá-lo, e **pulam com o
  motivo** quando ele está presente mas quebrado, em vez de acusarem falha de um código
  que funcionava minutos antes.

Todas as medições das seções 4 a 7 foram feitas com o endpoint saudável, e os 10 testes de
hardware passavam nessa condição.

---

## 3. Mecanismo escolhido, e por que não os outros

A decisão é a que `design.md` já antecipava, agora com medição: **ligar direto ao WASAPI
a partir do Python com `ctypes`**.

`IAudioCaptureClient::GetBuffer` é chamado com os cinco parâmetros, e os dois últimos são
ponteiros de saída reais:

```
GetBuffer(BYTE** ppData, UINT32* pNumFramesToRead, DWORD* pdwFlags,
          UINT64* pu64DevicePosition, UINT64* pu64QPCPosition)
```

Passar `NULL` nesses dois é precisamente o defeito que reprovou o PortAudio, então a
assinatura declarada é verificada por teste (`test_get_buffer_declares_both_out_pointers`)
para que uma edição futura não a reduza silenciosamente.

**Por que `ctypes` puro e não `comtypes` ou `pywin32`.** O que é preciso são oito
interfaces e cerca de vinte métodos. `comtypes` gera invólucros de type library na
importação e `pywin32` carrega uma extensão grande; ambos custam tempo de partida em
toda superfície que toca áudio, e partida rápida é requisito de produto aqui. Medido: o
pacote `voxvault.capture` importa **zero** dependências de terceiros.

*Alternativas descartadas nesta rodada:* componente nativo auxiliar em Rust — atende ao
contrato, mas acrescenta cadeia de compilação e um artefato binário por arquitetura sem
oferecer nada que o `ctypes` não tenha entregue; cabo de áudio virtual — exige instalar
driver, proibido pela especificação.

---

## 4. Tarefa 2.0.1 — os três dados chegam preenchidos

Captura de loopback sobre o endpoint de saída, com um tom de 441 Hz gerado pelo próprio
portão (via `IAudioRenderClient`) para que haja o que capturar.

```
[loopback] 44100 Hz, 2 canais, float32 (bloco 8 B) | buffer 8820 quadros (200 ms)
           periodo 10,0 ms | prioridade: Pro Audio

  devpos=3095379  qpc_ns=22159468975700  flags=DS-  quadros=441  bytes=0
  devpos=3095820  qpc_ns=22159478982200  flags=D--  quadros=441  bytes=3528
  devpos=3096261  qpc_ns=22159488970900  flags=D--  quadros=441  bytes=3528
  devpos=3096702  qpc_ns=22159498967700  flags=D--  quadros=441  bytes=3528
  devpos=3097143  qpc_ns=22159508988000  flags=---  quadros=441  bytes=3528
  ...
  pacotes=602  quadros=265482  descontinuidades=4  timestamps invalidos=0
```

- **Posição de dispositivo:** presente e monotônica, avançando exatamente 441 quadros por
  pacote (10 ms a 44,1 kHz).
- **Timestamp de alta resolução:** presente, avançando ~10.000.000 ns por pacote — o
  mesmo intervalo, pela via independente do relógio.
- **Flags:** presentes e efetivamente variando. `S` (`SILENT`) aparece antes de o tom
  encher o anel; `D` (`DATA_DISCONTINUITY`) aparece no início do fluxo, que é normal e
  não é perda. Nenhum `TIMESTAMP_ERROR` em 602 pacotes.

O `qpc_position` vem em unidades de 100 ns e é convertido para nanossegundos na origem,
dentro do laço de leitura, porque `CapturePacket.qpc_ns` é o que o restante consome.

---

## 5. Tarefa 2.0.2 — os instantes reportados sob carga

Este é o teste que reprova o PortAudio e aprova um backend adequado, então vale explicar
**o que** é medido antes dos números.

A grandeza discriminante é o **erro de passo**: entre dois pacotes consecutivos,
`ΔQPC − Δposição_convertida_em_tempo`. Ela é imune à deriva lenta entre o relógio de
áudio e o QPC (ao contrário de um resíduo ancorado no primeiro pacote), e — decisivo —
**não é inflada por uma lacuna real de captura**, porque nesse caso as duas grandezas
avançam juntas. O que sobra nela é exatamente a dependência do carimbo em relação à
entrega.

O mesmo conjunto de pacotes é avaliado de duas maneiras na mesma execução:

1. como o WASAPI reporta (`qpc_ns` contra `device_position`);
2. como o PortAudio sintetiza o `inputBufferAdcTime` — relógio lido na chegada do pacote,
   somado à latência estimada.

### Carga leve (1 thread), sem perda de captura

| | ocioso | sob carga |
|---|---|---|
| pacotes | 601 | 570 |
| descontinuidades | 0 | **0** |
| **passo WASAPI**, máx \|·\| | **22,1 µs** | **21,3 µs** |
| passo WASAPI, desvio | 9,4 µs | 9,2 µs |
| **passo estilo PortAudio**, máx \|·\| | 9.989 µs | **115.566 µs** |
| entrega − carimbo, amplitude | 17.126 µs | 327.555 µs |

Sem nenhuma perda de captura, o atraso de entrega saltou para ~295 ms, e o instante
calculado à maneira do PortAudio acompanhou esse atraso: **fator 5.426×** contra o
instante reportado pelo WASAPI, que não se moveu.

### Carga pesada (12 threads + 2 processos)

| | ocioso | sob carga |
|---|---|---|
| **passo WASAPI**, máx \|·\| | 22,1 µs | **19,9 µs** |
| **passo estilo PortAudio**, máx \|·\| | 9.993 µs | **1.002.207 µs** (1,0 s) |
| descontinuidades | 0 | 23 |

**Fator 50.362×.** E, apesar de 23 descontinuidades reais nesta fase, o erro de passo do
WASAPI continuou em ~20 µs — confirmando na prática que a grandeza escolhida separa
"lacuna real" de "carimbo contaminado", que era a razão de escolhê-la.

### Por que isso é o critério, e não uma tolerância arbitrária

O limiar de preenchimento de silêncio da especificação é 200 ms. Um instante reportado
que varia ~21 µs está **quatro ordens de grandeza** abaixo dele: não há carga concebível
em que esse erro produza inserção de silêncio. Um instante derivado da entrega variou
115 ms na carga leve e 1,0 s na pesada — ou seja, ultrapassa o limiar sozinho, e faria o
mecanismo de correção de deriva fabricar silêncio onde não houve lacuna alguma. É esse o
defeito, e ele foi medido, não suposto.

---

## 6. Tarefa 2.0.3 — a sinalização de descontinuidade

Método: parar de drenar por mais tempo que o buffer do motor (200 ms), para que o driver
sobrescreva quadros nunca lidos. A perda é então observada por dois canais independentes:
a flag do sistema operacional e o salto da posição de dispositivo.

```
  pausa 2,0 s -> pacotes=374  sinalizados=3  perda=1830 ms  flags=[DATA_DISCONTINUITY, SILENT]
  pausa 1,0 s -> pacotes=372  sinalizados=3  perda= 830 ms  flags=[DATA_DISCONTINUITY, SILENT]
  pausa 0,6 s -> pacotes=370  sinalizados=3  perda= 420 ms  flags=[DATA_DISCONTINUITY, SILENT]

    flag DATA_DISCONTINUITY recebida ......... sim (9 pacotes em 3/3 provocacoes)
    perda visivel na posicao de dispositivo .. sim (3/3 provocacoes)
```

**A flag é recebida.** Mas execuções repetidas mostraram algo que precisa constar:

> **A flag não é determinística neste endpoint.** Em execuções anteriores, uma perda real
> de **1830 ms** passou **sem sinalização alguma** (`flags vistas=['SILENT']`), enquanto
> uma perda de 420 ms na mesma sessão foi sinalizada. O salto da posição de dispositivo,
> por outro lado, esteve presente em **todas** as provocações, sem exceção.

Isso não é um defeito do backend, e sim uma propriedade do driver. A consequência de
projeto é direta e felizmente já está na especificação, que liga as duas condições por
**"ou"**:

> "...quando a posição de dispositivo avançar mais do que o número de quadros
> efetivamente entregues, **ou** quando o sistema operacional sinalizar descontinuidade."

Implementar apenas a flag perderia lacunas reais de quase dois segundos. A regra
implementada em `anchor.decide_gap` trata o salto de posição como o canal primário e a
flag como gatilho adicional que dispensa o limiar de 200 ms — porque uma perda
sinalizada não é jitter.

---

## 7. Dirigido por evento contra polling

Medido nesta máquina, sobre o mesmo endpoint, com tom ativo:

```
  loopback/evento    pacotes=304  quadros=134064  descont=0  entrega mediana=-29,64 ms
  loopback/polling   pacotes=303  quadros=133623  descont=4  entrega mediana=-28,62 ms
```

Com o endpoint **ativo**, `AUDCLNT_STREAMFLAGS_EVENTCALLBACK` funcionou no loopback deste
driver, e funcionou bem. Ainda assim a configuração padrão adotada é:

- **microfone → dirigido por evento** (`EVENTCALLBACK`);
- **loopback → polling** com `GetNextPacketSize` e sono de 5 ms.

O motivo é o modo de falha, não o desempenho médio. O evento do loopback só é sinalizado
enquanto o endpoint está ativo; a combinação é documentadamente instável em parte dos
drivers, e uma trilha que para de entregar silenciosamente é muito pior do que uma que
custa um sono de 5 ms por iteração. A medida de custo do polling confirma que é barato:
entrega mediana praticamente idêntica à do modo por evento.

A escolha é configurável por fluxo (`CaptureStream(event_driven=...)`), e o modo
`gate probe` remede as quatro combinações em qualquer máquina.

> **Verificação que ficou incompleta.** A transição *ocioso → retomada* com evento — o
> modo de falha que realmente importa — não pôde ser medida: as duas tentativas
> esbarraram no custo de `Initialize` a frio (§8) e depois na queda do endpoint. Fica
> registrado como **não verificado neste ambiente**. O padrão escolhido (polling) é o
> lado seguro dessa dúvida.

---

## 8. Achados operacionais que não estavam previstos

### 8.1 O primeiro `IAudioClient::Initialize` do processo pode levar dezenas de segundos

Medição por etapa, três execuções em processos novos:

```
run0  enum=0,0  dev=1,2  activate=5,7  mixfmt=1,9  init=58566,1  svc=0,1  start=5,7  ms
run1  enum=0,1  dev=27,4 activate=23,1 mixfmt=1,5  init=  185,3  svc=0,1  start=0,5  ms
run2  enum=0,1  dev=1,5  activate=1,8  mixfmt=0,2  init=   56,1  svc=0,0  start=0,5  ms
```

O custo está inteiramente dentro de `Initialize`, do lado do sistema operacional — não é
`ctypes`, nem COM, nem Python. Foram observados **11,4 s**, **13,2 s** e **58,6 s** em
aberturas a frio, e mais de **120 s** depois de o endpoint remoto adormecer. Aquecido, a
mesma chamada custa 8–185 ms.

É plausível que seja específico do endpoint de áudio remoto do RDP, que precisa negociar
o canal de áudio com o cliente. **Precisa ser reconfirmado num endpoint local** antes de
virar premissa de projeto.

Consequências já incorporadas:

- `CaptureStream.start()` mede e expõe `arming_ms` e `initialize_ms`. O teto de 1500 ms da
  especificação é julgado por quem chama, lendo esses valores e registrando a violação
  nos metadados — **não** é convertido em falha de abertura, que descartaria uma gravação
  por um endpoint meramente lento.
- `stream.prewarm(endpoint_id)` abre e fecha um fluxo de descarte. O serviço residente
  deve pagar esse custo **ao subir**, não quando o usuário aperta gravar. Sem isso, o teto
  de 1500 ms é inalcançável na primeira gravação após o boot.
- O `timeout` de `start()` é um guarda contra driver travado (padrão 120 s), não o
  orçamento de latência.

### 8.2 No loopback, o carimbo está *à frente* da entrega

`entrega − carimbo` é **negativa** e estável, com mediana entre −21 ms e −29 ms. Faz
sentido: num fluxo de loopback o `QPCPosition` corresponde ao instante em que a mistura é
apresentada pelo endpoint, que é posterior ao momento em que os quadros são repassados ao
capturador.

Não é problema — é um deslocamento aproximadamente constante, absorvido pela âncora por
trilha. Mas é exatamente por isso que a especificação exige uma âncora **por trilha** com
a latência registrada nos metadados: subtrair os dois carimbos sem isso colocaria a
trilha do sistema ~25 ms adiantada em relação à de entrada.

### 8.3 Prioridade de thread

`AvSetMmThreadCharacteristicsW("Pro Audio")` foi obtida com sucesso via `ctypes`
(`avrt.dll`), retornando um índice de tarefa válido em todas as execuções. É aplicada na
thread de leitura e revertida no encerramento. Quando `avrt.dll` não existe, ou a chamada
falha, o fluxo segue e o motivo fica em `CaptureStream.priority_status`, que o portão
imprime — degradação registrada, não silenciosa.

---

## 9. Tarefa 2.0.4 — faixa de versão de Python imposta pelo backend

**O backend de captura não impõe teto algum.** Ele usa `ctypes` (biblioteca padrão) e a
API WASAPI do sistema operacional. Não há extensão compilada, não há ABI de terceiros, não
há wheel a esperar. Qualquer CPython com `ctypes` e `WinDLL` serve; o código usa
tipagem `X | None` e `enum.StrEnum`, o que dá um piso prático de **3.11**.

Isso deixa o teto `<3.13` do `pyproject.toml` sem fonte. O comentário atual atribui-o a
`soxr`, mas a verificação no PyPI mostra que a atribuição está incorreta:

| pacote | versão | wheels Windows relevantes |
|---|---|---|
| `soxr` | 1.1.0 | `soxr-1.1.0-cp312-**abi3**-win_amd64.whl` |
| `numpy` | 2.5.3 | `cp312`, `cp313`, `cp314`, `cp315` |
| `soundfile` | 0.14.0 | `py2.py3-none-any` (Python puro) |
| `ctranslate2` | 4.8.2 | `cp312`, `cp313`, `cp314` |
| `faster-whisper` | 1.2.1 | `py3-none-any` |

A wheel do `soxr` é **abi3** (ABI estável) a partir de `cp312`: ela instala em 3.13, 3.14 e
seguintes. Não há, portanto, "salto do 3.13" — `soxr` cobre 3.13 pela ABI estável, e
nenhum dos outros pacotes tem teto.

**Recomendação:** revisar para `requires-python = ">=3.12"`, sem teto. O piso 3.12 vem do
`numpy` (`requires_python = ">=3.12"`), não do áudio.

*Não alterei o `pyproject.toml`* — está fora do escopo de escrita deste trabalho. A
decisão e a evidência ficam registradas aqui para quem o mantém.

---

## 10. O que foi verificado, e como

### Verificado executando em hardware real

- Enumeração de endpoints com identificador persistente, nome amigável, `form factor` e
  o padrão de **cada** papel separadamente (`eConsole`, `eMultimedia`, `eCommunications`).
- Abertura de loopback em endpoint de renderização com `AUDCLNT_STREAMFLAGS_LOOPBACK` e
  `GetService(IID_IAudioCaptureClient)`, sem cabo virtual e sem driver.
- Leitura do formato de mistura: `WAVE_FORMAT_EXTENSIBLE` / IEEE float, 44,1 kHz, 2 canais,
  32 bits.
- Tarefas 2.0.1, 2.0.2 e 2.0.3 — os números das seções 4 a 6.
- Loopback ocioso não entrega pacote algum, e isso é comportamento correto.
- `DeviceLostError` na invalidação real do endpoint.
- Prioridade MMCSS "Pro Audio".
- 10 testes `@pytest.mark.hardware` passando.

### Verificado por teste sintético, sem hardware

51 testes, executando em 0,15 s. Cobrem, com pacotes construídos à mão:

- empacotamento das estruturas (`sizeof(WAVEFORMATEX) == 18`, `WAVEFORMATEXTENSIBLE == 40`);
  errar isso desloca todos os campos após a taxa de amostragem;
- análise de `WAVE_FORMAT_EXTENSIBLE`, IEEE float simples e PCM inteiro, além das recusas:
  cabeçalho truncado, extensão ausente, `nBlockAlign` incoerente, subformato desconhecido;
- aritmética da âncora, incluindo o caso em que o carimbo *é* o relógio de entrega — a
  falha do PortAudio, reproduzida para que o discriminante fique visível em teste;
- **atraso de entrega sem perda não insere silêncio algum** (o teste que separa a
  implementação correta da ingênua);
- limiar de 200 ms, dentro e fora;
- descontinuidade sinalizada tratada como lacuna real mesmo abaixo do limiar;
- pacotes sobrepostos: só o excedente é escrito, a posição nunca recua, nada é duplicado;
- loopback ocioso no início da sessão: âncora no primeiro pacote, intervalo preenchido;
- posição derivada da contagem contínua quando o carimbo é inválido, e restabelecimento da
  âncora no pacote válido seguinte;
- a posição escrita corresponde ao tempo decorrido após lacunas, jitter e sobreposição
  misturados.

### Bloqueado por ausência de hardware nesta sessão

- Abrir a trilha de microfone (não existe endpoint de entrada — §2).
- Abrir as duas trilhas simultaneamente e medir o deslocamento entre elas.
- A transição *ocioso → retomada* no modo por evento (§7).
- Confirmar se o custo de `Initialize` a frio (§8.1) existe fora do endpoint remoto.

Os testes para os dois primeiros itens estão escritos e marcados `hardware`; numa máquina
com microfone eles executam sem alteração alguma.

---

## 11. Como reproduzir

```powershell
cd E:\projetosAleatorios\VoxVault\voxvault-core

# portão completo
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m voxvault.capture.gate all

# modos individuais
... -m voxvault.capture.gate list            # dispositivos e padrões por papel
... -m voxvault.capture.gate prewarm         # custo da primeira abertura
... -m voxvault.capture.gate probe           # evento x polling, quatro combinações
... -m voxvault.capture.gate packets         # 2.0.1
... -m voxvault.capture.gate load --threads 1 --processes 0   # 2.0.2, carga leve
... -m voxvault.capture.gate load --threads 12 --processes 2  # 2.0.2, carga pesada
... -m voxvault.capture.gate discontinuity   # 2.0.3

# testes
.\.venv\Scripts\python.exe -m pytest tests/capture -m "not hardware"   # 51, sem hardware
.\.venv\Scripts\python.exe -m pytest tests/capture -m hardware         # exige endpoint
```

O portão gera o próprio áudio de teste (tom de 441 Hz a 2% de amplitude, via
`IAudioRenderClient`) no exato endpoint que está capturando, para não depender de alguém
tocar música. Em produção nada é reproduzido: a especificação proíbe, justamente porque a
trilha do sistema capturaria a própria reprodução.

---

## 12. Consequências para as tarefas seguintes

1. **A regra de lacuna precisa dos dois canais.** Salto de posição de dispositivo como
   primário; flag do sistema operacional como gatilho adicional que dispensa o limiar.
   Só a flag perderia lacunas de quase dois segundos (§6).
2. **Aquecer o motor de áudio ao subir o serviço.** Sem `prewarm`, o teto de 1500 ms da
   tarefa 2.7.1 não é atingível na primeira gravação (§8.1).
3. **Registrar a latência de cada fluxo nos metadados.** No loopback o carimbo está ~25 ms
   à frente da entrega; sem a âncora por trilha as duas ficam deslocadas entre si (§8.2).
4. **`arming_ms` é metadado, não condição de falha.** A especificação manda registrar a
   violação do teto, não abortar a gravação.
5. **Pacotes `SILENT` não carregam dados.** `CapturePacket.data` vem vazio e
   `CapturePacket.silent` marcado; expandir os zeros é trabalho do consumidor, fora da
   thread de captura. `CaptureStream.materialize()` faz isso.
6. **O portão está satisfeito.** As tarefas de alinhamento (2.3.x em diante) podem começar,
   e a aritmética de que elas dependem já está implementada e testada em
   `voxvault/capture/anchor.py`.

---

## 13. Arquivos

| Arquivo | Conteúdo |
|---|---|
| `src/voxvault/capture/wasapi.py` | Ligações COM em `ctypes`: `IMMDeviceEnumerator`, `IMMDevice`, `IMMDeviceCollection`, `IAudioClient`, `IAudioCaptureClient`, `IAudioRenderClient`, `IPropertyStore`, `IMMNotificationClient` (implementado em Python), estruturas, GUIDs, QPC e prioridade MMCSS |
| `src/voxvault/capture/format.py` | Análise pura de `WAVEFORMATEX` / `WAVEFORMATEXTENSIBLE` |
| `src/voxvault/capture/devices.py` | Enumeração, identificadores persistentes, padrão por papel, as duas políticas de dispositivo |
| `src/voxvault/capture/stream.py` | `CaptureStream`, `open_pair`, `prewarm` |
| `src/voxvault/capture/anchor.py` | `Anchor`, `decide_gap`, `TrackPlacer` — aritmética pura |
| `src/voxvault/capture/gate.py` | O portão executável, o gerador de tom e o gerador de carga |
| `tests/capture/` | 51 testes sem hardware + 12 marcados `hardware` |
