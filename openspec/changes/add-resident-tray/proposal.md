## Why

O VoxVault só existe enquanto a janela está aberta: fechá-la encerra a bandeja e, com ela, a detecção de reunião, e o ícone não diz se o microfone está sendo gravado naquele instante. A spec de conveniências (`add-recording-conveniences`) já previa ícone com estado, notificações e início com o sistema, mas a implementação cobriu só um menu mínimo com ícone fixo — e manter a janela viva só para ter a bandeja custaria cerca de 390 MB o dia inteiro, porque esconder a janela, medido, não libera memória nenhuma.

## What Changes

- **BREAKING (comportamento):** fechar a janela (X, Alt+F4) e minimizá-la passam a recolher o aplicativo para a bandeja, sem encerrá-lo e sem interromper gravação. Sair de verdade só pelo item "Sair do VoxVault" da bandeja. Um aviso único explica isso na primeira vez.
- Ao recolher, a interface é descarregada da memória: restam o ícone e o serviço, cerca de 67 MB medidos. Reabrir recria a janela na última tela vista.
- Ícone com estado — ocioso, gravando, pausado, falha do serviço —, distinguível por forma e não só por cor, refletindo apenas a captura do próprio VoxVault. O tooltip traz o estado e o tempo decorrido.
- Clique esquerdo abre a janela. Clique direito abre o menu, com "Iniciar gravação"/"Encerrar gravação" como primeiro item, seguido de pausar/retomar, abrir o VoxVault, abrir a última reunião e sair.
- Gravar pela bandeja ou pelo atalho não abre a janela: o ícone muda e uma notificação confirma. Se o microfone ficar 60 s sem áudio durante a gravação, uma notificação avisa.
- Notificações do sistema por categoria, desativáveis individualmente, funcionando com a janela fechada — incluindo a sugestão de gravar ao detectar reunião, com o botão "Gravar" que a spec de detecção já exige.
- Início com o Windows configurável, desligado por padrão, subindo direto na bandeja.
- Atalho global configurável nas Configurações, com padrão Alt+Shift+R.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `tray-and-hotkeys`: "Ícone de bandeja com estado", "Ações rápidas pela bandeja", "Atalho global de teclado", "Fechamento para a bandeja", "Notificações do sistema" e "Início junto com o sistema" são reescritos com as decisões desta mudança.
- `desktop-shell`: "Instância única" passa a recriar a janela recolhida; "Custo desprezível durante a gravação" ganha limites para o aplicativo recolhido na bandeja, com e sem gravação.

A detecção de reunião (`meeting-autodetect`) não muda de requisito: a notificação com acionamento único para gravar já é o especificado, e esta mudança é o que a torna possível com a janela fechada.

## Impact

- **Aplicativo (Rust):** ciclo de vida da janela — criação sob demanda, destruição ao recolher, saída só pela bandeja —, estado e ícones da bandeja, notificações, início com o sistema, atalho configurável, preferências próprias do aplicativo em `%USERPROFILE%\.voxvault\aplicativo.json`.
- **Aplicativo (interface):** seção "Aplicativo" nas Configurações; registro da tela corrente para a restauração; fim do diálogo de confirmação ao fechar a janela.
- **Núcleo:** rota `GET /eventos` no serviço, com cursor, para as notificações de transcrição concluída e falha.
- **Dependências novas:** `tauri-plugin-autostart` e `tauri-winrt-notification`.
- **Ícones:** quatro variantes de ícone de bandeja, geradas do ícone do aplicativo.
- **Arquivamento:** depende de `add-recording-conveniences` e `add-desktop-app` arquivadas antes desta.
