# VoxVault — Projeto Técnico

Gravador e transcritor de reuniões local-first para Windows, independente da
plataforma de videoconferência (Teams, Meet, Zoom, Discord, qualquer uma).

**Princípio central:** gravar é barato, transcrever é caro. Durante a reunião a
ferramenta só grava. Toda a inteligência roda depois, com a máquina livre.
É exatamente o oposto do que o Perssua faz, e a razão pela qual ele trava.

---

## 1. Como funciona a captura

O Windows expõe, via WASAPI, um modo **loopback**: qualquer app pode ler o áudio
que o sistema está *tocando* naquele momento. Isso significa capturar as vozes
dos outros participantes direto do Windows — sem bot na reunião, sem cabo de
áudio virtual (VB-Cable), sem plugin do Teams, sem permissão da plataforma.

O VoxVault abre **duas trilhas simultâneas e separadas**:

| Trilha | Origem | Contém |
|---|---|---|
| `mic.wav` | dispositivo de entrada (papel de comunicações) | o que o microfone capta — na prática, a **sua** voz |
| `system.wav` | loopback WASAPI do dispositivo de saída | **toda** a mistura reproduzida nesse dispositivo — a reunião, e qualquer outro som tocando nele |

Essa separação é a jogada mais importante do projeto: ela entrega
"quem falou" de graça — sem `pyannote`, sem token de Hugging Face, sem VRAM
extra, sem o erro irregular que a diarização tem em reunião com ruído.
Cada trilha é transcrita isoladamente e as duas são fundidas por timestamp
numa timeline única.

A atribuição é por **origem física**, não por identidade. A trilha do sistema
é o que saiu pela sua caixa de som ou fone: se um vídeo estiver tocando em
outra aba, ele entra na gravação e será transcrito como se fosse fala de
participante. Por isso a reprodução de reuniões antigas fica bloqueada
enquanto há gravação ativa — senão a ferramenta se realimentaria.

---

## 2. Arquitetura

```
┌─────────────────────────────────────────────────────┐
│  Tauri (Rust + webview nativo)         ~8 MB de app │
│  UI: gravar/parar · lista · leitura · busca         │
└───────────────┬─────────────────────────────────────┘
                │ HTTP + WebSocket em 127.0.0.1
┌───────────────▼─────────────────────────────────────┐
│  voxvault-core  (Python 3.12, venv gerenciada)      │
│  ├── capture/   acesso direto ao WASAPI (loopback)  │
│  ├── engines/   TranscriptionEngine (interface)     │
│  │              └── FasterWhisperEngine (CUDA)      │
│  ├── store/     SQLite WAL + FTS5                   │
│  └── api/       FastAPI                             │
└───────────────┬─────────────────────────────────────┘
                │ lê o mesmo SQLite, somente leitura
┌───────────────▼─────────────────────────────────────┐
│  voxvault-mcp  (processo próprio, stdio)            │
│  Claude Desktop / Codex leem as transcrições        │
└─────────────────────────────────────────────────────┘
```

### Por que o MCP lê o SQLite direto, e não a API

Para que o Claude Desktop funcione **com o VoxVault fechado**. O MCP não deve
depender do app estar rodando. SQLite em modo WAL suporta múltiplos leitores
concorrentes com um escritor — é seguro.

### Por que Python não vai ser empacotado com PyInstaller

Empacotar PyTorch/CUDA com PyInstaller gera bundle de 3–5 GB, quebra com
frequência e dispara falso-positivo de antivírus. Como `uv` já está instalado
nesta máquina, o Tauri chama `uv run` numa venv que ele mesmo cria no primeiro
uso — o `uv` baixa o Python 3.12 e as dependências sozinho. O bundle do app
continua minúsculo e a atualização das libs fica trivial.

---

## 3. Camada de transcrição — desenhada para ser trocável

Você quer **medir** o large-v3 antes de decidir. Por isso o motor é uma
interface desde o primeiro commit, não um detalhe interno:

```python
class TranscriptionEngine(Protocol):
    def transcribe(self, wav_path: Path, *, language: str,
                   hints: str | None) -> list[Segment]: ...
```

Se o large-v3 local não atender, trocar por Deepgram, AssemblyAI, ElevenLabs
Scribe ou a API da OpenAI é escrever **uma classe nova**. Nada mais no sistema
muda. A decisão fica reversível de verdade, não no papel.

### Configuração do faster-whisper

`faster-whisper` roda o Whisper sobre CTranslate2 — bem mais rápido e com menos
memória que o `openai-whisper` de referência, com a mesma qualidade.

```python
WhisperModel("large-v3", device="cuda", compute_type="float16")

model.transcribe(
    wav,
    language="pt",                  # nunca deixar autodetectar por chunk
    vad_filter=True,                # OBRIGATÓRIO — ver riscos
    beam_size=5,
    condition_on_previous_text=False,
    initial_prompt=hints,           # nomes, siglas, jargão do time
    word_timestamps=True,
)
```

VRAM estimada: ~3,1 GB em `float16`. Cabem folgados nos 8 GB da 4060 Ti.

### Modelos a comparar na Fase 0

| Modelo | Decoder | Observação |
|---|---|---|
| `large-v3` | 32 camadas | Referência de qualidade. É o que você pediu para medir. |
| `large-v3-turbo` | 4 camadas | Bem mais rápido no decoder; perda pequena mas real. Vale medir em pt-BR. |

Estimativa a confirmar na bancada: large-v3 em torno de 8–15x tempo real na
4060 Ti (1h de reunião ≈ 4–8 min). Turbo, 2–3 min. **São estimativas — o
objetivo da Fase 0 é substituí-las por número medido.**

---

## 4. Layout em disco

`E:\` tem só 5,7 GB livres. Código fica em `E:\`, **dados vão para `D:\`**
(100,8 GB livres):

```
D:\VoxVault\
├── models\                      # HF_HOME — cache dos modelos (~3 GB)
├── recordings\
│   └── 2026-09-20T14-30_daily\
│       ├── mic.flac
│       ├── system.flac
│       ├── meta.json
│       ├── transcript.json      # canônico, com timestamps
│       └── transcript.md        # leitura humana
└── voxvault.db                  # SQLite (WAL)
```

Áudio é gravado já em **16 kHz mono 16-bit** (resample em memória com `soxr`) e
comprimido em FLAC ao final. Whisper só consome 16 kHz — nada se perde para a
transcrição. Custo: ~60 MB/hora por trilha, contra 1,4 GB/hora se guardasse
48 kHz estéreo float32. Flag de config para quem quiser o áudio em qualidade
de arquivo.

### Esquema do SQLite

```sql
meetings(id, title, started_at, ended_at, duration_s, status, folder, notes)
segments(id, meeting_id, track, speaker, start_ms, end_ms, text)
segments_fts  -- FTS5 sobre segments.text
```

A FTS5 é o que torna o MCP realmente útil: busca full-text em todo o histórico
de reuniões em milissegundos.

---

## 5. Servidor MCP

Entry point próprio, `stdio`, registrado no `claude_desktop_config.json`.

| Tool | Função |
|---|---|
| `list_meetings` | filtra por período, título, duração |
| `get_transcript` | timeline completa de uma reunião, `md` ou `json` |
| `search_transcripts` | busca FTS5 em todo o histórico, devolve trechos com contexto |
| `get_segments` | recorte por intervalo de tempo de uma reunião |

Com isso, "resuma a daily de ontem", "o que ficou decidido sobre o deploy?" ou
"levanta tudo que falaram de orçamento nas últimas 4 reuniões" passam a ser
trabalho do Claude, não código seu.

---

## 6. Riscos reais e como tratar

### 6.1 Dessincronização das trilhas (o bug nº 1 deste tipo de ferramenta)

O loopback WASAPI **não entrega buffers quando o dispositivo de saída fica
ocioso**. Se ninguém falar por 30s, a trilha do sistema simplesmente não recebe
dados e passa a ficar adiantada em relação ao microfone — as duas transcrições
deixam de bater na timeline.

**Tratamento:** posicionar cada pacote pela **posição de dispositivo** que o
WASAPI informa, ancorada no timestamp de alta resolução do próprio pacote, e
preencher os buracos com silêncio — em vez de concatenar buffers cegamente.

E aqui mora uma armadilha: usar o relógio no momento em que o buffer chega ao
app, ainda que somado à latência, não resolve. Esse valor carrega o atraso de
escalonamento, então sob carga ele **fabrica** lacunas que nunca existiram. Foi
por isso que o `PyAudioWPatch` ficou de fora: o backend WASAPI do PortAudio
descarta a posição de dispositivo e o timestamp do `GetBuffer` e calcula o
`inputBufferAdcTime` exatamente dessa forma enganosa.
Isso precisa estar no núcleo da captura desde o começo, não como correção.

### 6.2 Alucinação do Whisper no silêncio

Em pt-BR, o Whisper preenche trechos silenciosos com lixo aprendido de
legendas — o clássico "Legendas pela comunidade Amara.org".

**Tratamento:** `vad_filter=True` (Silero VAD) e `condition_on_previous_text=False`.
Não é opcional.

### 6.3 Eco quando você usa caixas de som

Sem fone, seu microfone capta a voz dos outros e o mesmo trecho aparece nas
duas trilhas, duplicado.

**Tratamento v1:** usar fone. Documentado, e a UI avisa se detectar que a saída
não é um dispositivo de fone. Cancelamento de eco (WebRTC AEC) fica para depois.

### 6.4 Troca de dispositivo no meio da reunião

Plugar o fone durante a call muda o dispositivo de saída e o handle do loopback
morre silenciosamente.

**Tratamento:** detectar o erro do stream, reabrir no dispositivo novo e
registrar o corte no `meta.json`.

### 6.5 cuBLAS e cuDNN

`faster-whisper` em GPU precisa de cuBLAS e cuDNN 9 (CUDA 12). Não vêm com o
driver. Instalar via pip (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`). É o ponto
onde esse stack mais costuma falhar no Windows — **validar na Fase 0, antes de
qualquer outra coisa.**

### 6.6 Python 3.14

A venv do núcleo tem **piso em 3.12 e nenhum teto**. O piso vem do `numpy`,
que não instala abaixo disso. Não há teto porque o `soxr` publica wheel de ABI
estável (`soxr-1.1.0-cp312-abi3-win_amd64.whl`, que serve de 3.12 para cima), o
`ctranslate2` cobre até 3.14, `faster-whisper` e `soundfile` são Python puro, e
a captura é `ctypes` sobre a API do Windows, sem extensão compilada.

Usamos 3.12 porque é o que o uv já tem instalado, não porque algo impeça mais.
O 3.14 do sistema segue não servindo para a venv — mas por gestão de ambiente,
não por incompatibilidade.

### 6.7 Consentimento

Gravar reunião de trabalho pode exigir aviso aos participantes conforme a
política da sua empresa. Vale conferir antes de usar em call de cliente.

---

## 7. Fases

### Fase 0 — Bancada de medição ⟵ *decide o resto do projeto*

Sem UI, sem app, sem arquitetura. Só:

1. venv 3.12 via uv, ffmpeg, faster-whisper, cuBLAS/cuDNN funcionando em CUDA.
2. Gravar 10–15 min de áudio real seu (reunião de verdade, com ruído e sotaque
   do time — não áudio limpo de teste).
3. Transcrever com `large-v3` e `large-v3-turbo`.
4. Relatório: tempo de processamento, VRAM, e leitura sua da qualidade.

**Portão de decisão:** se a qualidade não servir, o projeto segue igual —
troca-se só a implementação de `TranscriptionEngine` por um provedor online.
Nada do que vem depois é desperdiçado.

### Fase 1 — Núcleo de gravação (CLI)

Captura dual-track com correção de gap, SQLite, fusão das timelines,
`transcript.json` + `transcript.md`. Utilizável de verdade por linha de comando.

### Fase 2 — Servidor MCP

Ligar no Claude Desktop. **Aqui você já tem o valor todo do Perssua**, sem UI.

### Fase 3 — App Tauri

Botão gravar/parar, lista de reuniões, leitor de transcrição com áudio
sincronizado, busca.

### Fase 4 — Conforto

Ícone na bandeja, atalho global, notificações do sistema e detecção automática
de início de reunião.

Vínculo com a agenda e exportação para o Obsidian foram considerados e ficaram
de fora do escopo, por não terem sido selecionados entre as saídas do produto.
Ambos seguem viáveis como mudanças futuras sobre o que já existe.

---

## 8. Dependências

- **Python 3.12 (uv):** `faster-whisper`, `ctranslate2`, `soxr`, `numpy`,
  `soundfile`, `fastapi`, `uvicorn`, `mcp`, `pydantic`
- **Captura:** backend com acesso direto ao WASAPI, definido por portão de
  validação na Fase 1. `PyAudioWPatch` foi avaliado e **reprovado**: o backend
  WASAPI do PortAudio descarta posição de dispositivo e timestamp, e o alinhamento
  entre as trilhas depende deles.
- **NVIDIA (pip):** `nvidia-cublas-cu12`, `nvidia-cudnn-cu12`
- **Sistema:** ffmpeg
- **App:** Rust + Tauri 2 (Node 25 e npm 11 já instalados)
