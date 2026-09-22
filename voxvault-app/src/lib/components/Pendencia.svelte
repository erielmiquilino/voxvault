<script lang="ts">
  // Renders an obstacle honestly.
  //
  // When the core simply has no machine-readable output for something yet, that
  // is what the user is told -- naming the command and the flag -- instead of a
  // generic error that leaves them guessing whether they broke something.
  import type { Falha } from "../api";

  let { falha, titulo = "Indisponível" }: { falha: Falha; titulo?: string } = $props();
</script>

<div class="nota" class:alerta={!!falha.pendencia} class:erro={!falha.pendencia}>
  <strong>{titulo}</strong>
  <p>{falha.mensagem}</p>
  {#if falha.acao}
    <p class="legenda">{falha.acao}</p>
  {/if}
  {#if falha.pendencia}
    <p class="legenda" style="margin-bottom:6px">
      Falta no núcleo: <code class="mono">{falha.pendencia.sinalizador}</code>
    </p>
  {/if}
</div>
