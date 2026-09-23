## 1. Núcleo: eventos com cursor

- [x] 1.1 Separar o `uid` do texto em `_record_event` e no callback `on_event` do pipeline, e numerar os eventos com um `seq` monotônico por execução; verificar com testes que os eventos `pronta` e `falhou` de uma transcrição carregam o `uid` da reunião e `seq` crescente.
- [x] 1.2 Adicionar `GET /eventos?desde=<seq>` com o contrato do design (decisão 5), protegido pelo mesmo segredo das outras rotas; verificar com testes: `desde` ausente devolve só `ultimo`, `desde` antigo devolve os eventos posteriores em ordem, e sem segredo a resposta é 403.

## 2. Aplicativo: preferências e ciclo de vida da janela

- [x] 2.1 Implementar o módulo de preferências `aplicativo.json` (campos, padrões, escrita atômica, arquivo ilegível tratado como padrões); verificar com testes Rust de ida e volta, de padrões com o arquivo ausente e de arquivo corrompido.
- [x] 2.2 Declarar a janela com `"create": false`, criá-la no `setup` salvo com `--bandeja`, destruí-la ao fechar e ao minimizar, e impedir a saída ao perder a última janela; verificar executando o binário: fechar e minimizar deixam só o ícone, sem botão na barra de tarefas, e o processo segue vivo.
- [x] 2.3 Remover o caminho de confirmação ao fechar (`app://fechamento-solicitado`, `fechamento_aprovado`, `estado_de_fechamento` e o diálogo correspondente na interface); verificar que `cargo test`, `npm run check` e o build passam e que fechar a janela durante uma gravação não pergunta nada e não interrompe a gravação.
- [x] 2.4 Registrar a rota corrente (`app_registrar_rota`) e restaurá-la ao recriar a janela (`app_rota_inicial`), medindo o tempo até a primeira renderização; verificar pelo depurador do WebView2 que reabrir com uma reunião aberta volta nela e que o tempo medido fica em até 2 s.
- [x] 2.5 Emitir o aviso de primeiro recolhimento uma única vez, persistido em `aviso_da_bandeja_mostrado`; verificar recolhendo duas vezes e reiniciando o aplicativo: uma única notificação.
- [x] 2.6 Instância única recriando a janela recolhida na última tela; verificar executando o binário uma segunda vez com o aplicativo na bandeja: a janela volta na última tela e nenhum processo novo permanece.

## 3. Aplicativo: estado, ícones e menu da bandeja

- [x] 3.1 Gerar os quatro ícones com `tools/gerar-icones-bandeja.py` (decisão 4) e versioná-los em `src-tauri/icons/bandeja/`; verificar abrindo os quatro PNG, com o mesmo tamanho e formas distinguíveis em tons de cinza.
- [x] 3.2 Implementar o mapeamento puro `Snapshot → TrayState` e aplicá-lo a ícone, tooltip e menu a cada troca; verificar com testes Rust dos quatro estados, e de que o serviço fora de `Conectado` vira `Falha`.
- [ ] 3.3 Implementar o menu na ordem do design, com habilitação por estado, "Abrir a última reunião" pela reunião mais recente e o clique principal abrindo a janela; verificar com testes da habilitação por estado e, executando, que iniciar e encerrar pelo menu não abrem a janela.
- [ ] 3.4 Implementar a thread de tooltip de 1 s, ativa só durante gravação, e reduzir o intervalo ocioso de consulta para 5 s; verificar que uma gravação iniciada por `voxvault record` com o aplicativo na bandeja muda o ícone em até 5 s e que o tooltip avança a cada segundo.
- [ ] 3.5 Trocar a partida da gravação pelo atalho e pela bandeja para não abrir a janela; verificar executando o atalho com outro aplicativo em tela cheia: a gravação começa e a janela não aparece.

## 4. Aplicativo: notificações

- [x] 4.1 Integrar `tauri-winrt-notification` com a escolha de AUMID do design (identificador instalado, PowerShell fora de instalação) e um comando de depuração que dispara cada categoria; verificar que as seis aparecem no desenvolvimento.
- [ ] 4.2 Notificações de gravação iniciada e encerrada pelas transições de estado, com deduplicação pelo `uid` da gravação; verificar que iniciar pela bandeja produz exatamente uma notificação e que iniciar por `voxvault record` também produz uma.
- [ ] 4.3 Notificações de transcrição concluída e falha por `/eventos`, com o primeiro `ultimo` apenas registrado e o clique abrindo a reunião; verificar que abrir o aplicativo depois de transcrições concluídas não notifica nada, e que uma transcrição que termina com o aplicativo na bandeja notifica e abre a reunião ao clique.
- [ ] 4.4 Avisos de captura: entradas de dispositivo em `/gravacao.avisos` e `silencio_ha_s ≥ 60` no microfone, uma vez por episódio; verificar com um microfone mudo pelo Windows durante uma gravação — uma notificação em 60 s, nenhuma nova até o sinal voltar e faltar de novo — e com a sala em silêncio e o microfone vivo, sem notificação.
- [ ] 4.5 Reunião detectada com o botão "Gravar", expirando em 2 minutos para abrir a janela; verificar abrindo uma chamada de teste num aplicativo reconhecido com o VoxVault na bandeja: a notificação aparece, "Gravar" inicia a gravação, e acioná-la depois de 2 minutos só abre a janela.
- [ ] 4.6 Chaves das seis categorias nas preferências, respeitadas por todas as fontes; verificar desligando "gravação iniciada": iniciar não notifica, e as demais continuam.

## 5. Aplicativo: início com o sistema, atalho e Configurações

- [x] 5.1 Integrar `tauri-plugin-autostart` com `--bandeja` e ligá-lo à seção Aplicativo das Configurações; verificar que ligar cria o valor em `HKCU\...\Run` com `--bandeja`, que executar o binário com `--bandeja` sobe só na bandeja sem gravar, e que desligar remove o valor.
- [x] 5.2 Atalho configurável: captura da combinação, registro do novo antes de liberar o antigo, persistência e aviso de conflito (decisão 8); verificar trocando para uma combinação livre, que passa a funcionar sem reiniciar, e para uma já ocupada, em que o conflito aparece e o antigo continua valendo.
- [x] 5.3 Seção Aplicativo com o aviso de que desligar as notificações de gravação deixa o atalho sem confirmação além do ícone; verificar pelo depurador do WebView2 que o aviso aparece e some conforme as chaves.
- [ ] 5.4 "Sair do VoxVault" com confirmação nativa durante gravação (decisão 9); verificar com uma gravação ativa e a janela fechada: a confirmação mostra a duração, confirmar encerra a gravação submetida para transcrição e sai, e cancelar mantém tudo.

## 6. Medição e registro

- [x] 6.1 Estender `tools/medir-custo.ps1` com o modo `bandeja`, sem janela, e medir 10 minutos ocioso na bandeja; verificar que a memória somada fica em até 100 MB e o processador médio em até 1%.
- [ ] 6.2 Medir uma gravação de 60 minutos com o aplicativo recolhido na bandeja; verificar que a memória somada fica em até 250 MB, que o processador não passa do medido com a janela aberta e que a gravação termina íntegra.
- [x] 6.3 Atualizar `docs/estado-da-implementacao.md` e o comentário de módulo de `tray.rs`, que hoje declara que a bandeja morre com a janela; verificar que ambos descrevem o comportamento residente e citam as medições.
