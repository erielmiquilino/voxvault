## Context

Motivação em `proposal.md`. Estado atual que determina a abordagem:

- **Bandeja** (`src-tauri/src/tray.rs`): ícone fixo, que é o próprio ícone da janela; menu com "Abrir", "Iniciar/encerrar" e "Fechar o aplicativo"; clique esquerdo abre a janela. Iniciar pela bandeja traz a janela para frente. O comentário do módulo declara o limite: "a bandeja vive enquanto a janela vive".
- **Janela** (`lib.rs`): criada pelo `tauri.conf.json` na partida. `CloseRequested` é interceptado para uma confirmação na interface quando há gravação (`app://fechamento-solicitado`, `fechamento_aprovado`, `estado_de_fechamento`). O `RunEvent::ExitRequested` encerra a gravação de forma limpa no desligamento do sistema.
- **Supervisão do serviço** (`lib.rs`, `service.rs`): uma thread Rust independente da janela consulta `/saude` e, em gravação, `/gravacao` a cada 2 s; ociosa, também `/deteccao` a cada 10 s. Continua viva sem a janela — é ela que alimenta a bandeja.
- **Memória medida** nesta máquina: janela visível 350 MB (7 processos); janela escondida 361 MB, ou seja, esconder não libera nada; só o executável, sem WebView2, 27 MB; serviço ocioso 40 MB.
- **Dados da gravação** que o serviço já publica em `/gravacao`: `niveis.<trilha>.pico`, `niveis.<trilha>.silencio_ha_s` — que só zera com uma amostra diferente de zero — e `avisos`, a lista acumulada que inclui os do supervisor de dispositivos.
- **Eventos do serviço**: `_record_event(tipo, detalhe)` guarda até 200 eventos em memória (`pronta`, `falhou`, `aviso`...), mas nenhuma rota os expõe, e o `uid` da reunião vem embutido no texto.
- **Atalho**: Alt+Shift+R fixo no código.

## Goals / Non-Goals

**Goals:**

- Aplicativo residente de verdade: a bandeja, a detecção e as notificações funcionam com a janela fechada.
- Presença leve na bandeja: interface descarregada, dentro dos limites de `desktop-shell`.
- O ícone como resposta confiável a "o VoxVault está gravando meu microfone agora?".

**Non-Goals:**

- Indicar o uso do microfone por outros aplicativos — o Windows já mostra isso.
- Manter a janela em memória para reabrir instantaneamente.
- Atualização automática, telemetria ou qualquer chamada de rede nova.
- Mudar a política de ociosidade do serviço: com o aplicativo na bandeja, as consultas dele mantêm o serviço vivo (40 MB), e é isso que mantém o início de gravação dentro do orçamento, com os dispositivos já aquecidos.

## Decisions

### 1. A janela é criada sob demanda e destruída ao recolher

- `tauri.conf.json` passa a declarar a janela `main` com `"create": false`. O `setup` a cria, com `WebviewWindowBuilder::from_config`, exceto quando o processo recebe `--bandeja`, que é o argumento do início automático.
- `WindowEvent::CloseRequested`: deixa fechar, e a janela é destruída. `WindowEvent::Resized` com `is_minimized()`: `destroy()`. As duas coisas levam ao mesmo estado.
- `RunEvent::ExitRequested` com `code == None` — a última janela sumiu — chama `api.prevent_exit()`. Só "Sair do VoxVault" chama `app.exit(0)`. O desligamento do sistema continua pelo caminho atual.
- O aviso de primeiro recolhimento sai uma vez e grava `aviso_da_bandeja_mostrado: true` nas preferências (decisão 6).
- Some a confirmação de fechamento na interface, com `app://fechamento-solicitado`, `fechamento_aprovado` e `estado_de_fechamento`. Fechar não encerra mais nada.

*Alternativa descartada:* esconder a janela. É instantâneo, mas medido não libera memória: 361 MB escondida contra 350 MB visível. O recolhimento destruindo a janela fica em torno de 67 MB.

### 2. Restauração da última tela

A interface informa a rota corrente a cada navegação por um comando `app_registrar_rota(rota)`, guardado em memória no processo Rust. Ao recriar a janela, a interface pede `app_rota_inicial()` antes do primeiro desenho e navega direto para ela. Numa partida nova do processo, a rota inicial é a tela de Gravação.

Verificação dos 2 s: medir do pedido de abertura até a primeira renderização da rota, com `performance.now()` na interface reportado ao Rust e registrado.

### 3. Estado da bandeja derivado de um único mapeamento

`TrayState = Ocioso | Gravando | Pausado | Falha` sai de uma função pura sobre o `Snapshot` do serviço:

- `Falha` quando o estado não é `Conectado`;
- senão `Gravando` ou `Pausado`, pelo `/gravacao`;
- senão `Ocioso`.

A função pura é o que se testa. A cada troca de estado: ícone (`set_icon`), tooltip (`set_tooltip`) e itens do menu (`set_text`/`set_enabled`).

- **Cadência.** O intervalo ocioso de consulta cai de 10 s para 5 s, para cumprir o limite de 5 s do requisito com gravações iniciadas por outra superfície. As ações da própria bandeja e do atalho atualizam o estado na hora, sem esperar a consulta.
- **Tooltip.** Durante uma gravação, uma thread de 1 s reescreve `VoxVault — gravando 00:12:34 · <título>`, com o tempo decorrido calculado de `duracao_ms` e do instante local em que foi lido, sem esperar a consulta de 2 s. Fora de gravação a thread não roda. Os outros textos: `VoxVault — ocioso`, `VoxVault — pausado em 00:12:34` e `VoxVault — serviço indisponível: <causa curta>`.
- **Menu**, em ordem:
  1. Iniciar gravação / Encerrar gravação
  2. Pausar / Retomar
  3. separador
  4. Abrir o VoxVault
  5. Abrir a última reunião
  6. separador
  7. Sair do VoxVault

  "Abrir a última reunião" usa a reunião mais recente lida pelo módulo `library` e abre a janela na rota dela.

### 4. Ícones por forma, gerados do ícone do aplicativo

Quatro PNG de 32×32 em `src-tauri/icons/bandeja/`, embutidos com `include_bytes!`:

| Estado | Arquivo | Forma |
|---|---|---|
| Ocioso | `ocioso.png` | o ícone do app |
| Gravando | `gravando.png` | círculo cheio vermelho `#E5484D` no canto inferior direito, com contorno branco |
| Pausado | `pausado.png` | duas barras verticais âmbar `#F5A524` |
| Falha | `falha.png` | ícone em tons de cinza com um triângulo de exclamação |

São gerados por `voxvault-app/tools/gerar-icones-bandeja.py` a partir de `icons/icon.png`, rodado com `uv run --with pillow`, e ficam versionados; o build não depende do Pillow. As formas distinguem os estados também para quem não distingue cores.

### 5. Notificações com botão pelo WinRT, e eventos com cursor no serviço

- **Montagem.** O XML da notificação é montado pelo aplicativo e mostrado pelo WinRT (`windows::UI::Notifications`), com os títulos escapados. O plugin oficial de notificações não expõe botões de ação no desktop, e o botão "Gravar" é exigido pela detecção de reunião.
- **Clique por endereço.** Cada notificação com destino leva `activationType="protocol"` e um endereço `voxvault://abrir/<rota>`; o botão "Gravar" leva `voxvault://gravar/<token>`. O esquema `voxvault:` é registrado em `HKCU\Software\Classes` pelo próprio aplicativo a cada partida, apontando para o executável em uso, e removido pela desinstalação. O Windows abre o endereço de onde a notificação for clicada, iniciando o executável com ele; a instância única o entrega ao processo que já roda, que abre a janela na rota; clicada com o aplicativo encerrado, ela o inicia já nessa rota. Só as rotas de gravação e de uma reunião são aceitas; o resto abre a janela, como uma segunda abertura qualquer.
- **Token do "Gravar".** Qualquer programa pode abrir um endereço, então nenhum grava sozinho: o token é emitido pelo processo ao mostrar a sugestão, vale enquanto ela vale e é usado uma vez; um token desconhecido ou vencido só abre a janela.
- **AUMID.** O identificador do pacote, `com.erielmiquilino.voxvault`, que o atalho do menu Iniciar criado pelo instalador registra. Rodando fora de uma instalação, sem esse atalho, usa-se o AUMID do PowerShell, para as notificações aparecerem no desenvolvimento. A verificação no aplicativo instalado é uma tarefa de `add-public-release`.
- **Eventos.** O serviço ganha `GET /eventos?desde=<seq>`, que devolve `{"eventos": [{"seq", "tipo", "uid", "titulo", "detalhe", "instante"}], "ultimo": <seq>, "execucao": <instante de partida do serviço>}`, com o título da reunião em `titulo` para a notificação nomeá-la sem consultar a lista. O `seq` é monotônico por execução do serviço; `execucao` muda quando o serviço reinicia e a numeração recomeça, e o aplicativo, ao ver outra `execucao`, lê a nova execução desde o início — tudo nela aconteceu com ele já aberto. `_record_event` e o callback `on_event` do pipeline passam a receber o `uid` separado do texto.
- **Primeira consulta.** Na primeira consulta de cada sessão do aplicativo, ele só registra `ultimo` e não notifica nada. É isso que impede notificar eventos anteriores à abertura.
- **Origem de cada categoria.**
  - *Gravação iniciada / encerrada:* transições `Ocioso ↔ Gravando` do mapeamento da decisão 3, que cobrem qualquer origem, com deduplicação pelo `uid` da gravação, para que a ação da própria bandeja não gere duas notificações.
  - *Transcrição concluída / falha:* `/eventos` com tipo `pronta` / `falhou`.
  - *Avisos de captura:* novas entradas em `/gravacao.avisos` que tratam de dispositivo — perdido, recuperando, gravando agora em, encerrada como incompleta.
  - *Microfone mudo:* `niveis.mic.silencio_ha_s ≥ 60`, emitido uma vez por episódio. O episódio termina quando o valor volta abaixo de 60.
  - *Reunião detectada:* `/deteccao` passando a indicar reunião, com o botão "Gravar". O token do botão registra o instante; acioná-lo depois de 2 minutos abre a janela em vez de gravar. Esse é o tempo limite da sugestão.
- **Deduplicação.** Um conjunto em memória de chaves (`tipo:uid:seq`) já notificadas.
- **Ao clicar numa notificação:** transcrição → rota da reunião; gravação e avisos → tela de Gravação; aviso da bandeja → nada.

*Alternativa descartada:* o `on_activated` de `tauri-winrt-notification`, a primeira implementação. Um aplicativo sem pacote só recebe esse aviso dentro do processo enquanto o Windows ainda segura a notificação mostrada; clicada na central — para onde todas vão com o "Não incomodar" ligado —, ela não chegava a lugar algum, e isso só apareceu com o aplicativo instalado. A outra saída, um ativador COM registrado no atalho, pede um servidor COM no aplicativo para o mesmo resultado.

### 6. Preferências do aplicativo em arquivo próprio

`%USERPROFILE%\.voxvault\aplicativo.json`, ao lado da configuração do núcleo e pelo mesmo motivo, porque fica fora da virtualização de `%APPDATA%`. É do aplicativo, e o núcleo não o lê: o `config.json` do núcleo recusa campos desconhecidos, de propósito. Campos e padrões:

```json
{
  "atalho": "Alt+Shift+R",
  "notificacoes": {
    "gravacao_iniciada": true, "gravacao_encerrada": true,
    "transcricao_concluida": true, "transcricao_falha": true,
    "avisos_de_captura": true, "reuniao_detectada": true
  },
  "aviso_da_bandeja_mostrado": false
}
```

Escrita atômica, com temporário e troca. Um arquivo ilegível vale como padrões e é regravado na próxima alteração, com um aviso na tela de Configurações. O estado do início automático **não** é duplicado aqui: a fonte de verdade é o registro do Windows, consultado com `is_enabled()`.

### 7. Início com o sistema pelo plugin oficial

`tauri-plugin-autostart` com argumento `--bandeja`, que cria o valor em `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. A tela de Configurações ganha a seção **Aplicativo**, com três partes:

- iniciar com o Windows;
- atalho global, com captura da combinação e aviso de conflito;
- as seis categorias de notificação, com o aviso de que desligar as de gravação deixa o atalho sem confirmação além do ícone.

Remover o valor do registro na desinstalação é tarefa de `add-public-release`.

### 8. Atalho trocado em tempo de execução

Registrar primeiro o novo atalho e, só com sucesso, liberar o antigo e persistir. Com falha, o antigo continua valendo e a interface mostra o conflito. A captura na interface aceita combinações com ao menos um modificador — Ctrl, Alt, Shift ou Win — e uma tecla que não seja modificador.

### 9. Sair pela bandeja com confirmação nativa

Sem janela para mostrar diálogo da interface, "Sair do VoxVault" usa `tauri-plugin-dialog`, com `message(...).buttons(OkCancelCustom("Encerrar gravação e sair", "Cancelar"))`, numa thread. O texto informa a duração da gravação. Confirmado, chama `POST /gravacao/encerrar`, espera a resposta e sai. Sem gravação, sai direto. O serviço nunca é encerrado pelo aplicativo.

## Risks / Trade-offs

- [Reabrir custa recriar o WebView2, cerca de 1–2 s] → decidido com o usuário em troca de ~300 MB. A rota é restaurada, e o limite de 2 s é verificado por medição.
- [O que estava na janela ao recolher se perde — um título digitado, um diálogo aberto] → aceito. Só a rota é restaurada. A gravação vive no serviço e nunca é afetada.
- [Um fone com supressão de ruído agressiva pode entregar zeros exatos numa sala silenciosa e disparar o aviso de microfone mudo] → o texto do aviso é "o microfone está entregando silêncio absoluto há 1 minuto — confira se não está mudo". Ele não altera a gravação, e a categoria pode ser desligada.
- [Sem o atalho do menu Iniciar registrando o AUMID, as notificações não aparecem] → fallback para o AUMID do PowerShell no desenvolvimento; verificação no aplicativo instalado em `add-public-release`.
- [Consultar o serviço a cada 5 s em vez de 10 s] → uma requisição local a mais a cada 10 s, medida dentro do limite de 1% de processador ocioso na bandeja.
- [Ícone escondido na área de transbordamento da bandeja] → o Windows decide isso, não o aplicativo. A notificação ao iniciar cobre o caso, e o README ensina a fixar o ícone.

## Migration Plan

- A primeira execução depois da atualização cria `aplicativo.json` com os padrões; nada do usuário se perde, porque o atalho era fixo.
- O comportamento de fechar muda: o aviso de primeiro recolhimento explica, e o `aviso_da_bandeja_mostrado` garante que só uma vez.
- Arquivamento: esta mudança altera `tray-and-hotkeys`, de `add-recording-conveniences`, e `desktop-shell`, de `add-desktop-app`. As duas precisam estar arquivadas antes desta.
