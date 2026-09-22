# VoxVault

Grava, transcreve e indexa suas reuniões — no seu computador, sem depender da
plataforma de reunião e sem mandar nada para fora.

Grava duas trilhas ao mesmo tempo: o seu microfone e a mistura que o Windows
está reproduzindo. É essa separação física que diz quem falou, sem modelo de
diarização e sem precisar que Teams, Meet ou Zoom cooperem. Nenhum bot entra na
chamada, nenhuma permissão é pedida à plataforma.

Durante a reunião a máquina **só grava**. A transcrição roda depois, e é
interrompida se você apertar gravar de novo.

## Começando

```bash
cd voxvault-core
uv venv --python 3.12
uv pip install -e ".[dev,engine,cuda]"
voxvault doctor
```

O `doctor` verifica cada pré-requisito separadamente e diz o que fazer em cada
um que falhar. Ele **abre** um dispositivo de áudio de verdade em vez de apenas
listar, porque no Windows "aparece na lista" e "abre" são coisas diferentes.

## Gravando

```bash
voxvault record --title "Alinhamento da sprint"
```

`Ctrl+C` encerra. O áudio é comprimido sem perdas, os metadados gravados, e a
reunião entra na fila de transcrição.

```bash
voxvault queue --run
```

Para gravar em segundo plano com o aplicativo ou qualquer outra superfície,
suba o serviço residente — ele é quem segura a captura e a fila:

```bash
voxvault serve
```

## Lendo depois

```bash
voxvault list
voxvault show <id>
voxvault search "janela de manutenção"
voxvault export <id>
```

A busca ignora acentuação nos dois sentidos: `manutencao` encontra
`manutenção`, e vice-versa.

Também dá para trazer material que já existe:

```bash
voxvault import "reuniao-gravada.mp4"
```

## Ligando ao Claude Desktop ou ao Codex

É aqui que a ferramenta passa a valer mais do que um arquivo de texto: um
agente lê suas reuniões e trabalha em cima delas.

```bash
voxvault mcp --apply
```

Preserva os outros servidores e copia o arquivo antes de mexer. Reinicie o
cliente depois.

O agente pode ler transcrições, recortar por intervalo de tempo, buscar em todo
o histórico e gravar notas de volta. **Não pode** apagar reunião, alterar
segmento, remover áudio nem disparar gravação — a transcrição é o registro do
que foi dito, e um agente capaz de editá-la a tornaria inútil como registro.

## Comparando modelos

```bash
voxvault benchmark "reuniao.wav" --models "large-v3,large-v3-turbo"
```

Cada configuração roda em processo próprio, para que a memória de GPU seja
liberada entre elas. O relatório alinha os textos por janela de tempo, não por
índice de segmento — modelos diferentes cortam o áudio de formas diferentes.

## Onde as coisas ficam

| | |
|---|---|
| Dados, áudio e banco | `D:\VoxVault` (configurável) |
| Configuração | `%APPDATA%\VoxVault\config.json` |
| Serviço em execução | `%APPDATA%\VoxVault\servico.json` |

Cada reunião vive num diretório autocontido, com o áudio, os metadados e as
exportações legível e estruturada. Copie a pasta para outra máquina e ela
continua descrevendo a si mesma.

```bash
voxvault config                      # o que vale agora, e de onde veio cada valor
voxvault config data_dir=E:\Reunioes
```

A configuração tem precedência explícita: argumento, depois variável de
ambiente, depois arquivo, depois padrão. O `doctor` mostra a origem de cada
valor efetivo, para que uma divergência entre processos seja diagnosticável em
vez de misteriosa.

## Desempenho

Medido nesta máquina, com RTX 4060 Ti:

| | |
|---|---|
| `voxvault --help` | 213 ms |
| Servidor MCP subindo | 1,0 s, sem carregar nenhum runtime de inferência |
| Transcrição (`large-v3`, float16) | 22x tempo real |
| Uma hora de reunião | menos de três minutos para transcrever |
| Aplicativo desktop em repouso | ~1,3% de CPU, ~430 MB |
| Disco por hora gravada | ~220 MB durante, ~120 MB depois de comprimida |

Nada pesado é carregado até ser necessário. Um comando que não transcreve nunca
paga o custo de importar o motor.

## Privacidade

Todo o processamento é local. Nenhum áudio e nenhuma transcrição sai da
máquina.

A detecção de início de reunião, quando ligada, lê apenas **qual processo detém
o microfone** — pelo mesmo registro que o Windows usa para mostrar o indicador
de microfone em uso. Não lê áudio, nem título de janela, nem endereço, nem
conteúdo de espécie alguma.

Gravar uma conversa de trabalho pode exigir avisar os participantes, conforme a
jurisdição e a política da sua empresa. A ferramenta não faz esse aviso por
você.

## Documentos

- [DESIGN.md](DESIGN.md) — por que o sistema é assim
- [docs/estado-da-implementacao.md](docs/estado-da-implementacao.md) — o que está pronto e o que foi verificado como
- [docs/avaliacao-fase-0.md](docs/avaliacao-fase-0.md) — a medição que decidiu por transcrição local
- [docs/portao-captura.md](docs/portao-captura.md) — por que o backend de captura é o que é
- `openspec/changes/` — as especificações que guiaram a implementação
