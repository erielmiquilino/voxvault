<script lang="ts">
  // A destructive action names exactly what will be lost, in the confirmation
  // itself. "Tem certeza?" is not a confirmation; it is a speed bump.
  let {
    titulo,
    corpo,
    confirmar = "Confirmar",
    cancelar = "Cancelar",
    perigo = false,
    onConfirmar,
    onCancelar,
  }: {
    titulo: string;
    corpo: string;
    confirmar?: string;
    cancelar?: string;
    perigo?: boolean;
    onConfirmar: () => void;
    onCancelar: () => void;
  } = $props();

  function aoTeclar(evento: KeyboardEvent) {
    if (evento.key === "Escape") onCancelar();
  }
</script>

<svelte:window onkeydown={aoTeclar} />

<div
  class="modal-fundo"
  role="presentation"
  onclick={(e) => {
    if (e.target === e.currentTarget) onCancelar();
  }}
>
  <div class="modal" role="alertdialog" aria-modal="true" aria-label={titulo}>
    <h2 style="margin-bottom:8px">{titulo}</h2>
    <p style="white-space:pre-wrap;color:var(--texto-suave)">{corpo}</p>
    <div class="linha fim" style="margin-top:16px">
      <button class="botao" onclick={onCancelar}>{cancelar}</button>
      <button class={perigo ? "botao perigo" : "botao primario"} onclick={onConfirmar}>
        {confirmar}
      </button>
    </div>
  </div>
</div>
