<script lang="ts">
  import { listen } from "@tauri-apps/api/event";
  import { tick } from "svelte";

  import {
    ambienteEstado,
    appJanelaPronta,
    appRegistrarRota,
    appRotaInicial,
    diagnosticoDoAplicativo,
    servicoEstado,
    type ServicoSnapshot,
  } from "./lib/api";
  import {
    gravacao,
    irParaRota,
    navegacao,
    recado,
    rotaAtual,
    shell,
    pararRelogio,
    zerarGravacao,
  } from "./lib/estado.svelte";
  import { carimbo } from "./lib/format";
  import BarraEstado from "./lib/components/BarraEstado.svelte";
  import Recados from "./lib/components/Recados.svelte";
  import Biblioteca from "./views/Biblioteca.svelte";
  import Configuracoes from "./views/Configuracoes.svelte";
  import Gravacao from "./views/Gravacao.svelte";
  import Preparo from "./views/Preparo.svelte";

  let diagnosticoOk = $state<boolean | null>(null);
  let rotaRestaurada = false;

  // The window is destroyed whenever it goes to the tray and built again when
  // it comes back, so where it was is asked of the host before the first draw,
  // and the time until the restored route is on screen is reported back.
  async function carregarShell() {
    irParaRota(await appRotaInicial());
    shell.ambiente = await ambienteEstado();
    shell.servico = await servicoEstado();
    shell.carregado = true;
    await tick();
    rotaRestaurada = true;
    void appJanelaPronta(performance.now());
    if (shell.ambiente.preparado) {
      const itens = await diagnosticoDoAplicativo();
      diagnosticoOk = itens.every((item) => item.ok);
    }
  }

  $effect(() => {
    void carregarShell();
  });

  // Every navigation is reported, so the window reopens where it was left.
  $effect(() => {
    const rota = rotaAtual();
    if (rotaRestaurada) void appRegistrarRota(rota);
  });

  // The host pushes only when the picture changed, so there is no polling on
  // this side either.
  $effect(() => {
    const assinaturas = [
      listen<ServicoSnapshot>("servico://estado", (evento) => {
        shell.servico = evento.payload;
        // The service is the only authority on whether a recording exists. A
        // recording started from the command line therefore appears here as
        // active, which is correct: it is the same recording.
        const ativa = evento.payload.saude?.gravacao_ativa ?? false;
        if (!ativa && gravacao.estado !== "ocioso") {
          pararRelogio();
          zerarGravacao();
        }
      }),
      listen<string>("servico://queda", (evento) => recado("erro", evento.payload)),
      listen<string>("servico://desistiu", (evento) => recado("erro", evento.payload)),
      // A tray action or a clicked notification pointing this window elsewhere.
      listen<string>("app://navegar", (evento) => irParaRota(evento.payload)),
      listen("servico://conectado", () => {
        void diagnosticoDoAplicativo().then((itens) => {
          diagnosticoOk = itens.every((item) => item.ok);
        });
      }),
    ];
    return () => {
      for (const assinatura of assinaturas) assinatura.then((cancelar) => cancelar());
    };
  });

  function irPara(tela: "gravacao" | "biblioteca" | "configuracoes") {
    navegacao.tela = tela;
  }
</script>

{#if !shell.carregado}
  <div class="vazio" style="height:100%;display:grid;place-items:center">Abrindo o VoxVault...</div>
{:else if shell.ambiente && !shell.ambiente.preparado}
  <!-- The main interface does not open while the environment is not usable:
       commands that merely look like they work are worse than a closed door. -->
  <Preparo ambiente={shell.ambiente} onPronto={carregarShell} />
{:else}
  <div class="janela">
    <header class="barra-superior">
      <span class="marca">VoxVault</span>
      <nav class="abas">
        <button
          class="aba"
          aria-current={navegacao.tela === "gravacao" ? "page" : undefined}
          onclick={() => irPara("gravacao")}>Gravação</button
        >
        <button
          class="aba"
          aria-current={navegacao.tela === "biblioteca" ? "page" : undefined}
          onclick={() => irPara("biblioteca")}>Biblioteca</button
        >
        <button
          class="aba"
          aria-current={navegacao.tela === "configuracoes" ? "page" : undefined}
          onclick={() => irPara("configuracoes")}>Configurações</button
        >
      </nav>
      <span style="flex:1"></span>
      {#if gravacao.estado !== "ocioso"}
        <span class="selo erro">
          <i class="ponto"></i>{carimbo(gravacao.decorridoMs)}
        </span>
      {/if}
    </header>

    <main class:sem-padding={navegacao.tela === "biblioteca"} class="corpo">
      {#if navegacao.tela === "gravacao"}
        <Gravacao />
      {:else if navegacao.tela === "biblioteca"}
        <Biblioteca />
      {:else}
        <Configuracoes />
      {/if}
    </main>

    <BarraEstado
      {diagnosticoOk}
      onAbrirDiagnostico={() => {
        irPara("configuracoes");
        queueMicrotask(() =>
          document.getElementById("diagnostico")?.scrollIntoView({ block: "start" }),
        );
      }}
    />
  </div>
{/if}

<Recados />
