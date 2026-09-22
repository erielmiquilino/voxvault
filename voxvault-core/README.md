# voxvault-core

Núcleo do VoxVault: captura, armazenamento, transcrição e recuperação de reuniões,
inteiramente local, no Windows.

Grava duas trilhas independentes — o microfone e a mistura que o Windows está
reproduzindo — e as alinha na mesma linha de tempo. A separação física das
trilhas é o que atribui as falas, sem modelo de diarização e sem depender de
permissão, bot ou plugin da plataforma de reunião.

## Ambiente

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
```

O motor de transcrição é um extra separado (`.[engine]`), para que a linha de
comando e o servidor MCP nunca paguem o custo de carregar o runtime de
inferência sem precisar dele.

## Diagnóstico

```bash
voxvault doctor
```

Relata cada dependência do ambiente com `ok`, `aviso` ou `falha`, e diz o que
fazer em cada caso.

## Estrutura

| módulo | responsabilidade |
|---|---|
| `config.py` | configuração compartilhada e cadeia de precedência |
| `types.py` | tipos de domínio, sem dependências de terceiros |
| `capture/` | acesso direto ao WASAPI: dispositivos, trilhas, alinhamento |
| `store/` | banco local, revisões, linha de tempo, busca e exportação |
| `engine/` | contrato de transcrição e implementações |
| `session/` | ciclo de vida de uma gravação |
| `pipeline/` | fila de transcrição |
| `service/` | serviço residente que detém captura e fila |
| `cli/` | superfície de linha de comando |
