## Purpose

Permitir transcrever gravações que já existem — um vídeo de reunião exportado pela plataforma, um áudio de entrevista, uma nota de voz — aproveitando o mesmo pipeline de transcrição, armazenamento e busca usado pelas gravações feitas pela própria ferramenta.

## ADDED Requirements

### Requirement: Importação de arquivo de mídia

O sistema SHALL aceitar a importação de qualquer arquivo de áudio ou vídeo que o decodificador de mídia consiga ler, criando a partir dele uma sessão equivalente a uma gravação.

A sessão criada por importação SHALL ser indistinguível de uma sessão gravada para fins de transcrição, busca, exportação e leitura, diferindo apenas nos metadados que registram sua origem.

O sistema SHALL registrar nos metadados o caminho do arquivo original e o instante da importação.

#### Scenario: Importação de um vídeo de reunião

- **WHEN** o usuário importa um arquivo de vídeo de uma reunião gravada pela plataforma
- **THEN** uma sessão é criada e submetida para transcrição
- **AND** os metadados registram o caminho do arquivo original e a origem como importação

#### Scenario: Importação com título informado

- **WHEN** o usuário importa um arquivo informando um título
- **THEN** a sessão recebe o título informado
- **AND** na ausência de título, o nome do arquivo de origem é usado

#### Scenario: Arquivo ilegível

- **WHEN** o usuário importa um arquivo que o decodificador não consegue ler
- **THEN** a importação falha nomeando o arquivo e o motivo
- **AND** nenhuma sessão é criada

### Requirement: Extração de áudio sem duplicar a mídia original

Ao importar, o sistema SHALL extrair a trilha de áudio para o formato usado pelo armazenamento e guardá-la no diretório da sessão.

O sistema MUST NOT copiar o arquivo original para o diretório de dados, e MUST NOT modificá-lo nem removê-lo.

Quando o arquivo original contiver mais de uma trilha de áudio, o sistema SHALL extrair a primeira e registrar nos metadados que havia outras.

#### Scenario: Importação de vídeo grande

- **WHEN** o usuário importa um arquivo de vídeo de vários gigabytes
- **THEN** apenas o áudio extraído e comprimido é gravado no diretório da sessão
- **AND** o arquivo de vídeo original permanece inalterado em seu local

#### Scenario: Arquivo original movido após a importação

- **WHEN** o usuário move ou apaga o arquivo original depois de importá-lo
- **THEN** a sessão continua íntegra e transcritível a partir do áudio extraído
- **AND** os metadados indicam que o caminho original não está mais acessível

### Requirement: Atribuição de falante em mídia importada

Uma sessão importada tem trilha única, e o sistema MUST NOT atribuir suas falas ao usuário nem aos demais participantes.

Os segmentos de uma sessão importada SHALL ser marcados com atribuição de falante desconhecida, de forma distinguível na leitura e na exportação.

#### Scenario: Leitura de transcrição importada

- **WHEN** o usuário lê a transcrição de uma sessão importada
- **THEN** os segmentos aparecem com atribuição desconhecida
- **AND** nenhum segmento é rotulado como sendo do usuário

### Requirement: Importação em lote

O sistema SHALL aceitar a importação de vários arquivos numa única operação, criando uma sessão por arquivo e enfileirando todas para transcrição.

A falha na importação de um arquivo MUST NOT interromper a importação dos demais.

Ao final, o sistema SHALL reportar quantos arquivos foram importados e quais falharam, com o motivo de cada falha.

#### Scenario: Importação de uma pasta com arquivos válidos e inválidos

- **WHEN** o usuário importa em lote uma pasta contendo arquivos de mídia e arquivos que não são mídia
- **THEN** uma sessão é criada para cada arquivo de mídia válido
- **AND** os arquivos inválidos são reportados individualmente com o motivo
- **AND** a operação termina com código de saída diferente de zero quando houve alguma falha
