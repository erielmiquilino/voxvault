## Context

O preparo monta o ambiente em `%USERPROFILE%\.voxvault\runtime\ambiente`, e os pontos de entrada do núcleo ficam em `ambiente\Scripts`. Nada disso está no `PATH`. O requisito do ambiente isolado proíbe alterar o `PATH` para que o Python do ambiente nunca responda por `python` na máquina do usuário. A pasta `Scripts` tem 40 arquivos: os dois executáveis do VoxVault, um `python.exe`, um `pythonw.exe` e as ferramentas das dependências.

## Goals / Non-Goals

**Goals:**
- `voxvault` e `voxvault-mcp` encontrados pelo nome em qualquer terminal aberto depois do preparo, inclusive pelo shell de um agente.
- Nenhum outro executável do ambiente no `PATH`.
- A desinstalação desfaz exatamente o que foi feito.

**Non-Goals:**
- Mudar o `PATH` de outros usuários ou do sistema.
- Atualizar o ambiente de processos já abertos: um terminal aberto antes do preparo continua sem a pasta.

## Decisions

### 1. Uma pasta própria, com cópias dos dois executáveis

`%USERPROFILE%\.voxvault\bin` recebe cópias de `voxvault.exe` e `voxvault-mcp.exe`. Os lançadores que o uv gera guardam o caminho absoluto do interpretador do ambiente, então funcionam de qualquer pasta — conferido copiando o `voxvault.exe` para outra pasta e listando as reuniões por ele. A pasta fica dentro de `.voxvault`, que a desinstalação já remove.

*Alternativa descartada:* pôr `ambiente\Scripts` no `PATH`. Traria o `python.exe` do ambiente e trinta ferramentas das dependências para a frente de quem não tem outras, e para a disputa com as de quem tem.

*Alternativa descartada:* arquivos `.cmd` em vez de cópias. O shell do Git Bash, que é o de muitos agentes no Windows, não acha um `.cmd` pelo nome sem a extensão.

### 2. O `PATH` do usuário, no fim

A entrada vai para `HKCU\Environment\Path`: sem elevação, sem tocar em outros usuários. Vai no fim, então não passa na frente de nada. Uma entrada que já existe, com outra caixa ou com barra no fim, conta como existente.

### 3. Pela API do registro, sem expandir nem cortar

O valor é lido sem expandir variáveis (`RRF_NOEXPAND`) e gravado de volta com o tipo que tinha, para que um `%USERPROFILE%` do `PATH` continue sendo variável; é lido no tamanho que tiver. Uma leitura que falha por qualquer motivo que não seja a ausência do valor interrompe a integração: tomada por um `PATH` vazio, gravaria de volta um `PATH` só com a pasta. Depois de gravar, um `WM_SETTINGCHANGE` avisa o Explorer, que passa o `PATH` novo aos terminais que abrir.

*Alternativa descartada:* editar o `PATH` pelo NSIS. As strings do NSIS têm tamanho fixo, e um `PATH` maior que elas é gravado de volta cortado — um jeito conhecido de perder o `PATH` de alguém. Também descartado `[Environment]::SetEnvironmentVariable`, que devolve o valor expandido e o grava como texto simples.

### 4. Quando

Ao fim de todo preparo bem-sucedido, depois da verificação e do carimbo. Assim a instalação nova e cada atualização renovam as cópias, e uma instalação anterior a esta mudança ganha a pasta na primeira atualização. Uma falha aqui é registrada e não falha o preparo: o `PATH` é conveniência, o ambiente já está pronto. Uma cópia em uso — um `voxvault.exe` rodando da pasta — é renomeada antes de ser substituída.

### 5. A desinstalação, pelo próprio executável do aplicativo

O gancho de desinstalação executa `voxvault-app.exe --remover-do-path` depois da recusa por gravação em andamento e fora do modo de atualização. O aplicativo trata esse argumento antes de qualquer outra coisa — antes da instância única, que o entregaria a uma janela aberta — e sai.

## Risks / Trade-offs

- [Processos abertos antes do preparo não veem a pasta] → documentado: abrir um terminal novo, ou reiniciar o aplicativo que hospeda o agente.
- [Desinstalação cancelada depois do gancho] → o aplicativo fica sem a entrada até o próximo preparo; aceito, é raro e só custa o nome curto.
- [O usuário tira a entrada do `PATH` de propósito] → volta na próxima atualização, que refaz o preparo; aceito e documentado.
