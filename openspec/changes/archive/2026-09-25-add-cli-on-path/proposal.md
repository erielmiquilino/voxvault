## Why

Um agente de IA trabalhando no terminal — uma sessão do Claude Code do próprio usuário — não conseguia chegar às transcrições: o `voxvault.exe` fica dentro do ambiente preparado, fora do `PATH`, e a sessão acabou vasculhando o AppData e o banco da pasta de dados. O requisito do ambiente isolado proíbe alterar o `PATH`, com razão: o ambiente tem um Python próprio, que não pode passar a responder por `python` na máquina de ninguém. Mas a proibição inteira impede também o uso mais simples da linha de comando, chamá-la pelo nome — justamente o que um agente tenta primeiro.

## What Changes

- Ao fim de todo preparo bem-sucedido, o aplicativo mantém `%USERPROFILE%\.voxvault\bin` com cópias só dos executáveis do VoxVault — `voxvault.exe` e `voxvault-mcp.exe` — e acrescenta essa pasta ao fim do `PATH` do usuário, sem elevação.
- A pasta `Scripts` do ambiente continua fora do `PATH`: além dos dois executáveis, ela tem um `python.exe` e cerca de trinta ferramentas das dependências (`hf`, `httpx`, `uvicorn`, `tqdm`...).
- A desinstalação remove a entrada do `PATH`; uma atualização a mantém.
- README e notas permanentes da release dizem o que muda no `PATH` do usuário.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `app-distribution`: o ambiente isolado continua sem usar nem alterar o Python do sistema, mas a linha de comando passa a ser encontrada pelo nome; a desinstalação desfaz isso.

## Impact

- Aplicativo: `src-tauri/src/caminho.rs` (novo), `src-tauri/src/registro.rs` (novo, compartilhado com o esquema `voxvault:`), `preparo.rs`, `lib.rs`, `ativacao.rs`, `windows/ganchos.nsh`, e a feature `Win32_UI_WindowsAndMessaging` do `windows-sys`.
- Sistema do usuário: o valor `Path` de `HKCU\Environment`, só no fim e só com a pasta do VoxVault.
- Documentação: README, `.github/release-notes.md`, `docs/estado-da-implementacao.md`.
