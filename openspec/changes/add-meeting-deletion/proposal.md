## Why

Não existe hoje forma de apagar uma reunião: só de remover o áudio, preservando a transcrição. Gravações de teste, reuniões gravadas por engano e conversas que não deveriam ser guardadas ficam para sempre na Biblioteca e no disco, e o único caminho é apagar pastas e linhas do banco à mão — o que deixa o índice de busca apontando para o que não existe mais.

## What Changes

- Excluir uma reunião de forma **definitiva**, pela tela da reunião e pela linha de comando: áudio de todas as trilhas, todas as revisões de transcrição, notas, exportações, entradas no índice de busca e a pasta da reunião.
- Excluir **várias reuniões de uma vez**, selecionando-as na Biblioteca, com uma única confirmação que soma o que será perdido.
- Confirmação explícita que lista o que será perdido e o espaço em disco que será liberado, a partir de uma prévia que não altera nada.
- Exclusão recusada, com a causa em linguagem natural, enquanto a reunião está sendo gravada ou transcrita. Uma reunião apenas aguardando na fila pode ser excluída e deixa a fila.
- Exclusão à prova de interrupção: uma queda no meio nunca deixa reunião listada sem arquivos, nem arquivos órfãos que a inicialização seguinte não resolva.
- Numa reunião importada, só a cópia do VoxVault é removida; o arquivo original do usuário nunca é tocado.
- O servidor MCP continua sem qualquer operação de exclusão, como a fronteira de escrita já exige.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `transcript-store`: novo requisito de exclusão definitiva de reunião, com prévia, recusas por estado, atomicidade e recuperação após interrupção.
- `library-ui`: "Ações sobre uma reunião" passa a incluir excluir; novo requisito de exclusão de várias reuniões pela Biblioteca.

## Impact

- **Núcleo:** armazenamento (exclusão transacional e limpeza explícita do índice de busca), sistema de arquivos da pasta da reunião, recuperação na inicialização do serviço, novo comando `voxvault delete`.
- **Aplicativo:** comandos de prévia e de exclusão, ação "Excluir" na tela da reunião, modo de seleção na Biblioteca, liberação do reprodutor de áudio antes de excluir.
- **Servidor MCP:** nenhuma mudança; o teste que proíbe ferramentas destrutivas passa a cobrir também os nomes de exclusão.
- **Esquema de dados:** inalterado — nenhuma migração.
- **Arquivamento:** os deltas desta mudança alteram capacidades criadas por `add-meeting-recording-core` e `add-desktop-app`, que precisam ser arquivadas antes desta.
