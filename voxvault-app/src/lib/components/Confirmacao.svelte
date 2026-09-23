<script lang="ts">
  // A destructive action names exactly what will be lost, in the confirmation
  // itself. "Tem certeza?" is not a confirmation; it is a speed bump.
  //
  // For a destructive one the focus starts on the way out, never on the
  // button that destroys: Enter pressed out of habit must cancel.
  let {
    titulo,
    corpo,
    confirmar = "Confirmar",
    cancelar = "Cancelar",
    perigo = false,
    ocupado = false,
    onConfirmar,
    onCancelar,
  }: {
    titulo: string;
    corpo: string;
    confirmar?: string;
    cancelar?: string;
    perigo?: boolean;
    /** While the confirmed action runs: both buttons wait for it. */
    ocupado?: boolean;
    onConfirmar: () => void;
    onCancelar: () => void;
  } = $props();

  let botaoCancelar = $state<HTMLButtonElement | null>(null);
  let botaoConfirmar = $state<HTMLButtonElement | null>(null);

  $effect(() => {
    (perigo ? botaoCancelar : botaoConfirmar)?.focus();
  });

  function aoTeclar(evento: KeyboardEvent) {
    if (evento.key === "Escape" && !ocupado) onCancelar();
  }
</script>

<svelte:window onkeydown={aoTeclar} />

<div
  class="modal-fundo"
  role="presentation"
  onclick={(e) => {
    if (e.target === e.currentTarget && !ocupado) onCancelar();
  }}
>
  <div class="modal" role="alertdialog" aria-modal="true" aria-label={titulo}>
    <h2 style="margin-bottom:8px">{titulo}</h2>
    <p style="white-space:pre-wrap;color:var(--texto-suave)">{corpo}</p>
    <div class="linha fim" style="margin-top:16px">
      <button class="botao" bind:this={botaoCancelar} disabled={ocupado} onclick={onCancelar}>
        {cancelar}
      </button>
      <button
        class={perigo ? "botao perigo" : "botao primario"}
        bind:this={botaoConfirmar}
        disabled={ocupado}
        onclick={onConfirmar}
      >
        {confirmar}
      </button>
    </div>
  </div>
</div>
