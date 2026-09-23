## 1. Núcleo: exclusão no armazenamento

- [x] 1.1 Trocar a reivindicação da fila no pipeline por uma troca condicional `queued → running` que só prossegue se uma linha mudou (design, decisão 2); verificar com um teste em que a reunião some entre a escolha e a reivindicação e o pipeline segue para a próxima sem erro.
- [x] 1.2 Implementar no armazenamento a prévia de exclusão (título, início, duração, revisões, notas, arquivos com bytes, total) sem nenhuma escrita; verificar com um teste sobre uma reunião de fixture que compara a prévia com os arquivos e linhas reais e confirma que o banco não mudou.
- [x] 1.3 Implementar a transação de exclusão em `BEGIN IMMEDIATE`: re-checagem de gravação e de tentativa `running`, remoção de `segments_fts` por `rowid` dos segmentos da reunião, remoção explícita das notas e remoção da reunião; verificar com um teste que, após excluir uma reunião com duas revisões e três notas, conta zero linhas dela em `meetings`, `revisions`, `segments`, `notes`, `segments_fts` e `notes_fts`, e em que a busca não a retorna.
- [x] 1.4 Implementar a coreografia da lápide — pré-checagem, renomear para `.excluindo-<uid>`, transação, apagar a lápide — com retorno à pasta original quando a transação recusar; verificar com testes: renomeação falhando (arquivo aberto sem compartilhamento) não altera nada; recusa na transação restaura a pasta; sucesso não deixa a pasta nem a lápide.
- [x] 1.5 Cobrir as recusas e a fila: gravação em andamento e tentativa `running` recusam com causa; `queued` é excluída e nunca transcrita; verificar com testes, sendo o da fila feito com o pipeline real.
- [x] 1.6 Garantir que a exclusão de uma reunião importada não toca o original; verificar com um teste que importa um arquivo temporário, exclui a reunião e confere que o original existe com o mesmo conteúdo.
- [x] 1.7 Resolver lápides em `ResidentService.recover()` — restaurar quando a reunião consta, apagar quando não — e contar `exclusoes` no resumo; verificar com dois testes, um por ramo, simulando cada ponto de interrupção.
- [x] 1.8 Tratar várias reuniões num pedido de forma independente, com resultado por reunião; verificar com um teste de três reuniões em que uma está `running`: duas excluídas, uma intacta e recusada com motivo.

## 2. Núcleo: linha de comando e fronteira do MCP

- [x] 2.1 Adicionar `voxvault delete <uid>... [--yes] [--json]` com resolução de prefixo, prévia sem `--yes`, contrato JSON e códigos de saída `0`/`2`/`1` (design, decisão 4); verificar com testes de CLI para prévia sem efeito, exclusão confirmada, prefixo ambíguo, lote com recusa e o formato exato do JSON.
- [x] 2.2 Estender o teste que percorre as ferramentas do servidor MCP para falhar também diante de nomes de exclusão; verificar que o teste passa com o servidor atual e falharia com uma ferramenta de exclusão registrada de propósito num teste negativo.

## 3. Aplicativo

- [x] 3.1 Adicionar `cli::delete(uids, confirmar)` e os comandos Tauri `reunioes_excluir_previa` e `reunioes_excluir`, com o JSON do núcleo desserializado em tipos Rust; verificar com um teste Rust que desserializa uma saída real do comando, com um item excluído e um recusado.
- [x] 3.2 Tela da reunião: ação "Excluir reunião" com diálogo alimentado pela prévia, foco inicial em "Cancelar", botão "Excluir definitivamente", estados desabilitados com causa durante gravação e transcrição, liberação do reprodutor antes da chamada, nova tentativa única após 500 ms em arquivo em uso, e volta à Biblioteca ao concluir; verificar dirigindo a janela real pelo depurador do WebView2 contra um diretório de dados de teste: excluir com o áudio tocando conclui sem recusa, e a reunião some da lista.
- [x] 3.3 Biblioteca: modo de seleção com caixas de marcação, "Marcar todas as visíveis" respeitando os filtros, barra com quantidade, duração e espaço vindos da prévia, itens não selecionáveis com causa, confirmação única, lista de recusas por reunião, e `Esc`/"Cancelar" para sair; verificar pelo depurador do WebView2 com cinco reuniões de teste, uma delas marcada como `running` no banco de teste.
- [x] 3.4 Rodar `npm run check` (svelte-check) e `cargo test` no aplicativo; verificar que ambos passam sem avisos novos.

## 4. Verificação integrada e registro

- [x] 4.1 Ponta a ponta num diretório de dados descartável: importar três áudios, transcrever um, excluir um pela tela da reunião e dois em lote pela Biblioteca; verificar que as pastas deixam de existir, que a busca não os encontra, que os originais importados continuam no lugar e que a suíte completa do núcleo passa.
- [x] 4.2 Atualizar `docs/estado-da-implementacao.md` com a exclusão, o que foi verificado executando e como; verificar que o documento cita os testes e a verificação ponta a ponta.
