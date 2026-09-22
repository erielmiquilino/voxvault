<script lang="ts">
  import { listen } from "@tauri-apps/api/event";
  import { getCurrentWindow } from "@tauri-apps/api/window";

  import {
    ambienteEstado,
    diagnosticoDoAplicativo,
    gravacaoEncerrar,
    servicoEstado,
    type ServicoSnapshot,
  } from "./lib/api";
  import { gravacao, navegacao, recado, shell, pararRelogio, zerarGravacao } from "./lib/estado.svelte";
  import { carimbo } from "./lib/format";
  import BarraEstado from "./lib/components/BarraEstado.svelte";
  import Confirmacao from "./lib/components/Confirmacao.svelte";
  import Recados from "./lib/components/Recados.svelte";
  import Biblioteca from "./views/Biblioteca.svelte";
  import Configuracoes from "./views/Configuracoes.svelte";
  import Gravacao from "./views/Gravacao.svelte";
  import Preparo from "./views/Preparo.svelte";

  let diagnosticoOk = $state<boolean | null>(null);
  let pedidoDeFechamento = $state<{ gravando: boolean; fila_pendente: number } | null>(null);
  let encerrando = $state(false);

  async function carregarShell() {
    shell.ambiente = await ambienteEstado();
    shell.servico = await servicoEstado();
    shell.carregado = true;
    if (shell.ambiente.preparado) {
      const itens = await diagnosticoDoAplicativo();
      diagnosticoOk = itens.every((item) => item.ok);
    }
  }

  $effect(() => {
    void carregarShell();
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
      listen<string>("atalho://gravacao", (evento) => recado("ok", evento.payload)),
      listen<string>("atalho://falhou", (evento) => recado("erro", evento.payload)),
      listen<string>("atalho://indisponivel", (evento) => recado("info", evento.payload)),
      listen<string>("servico://desistiu", (evento) => recado("erro", evento.payload)),
      listen("servico://conectado", () => {
        void diagnosticoDoAplicativo().then((itens) => {
          diagnosticoOk = itens.every((item) => item.ok);
        });
      }),
      listen<{ gravando: boolean; fila_pendente: number }>(
        "app://fechamento-solicitado",
        (evento) => {
          if (evento.payload.gravando) {
            pedidoDeFechamento = evento.payload;
          } else {
            // No recording and nothing this window owns: the service ends
            // itself by its idle policy, so there is nothing to kill.
            void getCurrentWindow().destroy();
          }
        },
      ),
    ];
    return () => {
      for (const assinatura of assinaturas) assinatura.then((cancelar) => cancelar());
    };
  });

  async function confirmarFechamento() {
    encerrando = true;
    try {
      await gravacaoEncerrar();
    } catch (erro) {
      // Even a stop that failed must not become a silent kill of the service;
      // the user is told and the window stays open.
      recado("erro", `Não foi possível encerrar a gravação: ${String(erro)}`);
      encerrando = false;
      pedidoDeFechamento = null;
      return;
    }
    await getCurrentWindow().destroy();
  }

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

{#if pedidoDeFechamento}
  <Confirmacao
    titulo="Há uma gravação em andamento"
    corpo={`A reunião está sendo gravada há ${carimbo(gravacao.decorridoMs)}.\n\n` +
      `Ao confirmar, a gravação é encerrada de forma limpa — o áudio é finalizado e a sessão é submetida para transcrição — e só então o aplicativo fecha.\n\n` +
      (pedidoDeFechamento.fila_pendente > 0
        ? `As ${pedidoDeFechamento.fila_pendente} sessão(ões) na fila continuam sendo transcritas com a janela fechada.`
        : `O serviço continua em execução até concluir a transcrição e depois se encerra sozinho.`)}
    confirmar={encerrando ? "Encerrando..." : "Encerrar e fechar"}
    cancelar="Continuar gravando"
    perigo
    onConfirmar={confirmarFechamento}
    onCancelar={() => (pedidoDeFechamento = null)}
  />
{/if}
