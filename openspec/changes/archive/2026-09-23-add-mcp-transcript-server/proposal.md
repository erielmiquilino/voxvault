## Why

Transcrição bruta não é o produto; o que o usuário quer é trabalhar em cima do que foi dito — resumir a reunião de ontem, recuperar o que ficou decidido sobre um assunto, levantar pendências espalhadas por várias conversas. Construir isso dentro da ferramenta significaria embutir um gerador de resumo inferior ao que o usuário já tem à mão.

A decisão é a inversa: expor as transcrições por MCP e deixar que Claude Desktop, Codex ou qualquer cliente compatível façam esse trabalho. Isso remove um módulo inteiro do produto e entrega mais capacidade do que ele teria.

Ao terminar esta fase o VoxVault já entrega todo o valor prático que motivou o projeto, sem uma linha de interface gráfica.

## What Changes

- Adiciona um servidor MCP que expõe o histórico de reuniões para clientes compatíveis, operando de forma independente da aplicação principal — inclusive com ela fechada.
- Expõe leitura de reuniões, de linhas de tempo completas, de recortes por intervalo e busca textual sobre todo o histórico, com paginação e limites de tamanho que impedem uma reunião longa de consumir o contexto do cliente inteiro.
- Adiciona notas de reunião como entidade de primeira classe: resumos, decisões e pendências que um agente grava de volta no VoxVault, passando a fazer parte do histórico pesquisável em vez de se perder na conversa onde foram produzidos.
- Restringe a superfície de escrita a notas. O servidor não renomeia, não apaga, não dispara reprocessamento e não altera segmentos nem áudio.

## Capabilities

### New Capabilities

- `meeting-notes`: notas associadas a uma reunião — resumo, decisões, pendências ou texto livre — com autoria registrada, pesquisáveis junto com as transcrições e sem qualquer efeito sobre a transcrição original.
- `mcp-server`: superfície MCP de leitura do histórico e de escrita de notas, com paginação, limites de tamanho e fronteira de escrita explícita.

### Modified Capabilities

Nenhuma. A leitura se apoia em `transcript-store` pelos requisitos já estabelecidos, e as notas são uma entidade nova ao lado dos segmentos, sem alterar o comportamento existente do armazenamento.

## Impact

- **Depende de**: `add-meeting-recording-core` aplicada. Sem histórico gravado não há o que expor.
- **Código novo**: ponto de entrada próprio `voxvault-mcp`, separado da aplicação principal, e o módulo de notas dentro de `voxvault-core`.
- **Dependências novas**: biblioteca de servidor MCP para Python.
- **Dados**: acrescenta a tabela de notas e estende o índice de busca para cobri-las. Migração de esquema, sem perda.
- **Configuração externa**: exige registrar o servidor na configuração do cliente MCP do usuário. O comando de diagnóstico passa a reportar se esse registro está presente e correto.
- **Segurança**: o servidor lê e escreve dados locais em nome do usuário, sob o mesmo diretório de dados. Não abre porta de rede e não expõe nada fora da máquina.
