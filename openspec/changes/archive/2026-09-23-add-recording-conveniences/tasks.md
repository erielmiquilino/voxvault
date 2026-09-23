## 1. Bandeja do sistema

- [ ] 1.1 Implementar o ícone de bandeja presente enquanto o aplicativo executa, e verificar que ele aparece ao abrir e desaparece ao encerrar
- [ ] 1.2 Implementar a variação visual do ícone entre ocioso, gravando, pausado e falha, e verificar percorrendo os quatro estados
- [ ] 1.3 Implementar o texto de apresentação do ícone com o estado e, durante gravação, o tempo decorrido; verificar durante uma gravação real
- [ ] 1.4 Implementar o menu da bandeja com iniciar e encerrar gravação, pausar e retomar, abrir a janela principal, abrir a última reunião e encerrar o aplicativo, habilitando apenas o que é válido para o estado; verificar cada ação
- [ ] 1.5 Verificar que iniciar a gravação pelo menu da bandeja não abre a janela principal e que o ícone passa a refletir o estado de gravação
- [ ] 1.6 Implementar o encerramento do aplicativo pela bandeja reutilizando a confirmação e o encerramento limpo já exigidos ao fechar a janela, e verificar com uma gravação ativa

## 2. Atalho global

- [ ] 2.1 Implementar o registro do atalho global que alterna entre iniciar e encerrar gravação conforme o estado, e verificar acionando com outro aplicativo em primeiro plano
- [ ] 2.2 Implementar a configuração do atalho pelo usuário com persistência, e verificar reabrindo o aplicativo após alterá-lo
- [ ] 2.3 Implementar a detecção e o relato de falha de registro por conflito com outro aplicativo, sinalizando o conflito na configuração até ser resolvido; verificar configurando um atalho já em uso
- [ ] 2.4 Implementar a confirmação perceptível a cada acionamento do atalho, e verificar acionando com a plataforma de reunião em tela cheia
- [ ] 2.5 Verificar que acionar o atalho durante uma gravação a encerra e a submete para transcrição

## 3. Fechamento e inicialização

- [ ] 3.1 Implementar a configuração entre encerrar o aplicativo e recolher para a bandeja ao fechar a janela, e verificar nos dois modos
- [ ] 3.2 Verificar que, com o recolhimento configurado, fechar a janela durante uma gravação não interrompe a gravação nem pede confirmação
- [ ] 3.3 Verificar que o encerramento real permanece disponível como ação distinta e mantém a confirmação de gravação ativa
- [ ] 3.4 Implementar a opção de iniciar junto com o sistema, desativada por padrão, subindo recolhido na bandeja; verificar reiniciando a máquina com a opção ativada
- [ ] 3.5 Verificar que o início junto com o sistema não inicia nenhuma gravação por si só

## 4. Notificações do sistema

- [ ] 4.1 Implementar as notificações de gravação iniciada, gravação encerrada, transcrição concluída, transcrição falha e avisos de captura, e verificar cada categoria
- [ ] 4.2 Implementar a abertura da reunião correspondente ao acionar a notificação de transcrição concluída, e verificar com o aplicativo recolhido na bandeja
- [ ] 4.3 Implementar a configuração de ativação por categoria e verificar que desativar uma categoria não afeta as demais
- [ ] 4.4 Implementar a supressão de notificações repetidas para o mesmo evento, e verificar por teste com o evento emitido duas vezes

## 5. Detecção de reunião

- [ ] 5.1 Implementar a observação de sessões de captura de áudio ativas por outros processos, por consulta periódica de baixa frequência; verificar por teste que uma captura ativa de outro aplicativo é percebida
- [ ] 5.2 Implementar o reconhecimento de aplicativos de plataformas de videoconferência entre os processos em execução, com a lista inicial documentada; verificar por teste com um processo simulado
- [ ] 5.3 Implementar a **correlação ao mesmo processo** — o processo que detém a captura precisa ser ele próprio um aplicativo reconhecido —, e verificar por testes que um aplicativo de reunião aberto sem captura não dispara, e que um gravador independente capturando com um cliente de reunião ocioso também não dispara
- [ ] 5.3.1 Implementar o tratamento de navegador como sinal fraco, sem inferir plataforma a partir dele e sem inspecionar títulos de janela ou endereços; verificar por teste com o navegador detendo a captura
- [ ] 5.4 Implementar os períodos mínimos de sustentação com padrões de 20 segundos para aplicativo reconhecido, 60 segundos para navegador e 60 segundos para cessação, todos configuráveis; verificar por testes de cada um dentro e fora do período
- [ ] 5.5 Verificar, por revisão do código do detector e por teste, que nenhum conteúdo de áudio, tela ou comunicação é lido
- [ ] 5.6 Implementar a detecção de fim de reunião pela cessação sustentada dos sinais, e verificar por teste
- [ ] 5.7 Implementar o encerramento automático da gravação ao detectar o fim apenas quando ela foi iniciada por detecção, e verificar por teste nos dois casos de origem
- [ ] 5.8 Implementar a notificação, sem encerramento, quando a reunião aparenta ter terminado e a gravação foi iniciada manualmente; verificar por teste

## 6. Ação ao detectar e controles

- [ ] 6.1 Implementar a configuração da ação entre não fazer nada, sugerir e iniciar automaticamente, com sugerir como padrão; verificar que uma instalação nova tem o padrão correto
- [ ] 6.2 Implementar a notificação de sugestão com início da gravação em um único acionamento, e verificar aceitando a sugestão
- [ ] 6.3 Implementar o tempo limite após o qual a sugestão deixa de ser oferecida sem iniciar gravação, e verificar ignorando a sugestão
- [ ] 6.4 Implementar o início automático com notificação informando a origem automática e oferecendo descarte imediato, e verificar que o descarte encerra a gravação e remove sessão e áudio sem submeter para transcrição
- [ ] 6.5 Implementar a não interferência com gravação já em andamento, e verificar por teste que nenhuma sugestão é apresentada e que a gravação ativa não é afetada
- [ ] 6.6 Implementar a apresentação da lista de aplicativos considerados na detecção com exclusão por aplicativo, e verificar que um aplicativo excluído não dispara detecção em nenhuma circunstância
- [ ] 6.7 Implementar a desativação completa da detecção e verificar que nenhuma sugestão nem início automático ocorre com ela desligada

## 7. Verificação de ponta a ponta

- [ ] 7.1 Entrar numa reunião real com a ação configurada para sugerir e verificar que a sugestão aparece dentro do período esperado e que aceitá-la grava a reunião desde aquele ponto
- [ ] 7.2 Repetir com a ação configurada para iniciar automaticamente e verificar que a gravação começa sozinha e que o descarte pela notificação remove sessão e áudio
- [ ] 7.3 Verificar, ao longo de um dia de uso normal, quantas detecções falsas ocorreram e ajustar o período mínimo de sustentação conforme o resultado, registrando o valor escolhido
- [ ] 7.4 Gravar uma reunião de ponta a ponta usando apenas o atalho global e a bandeja, sem abrir a janela principal, e verificar que a transcrição aparece normalmente
- [ ] 7.5 Medir o consumo em repouso do aplicativo com a detecção ativa ao longo de uma hora sem reunião e verificar que permanece dentro dos limites configurados
