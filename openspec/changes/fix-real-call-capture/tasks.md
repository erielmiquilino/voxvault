## 1. Captura

- [x] 1.1 Leitura de propriedade GUID no `wasapi`, o identificador de aparelho (ContainerId) em `AudioEndpoint` e a saída de chamada de um microfone de headset em `devices`; verificar com testes que o microfone Hands-Free encontra a saída Hands-Free do mesmo aparelho, e não a estéreo, que um microfone que não é de headset não encontra saída nenhuma, e que um aparelho sem saída de chamada ativa também não.
- [x] 1.2 Alvo da trilha do sistema no supervisor — a saída de chamada do aparelho do microfone enquanto ativa, senão o padrão do papel — e início da gravação com esse alvo; verificar com testes que a trilha começa na saída Hands-Free, migra para ela quando ela fica ativa depois e volta ao padrão quando ela some, e que uma trilha fixada não migra.
- [x] 1.3 Recuperação contínua: tentativas em thread própria, trilha marcada como incompleta e usuário avisado aos 30 s, novas tentativas a cada 5 s até o fim; verificar com testes que um dispositivo que volta depois de 60 s devolve a trilha com uma única lacuna, e que uma abertura travada numa trilha não atrasa a detecção da outra.
- [x] 1.4 Troca de stream na thread de escrita e bloco não escrito que não encerra a trilha; verificar com testes que, numa troca de 16 kHz mono para 48 kHz estéreo com pacotes antigos pendentes, os antigos são escritos no formato antigo e a trilha segue no novo, e que um pacote que não pode ser convertido é contado nos metadados e a trilha continua.

## 2. Encerramento e registro

- [x] 2.1 `RecordingSession.stop` com os passos isolados, e o serviço finalizando pelo disco e fechando a reunião quando o encerramento falha; verificar com testes que uma falha num passo ainda produz metadados finalizados, a reunião fechada com a duração dos arquivos e a submissão para transcrição.
- [x] 2.2 Eventos do serviço e avisos da sessão no log com data e hora, sem título, e rotação do `servico.log` acima de 1 MB pelo app antes de iniciar o serviço; verificar com testes das linhas (formato, identificador, ausência de título) e da rotação.

## 3. Avisos e diagnóstico

- [x] 3.1 Nome da saída gravada na visão da gravação e aviso de captura da trilha do sistema em silêncio digital por 120 s, uma vez por episódio; verificar com testes do serviço e dos avisos do app.
- [x] 3.2 Captura recusada com reprodução funcionando: explicação e executáveis no diagnóstico e no erro de início; verificar com testes que simulam captura recusada e reprodução aberta, e o caso contrário, em que a mensagem continua a atual.

## 4. Verificação

- [x] 4.1 Suítes completas: testes e análise estática do núcleo, testes e clippy do aplicativo, verificação de tipos da interface.
- [x] 4.2 Ensaio instalado nesta máquina: o serviço registra os eventos com data e hora no `servico.log`, e a gravação que ficou aberta em 25/09 é finalizada, fechada e transcrita pela recuperação.
- [ ] 4.3 Reunião real no Teams com o headset Bluetooth, feita pelo usuário: a trilha do sistema tem a voz dos outros participantes, e uma queda do fone aparece no log com a volta da trilha.
- [ ] 4.4 No PC do trabalho, feito pelo usuário: o diagnóstico com a captura recusada nomeia os executáveis a liberar.

## 5. Documentação

- [x] 5.1 `docs/estado-da-implementacao.md` com o que aconteceu em 25/09 e o que mudou, e o README onde descreve a escolha de dispositivos e o log; verificar que dizem o mesmo que as specs.
