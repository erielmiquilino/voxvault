## Context

Projeto novo, repositório vazio até aqui. Ver `proposal.md — Why` para a motivação.

A máquina alvo, levantada antes do projeto, define boa parte das decisões abaixo:

| Recurso | Estado |
|---|---|
| GPU | NVIDIA RTX 4060 Ti, 8 GB VRAM, driver 595.71 |
| CPU / RAM | AMD Ryzen 5 3600 (6c/12t) / 16 GB |
| Python | 3.14.2 no sistema; 3.12.12 disponível via `uv` |
| ffmpeg | ausente |
| Disco | `E:` 5,7 GB livres (código) · `D:` 100,8 GB livres · `C:` 45,7 GB |

Duas restrições saem daí e não são negociáveis: o código vive em `E:` mas os dados não cabem lá, e o interpretador do sistema não serve.

## Goals / Non-Goals

**Goals:**

- Produzir uma resposta defensável para "a transcrição local serve?" sem construir nada do produto.
- Deixar o contrato `TranscriptionEngine` estável o bastante para que as fases seguintes sejam escritas contra ele, qualquer que seja a resposta acima.
- Tornar a falha de ambiente diagnosticável em um comando, em vez de um rastro de pilha de CUDA.

**Non-Goals:**

- Não calcular WER nem qualquer métrica automática de acurácia. A avaliação de qualidade nesta fase é leitura humana lado a lado — decisão registrada em `Decisions`.
- Não capturar áudio. A Fase 0 consome arquivos que já existem; a captura é a Fase 1.
- Não persistir nada em banco. Benchmark escreve arquivos soltos.
- Não otimizar. Medir primeiro; ajuste fino só se a medição justificar.

## Decisions

### Python 3.12, não o 3.14 do sistema

**Há piso, e não há teto.** O piso é 3.12, vindo do `numpy`, que não instala abaixo disso.

Esta decisão foi justificada erradamente duas vezes antes de ser medida direito, e vale registrar como, porque o erro é fácil de repetir. A primeira justificativa apontava o `PyAudioWPatch`, que depois foi reprovado no portão de captura. A segunda leu as tags dos wheels do `soxr`, viu `cp312` sem um `cp313` ao lado, e concluiu que o 3.13 era um buraco. O arquivo real é `soxr-1.1.0-cp312-abi3-win_amd64.whl`: a tag **`abi3`** significa ABI estável, e esse único wheel instala em 3.12, 3.13, 3.14 e adiante. Ler a tag pela metade inventou uma restrição que nunca existiu.

Verificado no índice de pacotes e no portão de captura da Fase 1:

| dependência | alcance real |
|---|---|
| `numpy` | exige 3.12 ou superior — é o piso |
| `soxr` | `cp312-abi3`, instala de 3.12 para cima |
| `ctranslate2` | wheels de 3.9 a 3.14 |
| `faster-whisper`, `soundfile` | Python puro |
| captura WASAPI | `ctypes` sobre a API do sistema, sem extensão compilada e sem ABI própria |

Portanto `requires-python = ">=3.12"`. O `uv` já tem 3.12.12 instalado nesta máquina e gerencia o interpretador sem tocar no Python do sistema, então 3.12 continua sendo o que o ambiente usa — mas por escolha, não por impedimento.

*Lição registrada:* uma tag de wheel só diz o que se lê dela inteira. `cp312-abi3` e `cp312-cp312` parecem quase iguais e significam coisas bem diferentes.

### faster-whisper (CTranslate2) como implementação local

Roda o Whisper sobre CTranslate2, com ganho expressivo de velocidade e menor consumo de memória que a implementação de referência, mantendo a mesma qualidade. Traz VAD integrado, que o requisito de supressão de alucinação exige.

*Alternativas consideradas:* a implementação de referência da OpenAI, rejeitada por consumo de VRAM e velocidade; `whisper.cpp`, forte em CPU mas com integração Python menos direta e sem vantagem aqui, já que existe GPU; WhisperX, que acrescenta alinhamento e diarização — desnecessário, porque a separação de falantes no VoxVault vem da separação física das trilhas, não de um modelo.

### Precisão `float16` na GPU

`large-v3` em `float16` ocupa em torno de 3,1 GB, folgado nos 8 GB disponíveis. `int8` economizaria memória que não falta, ao custo de qualidade — exatamente o que esta fase está medindo. Medir na precisão que o produto vai usar.

### Modelos comparados: `large-v3` e `large-v3-turbo`

O primeiro é a referência de qualidade e o que o usuário pediu para avaliar. O segundo reduz o decoder de 32 para 4 camadas, com ganho grande de velocidade e perda pequena mas real de qualidade — a comparação existe justamente para revelar se essa perda importa em pt-BR com áudio de reunião.

### Avaliação qualitativa, sem WER

Decisão do usuário. Calcular WER exigiria transcrever manualmente um trecho de referência, e o objetivo aqui é decidir se o resultado serve para uso próprio — pergunta que leitura direta responde.

*Consequência aceita e registrada:* quando um provedor online for avaliado no futuro, a comparação com o baseline local também será qualitativa, sem número objetivo. Se em algum momento for preciso comparar de forma rigorosa, será necessário produzir uma referência manual naquele momento.

### O contrato é um `Protocol`, não uma classe base

A fronteira precisa ser estrutural, não hereditária, para que um adaptador de provedor online não precise herdar de nada do VoxVault. Consumidores dependem do `Protocol`; a escolha da implementação vive em configuração.

### Dados em `D:\VoxVault\`, sempre por configuração

`E:` não comporta os modelos. O caminho é resolvido por configuração com padrão em `D:\VoxVault\`, e o cache de modelos é apontado para lá por variável de ambiente, nunca deixado no padrão do usuário em `C:`.

### Normalização de áudio via ffmpeg, com o original intocado

Qualquer entrada que o ffmpeg leia é aceita e convertida internamente para o formato que o motor exige. Isso já entrega, de graça, o requisito de importação de arquivos da Fase 1, e mantém os instantes dos segmentos relativos ao arquivo original.

### Matriz explícita em vez de duas regras concorrentes

"Pré-requisito ausente bloqueia a operação" e "aceleração indisponível degrada para CPU" são ambas defensáveis e se contradizem quando a GPU existe mas as bibliotecas não carregam. A matriz de disponibilidade resolve isso caso a caso, em vez de deixar a decisão para quem implementar.

A escolha de **recusar** a execução quando há GPU com bibliotecas quebradas é deliberada: cair para CPU nessa situação transformaria uma reunião de uma hora em horas de processamento e esconderia um defeito corrigível. Máquina sem GPU alguma é outra coisa — ali CPU é a única opção legítima, e o aviso basta.

### O diagnóstico carrega as bibliotecas de verdade, no ambiente real de execução

Constatar que o pacote está instalado não diz nada sobre a resolução de bibliotecas dinâmicas no Windows, que é onde este stack quebra. O diagnóstico executa uma inferência mínima, no mesmo interpretador e com as mesmas variáveis de ambiente que a transcrição usará — inclusive quando quem lança o processo é a aplicação de desktop, cujo ambiente pode diferir do terminal.

### Configuração compartilhada em local fixo por usuário

Vários processos resolvem a mesma configuração: serviço residente, comandos, servidores MCP e aplicação. Derivar o diretório de dados do diretório de trabalho faria o servidor MCP — lançado pelo cliente a partir de um diretório arbitrário — apontar para outro lugar, e o histórico se dividiria em dois sem nenhum sinal.

A precedência declarada (parâmetro, ambiente, arquivo, padrão) e o registro da origem de cada valor no diagnóstico tornam uma divergência entre processos diagnosticável em vez de misteriosa.

### Motor padrão provisório para não travar as fases seguintes

A avaliação de qualidade é uma decisão do usuário e não pode ser tomada por um agente. Mas as fases 1 a 3 não podem ficar bloqueadas esperando por ela. O padrão declarado — `large-v3` em `float16` — permite implementar tudo, e trocá-lo depois é alteração de configuração, não de código.

O registro da configuração usada em cada reunião garante que, se o padrão mudar, ainda se saiba o que produziu cada transcrição existente.

## Risks / Trade-offs

**Incompatibilidade entre cuBLAS/cuDNN e o runtime de inferência** → É a falha mais comum deste stack no Windows e costuma se manifestar como erro de carregamento de DLL sem explicação. Mitigação: o comando de diagnóstico é a primeira tarefa da implementação, antes de qualquer transcrição, e testa o carregamento real das bibliotecas em vez de apenas checar se o pacote está instalado.

**Áudio de benchmark não representativo** → Medir com áudio limpo produz uma resposta otimista que não se sustenta em reunião real. Mitigação: o requisito é usar gravação real, com ruído, sobreposição de fala e sotaques do time. O relatório registra a duração e a origem da entrada para que a conclusão seja lida junto com o que a produziu.

**8 GB de VRAM com outras aplicações abertas** → Navegador e cliente de reunião consomem VRAM e podem fazer o carregamento do modelo falhar de forma intermitente. Mitigação: erro explícito que nomeia memória exigida e disponível, e liberação obrigatória entre execuções do benchmark.

**A decisão desta fase pode invalidar a escolha do stack local** → Se a qualidade não servir, `faster-whisper` some do produto. Mitigação: é exatamente por isso que o contrato vem antes da implementação. O custo perdido nesse cenário se limita a uma classe.

**Avaliação qualitativa é subjetiva e não reproduzível** → Trade-off aceito conscientemente, com a consequência registrada acima.
