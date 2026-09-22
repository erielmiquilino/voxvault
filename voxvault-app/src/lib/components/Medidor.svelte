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
    disponivel = true,
    silencioHaS = 0,
  }: {
    rotulo: string;
    nivel: number;
    capturando: boolean;
    motivo?: string | null;
    /** False when nobody is publishing levels. A bar sitting at zero because
     *  nobody measured it looks exactly like a muted microphone, and confusing
     *  the two would defeat the one thing this meter exists for. */
    disponivel?: boolean;
    /** Seconds this track has been silent, as the capture counts it. */
    silencioHaS?: number;
  } = $props();

  /** The threshold that separates a quiet room from a dead device. A meeting
   *  has pauses; a minute without a single sample above the floor does not
   *  happen while somebody is talking into a working microphone. */
  const SILENCIO_SUSPEITO_S = 60;

  const mudo = $derived(disponivel && capturando && nivel < 0.005);
  const alto = $derived(disponivel && nivel > 0.92);
  const suspeito = $derived(disponivel && capturando && silencioHaS >= SILENCIO_SUSPEITO_S);

  function duracaoDoSilencio(s: number): string {
    if (s < 60) return `${Math.round(s)} s`;
    const min = Math.floor(s / 60);
    return min < 60 ? `${min} min` : `${Math.floor(min / 60)} h ${min % 60} min`;
  }
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
  {#if disponivel}
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
  {:else}
    <div
      class="medidor"
      style="opacity:.4"
      aria-label={`Nível de áudio indisponível: ${rotulo}`}
    ></div>
  {/if}

  {#if motivo}
    <p class="legenda" style="margin:5px 0 0">{motivo}</p>
  {:else if !disponivel}
    <p class="legenda" style="margin:5px 0 0">
      Nível indisponível: nada está publicando a medição desta trilha. A barra
      vazia aqui não significa microfone mudo.
    </p>
  {:else if suspeito}
    <!-- The whole point of the meter: a track that has been silent for a long
         stretch is the failure this tool exists to catch early. -->
    <p style="margin:5px 0 0;color:var(--gravando);font-size:12.5px;font-weight:600">
      Silêncio há {duracaoDoSilencio(silencioHaS)}. Confira o dispositivo — você
      pode estar gravando silêncio sem perceber.
    </p>
  {:else if mudo}
    <p class="legenda" style="margin:5px 0 0">
      Em zero neste instante. Silêncio há {duracaoDoSilencio(silencioHaS)}.
    </p>
  {/if}
</div>
