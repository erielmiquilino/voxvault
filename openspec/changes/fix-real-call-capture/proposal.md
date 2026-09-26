## Why

Numa reunião real no Teams, em 25/09, com um headset Bluetooth, as duas gravações saíram só com a voz do usuário. O Teams tocava a chamada pela saída Hands-Free do headset, e a trilha do sistema gravava a saída estéreo do mesmo headset, que é o padrão de comunicações do Windows e estava muda. Quando o fone caiu e voltou, as duas trilhas perderam o dispositivo, não se recuperaram em 30 segundos e foram encerradas. A gravação seguiu aberta sem captar nada, e o encerramento falhou no meio e a deixou "gravando". O serviço não registrou nada disso em disco. No mesmo dia, num PC corporativo, a captura foi recusada pelo antivírus, e o diagnóstico mandou reiniciar o serviço de áudio.

## What Changes

- A trilha do sistema passa a gravar a saída Hands-Free de um headset quando o microfone gravado é a entrada Hands-Free desse mesmo aparelho, e acompanha essa saída quando ela aparece ou some durante a gravação.
- A recuperação de uma trilha que perdeu o dispositivo deixa de desistir em 30 segundos. Esgotado o prazo, a trilha é registrada como incompleta e o usuário é avisado, mas as tentativas continuam enquanto a gravação durar. Cada tentativa de abertura tem tempo limitado e não atrasa a supervisão da outra trilha.
- Um bloco que não puder ser convertido ou posicionado passa a ser contabilizado como lacuna, e a trilha continua sendo escrita. A troca de dispositivo passa a acontecer na ordem dos pacotes: o que veio do dispositivo anterior é escrito no formato dele.
- O encerramento de uma gravação nunca deixa a reunião aberta. Se a finalização falhar, a reunião é fechada com o que está em disco e submetida para transcrição.
- O serviço passa a registrar em disco, com data e hora, os eventos da gravação, as mudanças de dispositivo, os avisos e as falhas com a causa, num arquivo de tamanho limitado.
- A trilha do sistema em silêncio digital por 120 segundos passa a gerar um aviso de captura, que nomeia a saída gravada.
- A tela de gravação deixa de mostrar a trilha do sistema como perdida quando nada está tocando: o estado de cada trilha vem do supervisor, e a saída gravada aparece em tom neutro quando está em silêncio.
- Quando a captura é recusada e a reprodução funciona, o diagnóstico e o erro de início passam a apontar um software de segurança bloqueando o VoxVault e a nomear o executável a liberar.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `audio-capture`: seleção da saída da trilha do sistema com a saída Hands-Free; recuperação de dispositivo sem desistência e sem bloqueio entre trilhas; blocos não escritos que não interrompem a trilha.
- `recording-session`: encerramento que sempre fecha a reunião; registro de eventos do serviço em disco.
- `environment-check`: diagnóstico da captura recusada por software de segurança.
- `tray-and-hotkeys`: aviso de captura para a trilha do sistema em silêncio digital.
- `recording-ui`: estado de cada trilha pelo estado do dispositivo, e não pelo silêncio da trilha do sistema.

## Impact

- `voxvault-core`:
  - `capture/devices.py`: identificador do aparelho, a saída Hands-Free de um microfone Hands-Free.
  - `capture/wasapi.py`: leitura de propriedade GUID.
  - `session/supervisor.py`: alvo da trilha do sistema, recuperação contínua, tentativas com tempo limitado.
  - `session/__init__.py`: troca de stream na thread de escrita, escrita que não morre, encerramento protegido.
  - `service/__init__.py`: início com a saída Hands-Free, encerramento que fecha a reunião, registro de eventos.
  - `doctor.py`: diagnóstico da captura recusada.
- `voxvault-app`: `avisos.rs`, com o aviso de silêncio da trilha do sistema; `service.rs`, com a rotação do log; `Gravacao.svelte` e `Medidor.svelte`, com o estado de cada trilha.
- Sem dependências novas.
