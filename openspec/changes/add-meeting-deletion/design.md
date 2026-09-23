## Context

Motivação em `proposal.md`. O que o código atual determina para a abordagem:

- **Armazenamento.** `meetings` é a raiz. `revisions`, `segments` e `notes` referenciam `meetings(id)` com `ON DELETE CASCADE`, e `PRAGMA foreign_keys = ON` é ligado em toda conexão (`store/connection.py`). O índice de busca dos segmentos, `segments_fts`, é uma tabela virtual FTS5 **sem** chave estrangeira: mantida pelo código ao trocar a revisão ativa (`store.py`). O índice das notas, `notes_fts`, é mantido por gatilho (`notes_fts_follows_delete`).
- **Arquivos.** Tudo o que pertence a uma reunião vive em `recordings/<uid>` (`layout.meeting_dir`): trilhas FLAC, `metadados.json`, `transcricao.json`/`.md` e, numa reunião importada, `importado.<ext>`. `Config.exports_dir` existe mas nada escreve nele.
- **Fila.** O pipeline escolhe a próxima reunião com `attempt_state = queued` e só depois a marca `running` (`pipeline/__init__.py`, `_next_queued` e `set_attempt_state`), em duas operações separadas.
- **Padrão existente de ação destrutiva.** `voxvault remove-audio <uid>` mostra a prévia sem `--yes` e só remove com ele; recusa com a reunião em gravação. O aplicativo chama o núcleo por linha de comando para isso (`cli::remove_audio(uid, confirmar)`) e lê a Biblioteca direto do SQLite em modo leitura (`library.rs`).
- **Reprodutor.** A tela da reunião toca o FLAC pelo protocolo de assets do WebView2, que mantém o arquivo aberto enquanto o elemento de áudio existe.

## Goals / Non-Goals

**Goals:**

- Uma única implementação de exclusão, no núcleo, usada pela linha de comando e pelo aplicativo.
- Nenhum estado intermediário visível: reunião listada sem arquivos, ou arquivos sem reunião que sobrevivam à inicialização seguinte.
- Corrida com o pipeline resolvida pelo banco, sem trava própria.

**Non-Goals:**

- Lixeira, restauração ou desfazer — a decisão foi exclusão definitiva.
- Exclusão pelo servidor MCP — proibida pela fronteira de escrita existente.
- Cancelar uma transcrição em andamento para permitir a exclusão; a exclusão é recusada até ela terminar.
- Apagar o arquivo original de uma mídia importada, em qualquer circunstância.

## Decisions

### 1. Coreografia: renomear para uma lápide, excluir no banco, apagar a lápide

A exclusão de uma reunião segue, nesta ordem:

1. **Pré-checagem** no banco: a reunião existe, não está em gravação e não tem tentativa `running`. Senão, recusa.
2. **Renomear** `recordings/<uid>` para `recordings/.excluindo-<uid>`. É atômico no mesmo volume e falha se qualquer arquivo estiver aberto sem compartilhamento de exclusão — e é exatamente essa falha que precisa acontecer **antes** de tocar no banco. Se falhar, recusa com "arquivo em uso", nada mudou.
3. **Transação** `BEGIN IMMEDIATE`, que re-checa o estado: remove as linhas de `segments_fts` por `rowid IN (SELECT id FROM segments WHERE meeting_id = ?)` — pelo índice de `segments`, sem varrer o FTS inteiro —, remove as notas explicitamente, para o gatilho de `notes_fts` disparar sem depender de o SQLite propagar gatilhos em cascata, e por fim remove a linha de `meetings`, que leva `revisions` e `segments` pela cascata. Se a re-checagem recusar, o diretório é renomeado de volta e a recusa é informada.
4. **Apagar** `.excluindo-<uid>` recursivamente. Uma falha aqui não desfaz nada: a reunião já não existe, e a lápide é resolvida pela recuperação (decisão 3).

*Alternativas descartadas:* apagar o banco primeiro deixaria arquivos órfãos a cada falha de remoção; apagar os arquivos primeiro deixaria uma reunião listada sem áudio a cada falha de banco. A lápide faz da única etapa que falha de verdade — arquivo em uso — a primeira, e sem efeito.

### 2. A corrida com o pipeline é decidida pelo SQLite

O pipeline passa a reivindicar a reunião com uma troca condicional — `UPDATE meetings SET attempt_state = 'running' WHERE uid = ? AND attempt_state = 'queued'` — e só prossegue se uma linha foi alterada; senão segue para a próxima. Com a transação da exclusão também em `BEGIN IMMEDIATE`, as duas escritas se serializam no bloqueio de escrita do banco:

- se o pipeline reivindica primeiro, a exclusão vê `running` e recusa;
- se a exclusão confirma primeiro, a troca do pipeline não encontra a linha e a reunião nunca é transcrita.

*Alternativa descartada:* uma trava de arquivo compartilhada entre serviço e linha de comando. Seria uma segunda fonte de verdade sobre um estado que o banco já serializa.

### 3. Recuperação das lápides na inicialização do serviço

`ResidentService.recover()`, que já roda antes de servir qualquer cliente, passa a varrer `recordings/.excluindo-*`:

- a reunião do `<uid>` ainda consta do banco → a exclusão parou entre as etapas 2 e 3 → renomeia de volta para `recordings/<uid>`;
- não consta → parou na etapa 4 → apaga a lápide.

O resumo da recuperação ganha a contagem `exclusoes` (restauradas e concluídas).

### 4. Um comando, várias reuniões, contrato JSON

`voxvault delete <uid>... [--yes] [--json]`:

- aceita prefixo de `uid`, resolvido pelo mesmo `_resolve_uid` dos outros comandos; um prefixo ambíguo é recusado para aquele item;
- sem `--yes`, imprime a prévia e a instrução de confirmação, sem alterar nada;
- com `--json`, imprime um objeto `{"itens": [...], "total": {...}}`, em que cada item traz `uid`, `titulo`, `inicio`, `duracao_ms`, `revisoes`, `notas`, `arquivos` (`caminho`, `bytes`), `bytes` e, depois de executar, `resultado`: `"excluida"` ou `"recusada"`, com `motivo`;
- processa cada `uid` de forma independente; o código de saída é `0` se todos foram excluídos, `2` se algum foi recusado e `1` para erro de uso.

O aplicativo ganha `cli::delete(uids, confirmar)`, simétrico a `cli::remove_audio`, e dois comandos Tauri: `reunioes_excluir_previa(uids)` e `reunioes_excluir(uids)`. A tela da reunião usa os mesmos comandos com um único `uid`. Há um só caminho, para uma ou várias.

*Alternativa descartada:* uma rota nova no serviço. A linha de comando já é o caminho das ações destrutivas do aplicativo, funciona com o serviço parado, e o banco resolve a concorrência (decisão 2).

### 5. Interface

- **Tela da reunião.** "Excluir reunião" no grupo de ações, em estilo de perigo, abre um diálogo alimentado pela prévia, com os campos que a spec exige e o botão "Excluir definitivamente". O foco inicial fica em "Cancelar". Com a reunião gravando ou transcrevendo, a ação fica desabilitada com a causa, pelo mesmo mecanismo das outras ações. Ao confirmar, o elemento de áudio é pausado, tem `src` removido e é desmontado **antes** da chamada, liberando o arquivo que o WebView2 mantém aberto. Se a renomeação ainda assim falhar por arquivo em uso, a exclusão é tentada mais uma vez após 500 ms e, falhando de novo, a causa é apresentada.
- **Biblioteca.** Um botão "Selecionar" liga o modo de seleção: caixas de marcação nos itens, "Marcar todas as visíveis", que respeita os filtros correntes, e uma barra inferior com "N selecionadas · duração · espaço · Excluir". Itens gravando ou transcrevendo ficam desabilitados com a causa no título do item. `Esc` ou "Cancelar" saem do modo. O resultado de um lote com recusas vira uma lista nomeando cada reunião recusada e o motivo.
- **Somas.** Duração e espaço vêm da prévia do núcleo, e não de uma soma feita pela interface, para que o número confirmado seja o mesmo que o núcleo vai apagar.

### 6. Fronteira do MCP

Nada é exposto. O teste que percorre as ferramentas do servidor MCP e falha diante de qualquer operação destrutiva passa a incluir também os termos de exclusão (`excluir`, `delete`, `remover_reuniao`) na lista do que não pode aparecer.

## Risks / Trade-offs

- [O WebView2 pode segurar o FLAC mesmo depois de o elemento de áudio ser desmontado] → nova tentativa após 500 ms; persistindo, recusa com a causa. Nada é removido pela metade, porque a renomeação é a primeira etapa.
- [Antivírus ou indexador do Windows com o arquivo aberto no momento da exclusão] → mesma recusa limpa, com "tente novamente em instantes".
- [Um lote grande leva segundos em disco lento] → a interface mostra progresso por item; como cada exclusão é independente, interromper no meio deixa só reuniões inteiras ou excluídas.
- [Exclusão definitiva é irreversível por engano] → prévia com espaço e conteúdo, foco inicial em "Cancelar", botão que nomeia a ação, e nenhum atalho de teclado que exclua.

## Migration Plan

Sem migração de esquema: as tabelas e cascatas atuais já suportam a exclusão, e a troca condicional do pipeline não muda dados. Uma versão anterior que abra um diretório de dados depois de uma exclusão encontra só reuniões inteiras. A única sobra possível, uma lápide `.excluindo-*`, é ignorada por quem não a conhece, porque não é um `uid` válido.

Arquivamento: esta mudança altera `transcript-store`, de `add-meeting-recording-core`, e `library-ui`, de `add-desktop-app`. As duas precisam estar arquivadas antes desta, ou o arquivamento recusa o `MODIFIED`.
