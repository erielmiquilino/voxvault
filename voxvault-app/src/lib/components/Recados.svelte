<script lang="ts">
  // Transient messages. Warnings emitted during a recording arrive here and
  // never block: the requirement is explicit that a warning must not interrupt
  // the recording nor demand interaction for it to continue.
  import { recados, dispensarRecado } from "../estado.svelte";
</script>

{#if recados.itens.length > 0}
  <div class="recados" aria-live="polite">
    {#each recados.itens as item (item.id)}
      <div class={`recado ${item.tom}`}>
        <span style="flex:1">{item.texto}</span>
        <button
          class="botao discreto"
          style="padding:0 4px"
          aria-label="Dispensar"
          onclick={() => dispensarRecado(item.id)}>×</button
        >
      </div>
    {/each}
  </div>
{/if}
