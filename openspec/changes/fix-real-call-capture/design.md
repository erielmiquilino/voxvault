## Context

Cada trilha segue o dispositivo padrão do papel configurado, por omissão o de comunicações. Um headset Bluetooth aparece no Windows como três dispositivos com o mesmo identificador de aparelho (ContainerId):

- uma saída estéreo, com forma "fones de ouvido";
- uma saída Hands-Free, com forma "headset";
- um microfone Hands-Free, também com forma "headset".

Numa chamada, o app de reunião abre o microfone Hands-Free e toca na saída Hands-Free. O padrão de comunicações pode continuar sendo a saída estéreo. Foi o caso em 25/09, confirmado no registro do Windows.

Três detalhes do código atual pesam no desenho:

- **Supervisor:** verifica as trilhas a cada 2 s e recupera por até 30 s. A abertura de cada tentativa roda na thread dele, com a guarda de 120 s do `CaptureStream.start`.
- **Troca de dispositivo:** o supervisor reconfigura o gravador de trilha (`rebind`) antes de trocar o stream.
- **Thread de escrita de cada trilha:** termina na primeira exceção.

No encerramento, o serviço solta a sessão antes de finalizá-la, e os passos do `RecordingSession.stop` não são isolados. Os eventos do serviço ficam só em memória, e a saída padrão do serviço já vai, em modo de acréscimo, para o `servico.log`.

## Goals / Non-Goals

**Goals:**
- A chamada num headset Bluetooth é gravada na trilha do sistema desde o primeiro instante em que a saída Hands-Free está ativa.
- Uma trilha que perde o dispositivo volta a ser gravada quando houver um dispositivo utilizável, por mais tempo que isso leve.
- Nenhuma falha de um pacote, de uma troca ou de um encerramento deixa uma trilha ou uma reunião num estado que ninguém vê.
- A próxima falha é diagnosticável pelo log.

**Non-Goals:**
- Escolher a saída pelo nível de áudio de cada dispositivo ou pelo processo do app de reunião. Isso fica para quando aparecer um caso real que a saída Hands-Free não cubra.
- Misturar mais de uma saída na trilha do sistema.
- Mudar o comportamento diante de falha de E/S no disco.

## Decisions

### 1. A saída Hands-Free pelo aparelho, não pelo nome

Quando o microfone gravado tem forma "headset" ou "monofone", a trilha do sistema grava a saída ativa do mesmo aparelho (mesmo ContainerId) com forma "headset" ou "monofone". Se não houver, grava o padrão do papel, como hoje. O alvo é reavaliado a cada verificação do supervisor: a trilha migra para a saída Hands-Free quando ela fica ativa, no início da chamada, e volta ao padrão quando ela some. A regra vale só com a política de seguir o padrão.

*Alternativa descartada:* reconhecer a saída pelo nome ("Hands-Free"), que muda com o idioma e o fabricante.

*Alternativa descartada:* gravar as duas saídas do headset misturadas. Poria dois relógios numa trilha, que hoje tem um só, e mudaria o gravador de trilha inteiro.

*Alternativa descartada:* seguir a saída que estiver tocando. Erra com áudio simultâneo, como música nas caixas durante a chamada, e trocaria a trilha de lugar sozinha.

### 2. Recuperação contínua, com cada tentativa na sua própria thread

Esgotados 30 segundos sem dispositivo, a trilha é registrada como incompleta e o usuário é avisado uma vez. As tentativas continuam a cada 5 segundos enquanto a gravação durar. A trilha volta quando uma delas der certo, e o intervalo inteiro vira uma lacuna preenchida com silêncio.

Cada tentativa (resolver o dispositivo e abrir o stream) roda numa thread da própria trilha; o supervisor só consulta o resultado. Uma abertura travada não atrasa a detecção da outra trilha.

*Alternativa descartada:* um tempo limite curto na abertura. Um dispositivo lento mas bom, como o do Remote Desktop, que já levou mais de 30 s dentro do `Initialize`, nunca abriria.

### 3. A troca de dispositivo acontece na thread de escrita

`replace_stream` passa a deixar o stream novo pendente. A thread de escrita da trilha esvazia o stream antigo e escreve esses pacotes no formato antigo. Depois reconfigura o gravador para o formato novo e passa a ler o novo stream. O supervisor deixa de chamar `rebind`. Assim nenhum pacote é interpretado no formato de outro dispositivo, qualquer que seja o momento da troca.

### 4. Um pacote que não pode ser escrito não encerra a trilha

Uma exceção ao converter ou posicionar um pacote conta como bloco não escrito: é somada ao número e à duração desses blocos, que vão para os metadados, e a trilha continua. Uma falha de E/S mantém o comportamento atual.

### 5. O encerramento isola cada passo e sempre fecha a reunião

No `RecordingSession.stop`, cada passo antes dos metadados fica isolado: esvaziar os streams, igualar as trilhas, calcular as estatísticas e fechar os arquivos. Uma falha vira aviso. Os metadados e a finalização rodam sempre.

No serviço, se o `stop` lançar exceção mesmo assim, a sessão é finalizada pelo que está em disco, pela mesma rotina da recuperação de sessão interrompida. A linha no banco é fechada e a reunião vai para a fila. O erro vai para o log com a pilha.

### 6. O log é a saída padrão do serviço, com data e hora

Cada evento do serviço e cada aviso da sessão, como perda, recuperação, migração e encerramento como incompleta, vira uma linha com data e hora local na saída padrão, que o app já envia ao `servico.log`. Antes de iniciar o serviço, o app renomeia o log para `servico.log.1` quando ele passa de 1 MB. As linhas levam o identificador da reunião e nunca o título.

### 7. O aviso de silêncio da trilha do sistema vem do app

Segue o mesmo mecanismo do aviso de microfone mudo: o nível publicado pelo serviço, uma vez por episódio. O limiar é de 120 segundos, o da detecção de trilha silenciosa. O texto nomeia a saída gravada, que o serviço passa a publicar na visão da gravação, e é condicional ("se os outros estão falando"), porque silêncio na trilha do sistema pode ser legítimo.

### 8. A captura recusada pelo que a reprodução mostra

Depois de uma falha ao abrir uma captura, o diagnóstico e o início da gravação tentam abrir o padrão de saída para reprodução, sem tocar nada. Se a reprodução abre e a captura não, a mensagem aponta um software de segurança bloqueando a captura para o VoxVault e nomeia os executáveis a liberar: `sys._base_executable`, o processo que abre o áudio, e `sys.executable`.

## Risks / Trade-offs

- [A saída Hands-Free só fica ativa quando a chamada começa] → O alvo é reavaliado a cada 2 s. A trilha migra quando ela aparece, com a lacuna registrada, como numa troca de padrão.
- [Um headset USB com microfone "headset" e saída "fones de ouvido"] → Sem saída "headset" no aparelho, a regra não se aplica e vale o padrão, como hoje.
- [Aviso falso numa apresentação longa, com os outros calados] → O texto é condicional e o aviso sai uma vez por episódio.
- [Tentativas contínuas por uma reunião inteira] → Depois dos 30 s, uma enumeração e uma abertura a cada 5 s, em thread própria.
- [Não dá para verificar aqui com o headset e o Teams, porque a sessão remota esconde os dispositivos locais] → Cada regra tem teste com dispositivos falsos. A verificação com o aparelho real fica como tarefa do usuário na próxima reunião, e o log novo registra o que acontecer.
