<script lang="ts">
  // The resident application's own settings: starting with Windows, the
  // global shortcut and the notifications. None of these belong to the core,
  // which is why they live in the app's own preferences file and not in the
  // shared configuration.
  import {
    aplicativoLer,
    atalhoTrocar,
    comoFalha,
    inicioComOWindowsDefinir,
    notificacaoDeTeste,
    notificacoesDefinir,
    type Aplicativo,
    type CategoriaDeNotificacao,
  } from "../api";
  import { recado } from "../estado.svelte";

  const CATEGORIAS: { chave: CategoriaDeNotificacao; rotulo: string; detalhe: string }[] = [
    { chave: "gravacao_iniciada", rotulo: "Gravação iniciada", detalhe: "Por qualquer caminho: bandeja, atalho, janela ou linha de comando." },
    { chave: "gravacao_encerrada", rotulo: "Gravação encerrada", detalhe: "Com a duração, quando a reunião vai para a fila de transcrição." },
    { chave: "transcricao_concluida", rotulo: "Transcrição concluída", detalhe: "Clicar abre a reunião." },
    { chave: "transcricao_falha", rotulo: "Transcrição com falha", detalhe: "Com o motivo; clicar abre a reunião." },
    { chave: "avisos_de_captura", rotulo: "Avisos de captura", detalhe: "Dispositivo perdido, recuperado ou trocado, e microfone entregando silêncio absoluto há 1 minuto." },
    { chave: "reuniao_detectada", rotulo: "Reunião detectada", detalhe: "Com o botão Gravar, válido por 2 minutos." },
  ];

  let app = $state<Aplicativo | null>(null);
  let falha = $state<string | null>(null);
  let capturando = $state(false);
  let erroDoAtalho = $state<string | null>(null);
  let ocupado = $state(false);
  let campoDeCaptura = $state<HTMLInputElement | null>(null);

  // The capture field takes the keyboard as soon as it appears, so the very
  // next combination pressed is the one recorded.
  $effect(() => {
    if (capturando) campoDeCaptura?.focus();
  });

  const semConfirmacaoDoAtalho = $derived(
    !!app &&
      (!app.preferencias.notificacoes.gravacao_iniciada ||
        !app.preferencias.notificacoes.gravacao_encerrada),
  );

  $effect(() => {
    void carregar();
  });

  async function carregar() {
    try {
      app = await aplicativoLer();
      erroDoAtalho = app.atalho.conflito;
      falha = null;
    } catch (erro) {
      falha = comoFalha(erro).mensagem;
    }
  }

  async function aplicar(acao: () => Promise<Aplicativo>, sucesso: string) {
    ocupado = true;
    try {
      app = await acao();
      erroDoAtalho = app.atalho.conflito;
      recado("ok", sucesso);
    } catch (erro) {
      recado("erro", comoFalha(erro).mensagem);
      await carregar();
    } finally {
      ocupado = false;
    }
  }

  function alternarInicio(ligado: boolean) {
    void aplicar(
      () => inicioComOWindowsDefinir(ligado),
      ligado
        ? "O VoxVault vai iniciar com o Windows, direto na bandeja."
        : "O VoxVault não inicia mais com o Windows.",
    );
  }

  function alternarCategoria(chave: CategoriaDeNotificacao, ligada: boolean) {
    if (!app) return;
    const chaves = { ...app.preferencias.notificacoes, [chave]: ligada };
    void aplicar(() => notificacoesDefinir(chaves), "Notificações atualizadas.");
  }

  /** A key as the registration names it: letters and digits bare, the rest
   *  by their W3C code. `null` for a modifier pressed alone. */
  function teclaDe(codigo: string): string | null {
    if (/^Key[A-Z]$/.test(codigo)) return codigo.slice(3);
    if (/^Digit[0-9]$/.test(codigo)) return codigo.slice(5);
    if (/^(Control|Alt|Shift|Meta|OS)(Left|Right)?$/.test(codigo)) return null;
    return codigo;
  }

  function capturar(evento: KeyboardEvent) {
    evento.preventDefault();
    evento.stopPropagation();
    if (evento.key === "Escape" && !evento.ctrlKey && !evento.altKey && !evento.shiftKey) {
      capturando = false;
      return;
    }
    const tecla = teclaDe(evento.code);
    if (!tecla) return;
    const modificadores = [
      evento.ctrlKey && "Ctrl",
      evento.altKey && "Alt",
      evento.shiftKey && "Shift",
      evento.metaKey && "Super",
    ].filter(Boolean) as string[];
    if (modificadores.length === 0) {
      erroDoAtalho = "Use ao menos um modificador — Ctrl, Alt, Shift ou Win — junto com a tecla.";
      return;
    }
    capturando = false;
    const atalho = [...modificadores, tecla].join("+");
    void aplicar(() => atalhoTrocar(atalho), `Atalho trocado para ${atalho}, já valendo.`);
  }
</script>

<section class="cartao" id="aplicativo">
  <header>
    <h2>Aplicativo</h2>
    <span class="legenda">Bandeja, início com o Windows, atalho e notificações</span>
  </header>

  {#if falha}
    <div class="nota erro"><p style="margin:0">{falha}</p></div>
  {:else if app}
    {#if app.preferencias_ilegiveis}
      <div class="nota alerta" style="margin-bottom:12px">
        <strong>Preferências do aplicativo ilegíveis</strong>
        <p style="margin:0">{app.preferencias_ilegiveis}</p>
      </div>
    {/if}

    <p class="legenda">
      Fechar ou minimizar a janela recolhe o VoxVault para a bandeja: ele continua
      pronto para gravar pelo ícone e pelo atalho, e as notificações continuam
      chegando. Para encerrar de vez, use "Sair do VoxVault" no menu do ícone.
    </p>

    <label class="linha" style="gap:8px;margin:10px 0 14px">
      <input
        type="checkbox"
        style="width:auto"
        checked={app.inicio_com_o_windows ?? false}
        disabled={ocupado || app.inicio_com_o_windows === null}
        onchange={(e) => alternarInicio((e.currentTarget as HTMLInputElement).checked)}
      />
      <span>
        <strong>Iniciar com o Windows</strong>
        <span class="legenda">— sobe direto na bandeja e nunca começa a gravar sozinho</span>
      </span>
    </label>
    {#if app.inicio_com_o_windows_erro}
      <p class="legenda" style="color:var(--gravando)">{app.inicio_com_o_windows_erro}</p>
    {/if}

    <h3 style="margin:6px 0 6px">Atalho global</h3>
    <div class="linha">
      <span class="selo acento mono" style="font-size:12.5px">
        {app.atalho.em_vigor ?? "nenhum em vigor"}
      </span>
      {#if capturando}
        <input
          type="text"
          readonly
          placeholder="Pressione a combinação — Esc cancela"
          onkeydown={capturar}
          onblur={() => (capturando = false)}
          style="max-width:320px"
          aria-label="Capturar novo atalho"
          bind:this={campoDeCaptura}
        />
      {:else}
        <button class="botao" disabled={ocupado} onclick={() => { erroDoAtalho = null; capturando = true; }}>
          Trocar atalho
        </button>
      {/if}
    </div>
    <p class="legenda" style="margin:6px 0 0">
      Inicia a gravação quando ociosa e a encerra quando em andamento, com qualquer
      aplicativo em primeiro plano, sem abrir a janela.
    </p>
    {#if erroDoAtalho}
      <div class="nota erro" style="margin-top:8px">
        <p style="margin:0">{erroDoAtalho}</p>
      </div>
    {/if}

    <h3 style="margin:16px 0 6px">Notificações</h3>
    <div style="display:flex;flex-direction:column;gap:6px">
      {#each CATEGORIAS as categoria (categoria.chave)}
        <label class="linha" style="gap:8px;align-items:flex-start;flex-wrap:nowrap">
          <input
            type="checkbox"
            style="width:auto;margin-top:3px"
            checked={app.preferencias.notificacoes[categoria.chave]}
            disabled={ocupado}
            onchange={(e) =>
              alternarCategoria(categoria.chave, (e.currentTarget as HTMLInputElement).checked)}
          />
          <span style="flex:1">
            <strong>{categoria.rotulo}</strong>
            <span class="legenda"> — {categoria.detalhe}</span>
          </span>
          <button
            class="botao discreto"
            title="Mostra uma notificação de exemplo desta categoria"
            onclick={() => void notificacaoDeTeste(categoria.chave)}
          >
            Testar
          </button>
        </label>
      {/each}
    </div>

    {#if semConfirmacaoDoAtalho}
      <div class="nota alerta" style="margin-top:12px" id="aviso-atalho-sem-confirmacao">
        <strong>O atalho fica sem confirmação além do ícone</strong>
        <p style="margin:0">
          Com as notificações de gravação iniciada ou encerrada desligadas, acionar o
          atalho durante uma reunião em tela cheia só muda o ícone da bandeja — que
          pode nem estar visível. Confira o ícone depois de usar o atalho.
        </p>
      </div>
    {/if}
  {:else}
    <p class="legenda">Carregando...</p>
  {/if}
</section>
