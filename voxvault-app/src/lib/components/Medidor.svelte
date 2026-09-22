<script lang="ts">
  // One track's live level.
  //
  // The bar is driven by `transform: scaleX`, which the compositor handles
  // without a layout pass. At the capped 20 updates per second per track that
  // is the difference between a meter that costs nothing and one that keeps the
  // main thread busy for an hour.
  let {
    rotulo,
    nivel,
    capturando,
    motivo = null,
  }: {
    rotulo: string;
    nivel: number;
    capturando: boolean;
    motivo?: string | null;
  } = $props();

  const mudo = $derived(capturando && nivel < 0.005);
  const alto = $derived(nivel > 0.92);
</script>

<div>
  <div class="linha" style="justify-content:space-between;margin-bottom:5px">
    <span style="font-weight:600;font-size:12.5px">{rotulo}</span>
    {#if capturando}
      <span class="selo ok"><i class="ponto"></i>capturando</span>
    {:else}
      <span class="selo erro"><i class="ponto"></i>não está capturando</span>
    {/if}
  </div>
  <div
    class="medidor"
    class:mudo
    class:alto
    role="meter"
    aria-valuemin="0"
    aria-valuemax="1"
    aria-valuenow={nivel}
    aria-label={`Nível de áudio: ${rotulo}`}
  >
    <i style={`transform: scaleX(${capturando ? nivel : 0})`}></i>
  </div>
  {#if motivo}
    <p class="legenda" style="margin:5px 0 0">{motivo}</p>
  {:else if mudo}
    <p class="legenda" style="margin:5px 0 0">
      Em zero. Se deveria haver som aqui, confira o dispositivo antes de seguir.
    </p>
  {/if}
</div>
