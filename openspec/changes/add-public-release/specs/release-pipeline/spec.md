## Purpose

Garantir que toda mudança na `main` seja verificada no sistema operacional alvo, e que cada versão publicada seja construída a partir do código marcado, testada antes de ser publicada e baixável pelo público com integridade verificável.

## ADDED Requirements

### Requirement: Verificação a cada mudança

Cada envio à branch `main` e cada pedido de integração direcionado a ela SHALL disparar, em Windows, a verificação: testes do núcleo, exceto os que exigem dispositivo de áudio ou GPU; análise estática do núcleo; verificação de tipos da interface; e testes e análise estática do aplicativo.

O resultado SHALL ser publicado como um check chamado `testes`, exigido para integrar na `main`.

A `main` SHALL rejeitar reescrita de histórico e exclusão da branch.

Um envio novo na mesma branch SHALL cancelar a verificação ainda em andamento do envio anterior.

#### Scenario: Pedido de integração que quebra um teste

- **WHEN** um pedido de integração altera o código de modo que um teste do núcleo falha
- **THEN** o check `testes` falha
- **AND** a integração na `main` fica bloqueada até ele passar

#### Scenario: Reescrita de histórico da main

- **WHEN** alguém tenta reescrever o histórico da `main` com um envio forçado
- **THEN** o envio é rejeitado

### Requirement: Publicação por marcação de versão

Enviar uma marcação `vX.Y.Z` SHALL produzir uma versão pública chamada "VoxVault X.Y.Z", contendo o instalador `VoxVault-X.Y.Z-setup.exe` e um arquivo `SHA256SUMS.txt` com a soma SHA-256 de cada arquivo publicado.

As notas da versão SHALL trazer o texto permanente mantido no repositório — qual arquivo baixar, o aviso de executável não assinado, o que é baixado no primeiro uso e quanto, requisitos de hardware, e o que o aplicativo acessa na máquina — seguido da lista de mudanças gerada a partir do histórico.

O instalador publicado SHALL ser construído exclusivamente a partir do código da marcação, sem nenhum artefato produzido fora do processo de publicação.

#### Scenario: Publicação da versão 0.1.0

- **WHEN** a marcação `v0.1.0` é enviada com todas as versões declaradas em 0.1.0 e a verificação passando
- **THEN** a versão "VoxVault 0.1.0" é publicada com `VoxVault-0.1.0-setup.exe` e `SHA256SUMS.txt`
- **AND** a soma publicada confere com o instalador baixado

### Requirement: Versão única conferida

A publicação SHALL falhar antes de qualquer compilação quando a versão da marcação divergir da versão declarada por qualquer componente — configuração do aplicativo, pacote Rust, pacote da interface, pacote do núcleo e módulo do núcleo —, nomeando o componente divergente.

#### Scenario: Marcação que não bate com o aplicativo

- **WHEN** a marcação `v0.2.0` é enviada com a configuração do aplicativo ainda em 0.1.0
- **THEN** a publicação falha antes de compilar
- **AND** a mensagem nomeia a configuração do aplicativo como divergente
- **AND** nenhuma versão é publicada

### Requirement: Nada publicado sem testes

A publicação SHALL executar a mesma verificação exigida a cada mudança antes de construir o instalador, e MUST NOT publicar nada quando qualquer parte dela falhar.

#### Scenario: Teste falhando no momento da publicação

- **WHEN** uma marcação é enviada sobre um código com um teste falhando
- **THEN** a publicação para antes de construir o instalador
- **AND** nenhuma versão é publicada

### Requirement: Ensaio sem publicar

Um disparo manual do processo de publicação SHALL construir o instalador e o arquivo de somas e guardá-los como artefatos da execução, sem criar marcação nem versão pública.

#### Scenario: Ensaio antes da primeira publicação

- **WHEN** o processo de publicação é disparado manualmente na `main`
- **THEN** o instalador e as somas ficam disponíveis como artefatos da execução
- **AND** nenhuma versão pública é criada

### Requirement: Componentes de terceiros fixados e verificados

Todo componente de terceiros embutido no instalador SHALL ter versão fixada no repositório e soma SHA-256 conferida durante a construção, e uma soma divergente SHALL interromper a construção.

As dependências do núcleo instaladas no primeiro uso SHALL vir de um arquivo de travamento versionado, instalado sem resolução nova de versões.

#### Scenario: Componente embutido adulterado

- **WHEN** o arquivo baixado de um componente embutido não confere com a soma fixada
- **THEN** a construção é interrompida nomeando o componente
- **AND** nenhum instalador é produzido
