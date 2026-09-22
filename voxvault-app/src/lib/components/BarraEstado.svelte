<script lang="ts">
  // Permanent, discreet, and never hidden: the state of the resident service
  // and the result of the environment diagnostic. An interface that only tells
  // you the service is down at the moment you press record has already wasted
  // the meeting.
  import { shell } from "../estado.svelte";
  import { servicoRearmar } from "../api";
  import { recado } from "../estado.svelte";

  let { diagnosticoOk, onAbrirDiagnostico }: {
    diagnosticoOk: boolean | null;
    onAbrirDiagnostico: () => void;
  } = $props();

  const servico = $derived(shell.servico);

  const rotulo = $derived.by(() => {
    switch (servico?.estado) {
      case "conectado":
        return "Serviço em execução";
      case "procurando":
        return "Procurando o serviço...";
      case "indisponivel":
        return "Serviço indisponível";
      case "falho":
        return "Serviço em falha";
      default:
        return "Verificando o serviço...";
    }
  });

  const tom = $derived.by(() => {
    switch (servico?.estado) {
      case "conectado":
        return "ok";
      case "falho":
        return "erro";
      case "indisponivel":
        return "alerta";
      default:
        return "";
    }
  });

  let rearmando = $state(false);

  async function tentarDeNovo() {
    rearmando = true;
    try {
      shell.servico = await servicoRearmar();
      recado("info", "Nova tentativa de alcançar o serviço local.");
    } finally {
      rearmando = false;
    }
  }
</script>

<footer class="barra-estado">
  <span class={`selo ${tom}`} title={servico?.detalhe ?? ""}>
    <i class="ponto"></i>{rotulo}
  </span>

  {#if servico?.saude}
    {#if servico.saude.gravacao_ativa}
      <span class="selo erro"><i class="ponto"></i>gravando</span>
    {/if}
    {#if servico.saude.fila_pendente > 0}
      <span class="selo acento">
        {servico.saude.fila_pendente} na fila de transcrição
      </span>
    {/if}
  {/if}

  {#if servico?.pode_tentar_de_novo}
    <button class="botao discreto" onclick={tentarDeNovo} disabled={rearmando}>
      Tentar novamente
    </button>
  {/if}

  <span class="separador"></span>

  <button class="botao discreto" onclick={onAbrirDiagnostico}>
    {#if diagnosticoOk === null}
      Diagnóstico
    {:else if diagnosticoOk}
      <span class="selo ok"><i class="ponto"></i>Ambiente em ordem</span>
    {:else}
      <span class="selo alerta"><i class="ponto"></i>Ambiente com pendências</span>
    {/if}
  </button>
</footer>
