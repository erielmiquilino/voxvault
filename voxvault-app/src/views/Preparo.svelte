<script lang="ts">
  // The gate before the main interface.
  //
  // It exists to keep one specific promise: when the environment is not ready,
  // the application must not open a window whose commands merely look like they
  // work. Everything past this screen assumes the core can be executed.
  import { listen } from "@tauri-apps/api/event";
  import { ambientePreparar, comoFalha, type Ambiente, type Falha } from "../lib/api";
  import Pendencia from "../lib/components/Pendencia.svelte";

  let { ambiente, onPronto }: { ambiente: Ambiente; onPronto: () => void } = $props();

  let preparando = $state(false);
  let linhas = $state<string[]>([]);
  let falha = $state<Falha | null>(null);

  $effect(() => {
    const parar = listen<string>("ambiente://progresso", (evento) => {
      // Only the tail is kept: the whole log of a dependency resolution is
      // thousands of lines, and holding it would be memory spent on noise.
      linhas = [...linhas.slice(-40), evento.payload];
    });
    return () => {
      parar.then((cancelar) => cancelar());
    };
  });

  async function preparar() {
    preparando = true;
    falha = null;
    linhas = [];
    try {
      await ambientePreparar();
      onPronto();
    } catch (erro) {
      falha = comoFalha(erro);
    } finally {
      preparando = false;
    }
  }
</script>

<div class="corpo" style="display:grid;place-items:center">
  <div class="coluna" style="max-width:640px">
    <div class="cartao">
      <h1 style="margin-bottom:10px">Preparar o VoxVault</h1>
      <p style="color:var(--texto-suave)">{ambiente.detalhe}</p>

      {#if !preparando && !falha}
        <p class="legenda">
          O interpretador, as bibliotecas de áudio e o runtime de inferência são
          baixados uma única vez, agora. Eles não vêm dentro do aplicativo: um
          instalável com tudo isso teria vários gigabytes, quebraria com
          frequência e dispararia falso-positivo de antivírus.
        </p>
        <p class="legenda">Este passo exige conexão e leva alguns minutos.</p>
        <div class="linha" style="margin-top:14px">
          <button class="botao primario grande" onclick={preparar}>
            Preparar o ambiente
          </button>
        </div>
      {/if}

      {#if preparando}
        <p class="legenda">Preparando. Você pode acompanhar o progresso abaixo.</p>
        <pre class="trecho" style="max-height:260px;white-space:pre-wrap">{linhas.length
            ? linhas.join("\n")
            : "Iniciando..."}</pre>
      {/if}

      {#if falha}
        <Pendencia {falha} titulo="O preparo do ambiente falhou" />
        {#if linhas.length}
          <details style="margin-top:10px">
            <summary class="legenda" style="cursor:pointer">Ver a saída completa</summary>
            <pre class="trecho" style="max-height:220px;white-space:pre-wrap;margin-top:8px">{linhas.join(
                "\n",
              )}</pre>
          </details>
        {/if}
        <div class="linha" style="margin-top:14px">
          <button class="botao primario" onclick={preparar}>Tentar novamente</button>
        </div>
      {/if}
    </div>
  </div>
</div>
