<script lang="ts">
  // Renders an obstacle honestly: the cause the core gave, in its own words,
  // and the corrective action when there is one. No paraphrase, because a
  // paraphrase of an error is a second story about what went wrong.
  import type { Falha } from "../api";

  let { falha, titulo = "Indisponível" }: { falha: Falha; titulo?: string } = $props();
</script>

<div class="nota erro">
  <strong>{titulo}</strong>
  <p style="white-space:pre-wrap">{falha.mensagem}</p>
  {#if falha.acao}
    <p class="legenda" style="margin:0"><strong>O que fazer:</strong> {falha.acao}</p>
  {/if}
</div>
