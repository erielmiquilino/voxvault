## MODIFIED Requirements

### Requirement: Diagnóstico de ambiente sob demanda

O sistema SHALL oferecer um comando de diagnóstico que inspeciona e reporta o estado de cada pré-requisito de execução: versão do interpretador Python, presença e modelo da GPU, disponibilidade de aceleração por GPU para inferência, presença das bibliotecas de álgebra linear e de redes neurais exigidas pelo runtime de inferência, presença do decodificador de mídia externo, e espaço livre no diretório de dados configurado.

Cada item verificado SHALL ser reportado individualmente com um de três estados: `ok`, `aviso` ou `falha`. O comando SHALL terminar com código de saída zero quando nenhum item estiver em `falha`, e diferente de zero caso contrário.

Nenhum item em estado `falha` ou `aviso` SHALL ser reportado sem uma mensagem que indique a ação corretiva.

A verificação das bibliotecas de inferência acelerada SHALL tentar o **carregamento real** das bibliotecas e uma inferência mínima, e MUST NOT se limitar a constatar que o pacote está instalado. Instalar o pacote e conseguir carregá-lo são coisas distintas no Windows, e a diferença entre as duas é exatamente o modo de falha mais comum deste stack.

Quando a abertura de um dispositivo de captura falhar e a abertura do dispositivo de saída padrão para reprodução funcionar, o diagnóstico SHALL informar que a captura está sendo recusada ao VoxVault, provavelmente por um software de segurança, e SHALL nomear os executáveis do VoxVault a liberar. A mesma explicação SHALL acompanhar o erro de início de gravação nessa situação.

A verificação SHALL ser executada no mesmo ambiente de processo que executará a transcrição — mesmo interpretador, mesmas variáveis de ambiente e mesmo diretório de trabalho —, de modo que a resolução de bibliotecas dinâmicas observada no diagnóstico seja a mesma da execução real.

#### Scenario: Ambiente completo e funcional

- **WHEN** o usuário executa o diagnóstico numa máquina com GPU compatível, runtime de inferência acelerado e decodificador de mídia instalados
- **THEN** todos os itens são reportados como `ok`
- **AND** o relatório inclui o modelo da GPU, a VRAM total e o espaço livre do diretório de dados
- **AND** o comando termina com código de saída zero

#### Scenario: Pacote instalado mas biblioteca não carregável

- **WHEN** os pacotes de inferência acelerada estão instalados mas o carregamento da biblioteca dinâmica falha
- **THEN** o item é reportado como `falha`
- **AND** a mensagem distingue explicitamente instalação de carregamento e nomeia a biblioteca que falhou

#### Scenario: Decodificador de mídia ausente

- **WHEN** o diagnóstico roda numa máquina sem o decodificador de mídia externo disponível no `PATH`
- **THEN** o item é reportado como `falha` com instrução de instalação
- **AND** o comando termina com código de saída diferente de zero

#### Scenario: Versão de Python incompatível

- **WHEN** o diagnóstico roda sob um interpretador fora da faixa suportada
- **THEN** o item da versão de Python é reportado como `falha`
- **AND** a mensagem informa a faixa de versões suportada e a versão encontrada

#### Scenario: Captura recusada por software de segurança

- **WHEN** o diagnóstico roda numa máquina em que a captura de áudio é recusada ao VoxVault e a reprodução funciona
- **THEN** o item de captura é reportado como `falha`
- **AND** a mensagem aponta um software de segurança bloqueando a captura e nomeia os executáveis a liberar
