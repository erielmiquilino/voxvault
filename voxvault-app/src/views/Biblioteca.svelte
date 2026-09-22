<script lang="ts">
  // The library: finding a meeting again, and reading it.
  //
  // The refresh policy is a budget decision. The list re-reads itself only when
  // something is actually being processed, never while a recording is running,
  // and never while the window is hidden. A list that polls on a timer for an
  // hour is precisely the kind of idle cost this product exists to avoid.
  import { buscar, comoFalha, reunioesListar, type Falha, type Resumo } from "../lib/api";
  import { biblioteca, navegacao, shell } from "../lib/estado.svelte";
  import { dataCurta, duracao, ROTULO_ORIGEM, ROTULO_SITUACAO } from "../lib/format";
  import Pendencia from "../lib/components/Pendencia.svelte";
  import Reuniao from "./Reuniao.svelte";

  const INTERVALO_DE_ATUALIZACAO_MS = 5000;

  let termoDeBusca = $state("");
  let falhaDaBusca = $state<Falha | null>(null);
  let buscando = $state(false);

  const gravacaoAtiva = $derived(shell.servico?.saude?.gravacao_ativa ?? false);

  const visiveis = $derived.by(() => {
    const termo = biblioteca.filtroTermo.trim().toLowerCase();
    const de = biblioteca.filtroDe ? new Date(biblioteca.filtroDe).getTime() : null;
    const ate = biblioteca.filtroAte ? new Date(biblioteca.filtroAte).getTime() + 86_400_000 : null;
    return biblioteca.reunioes.filter((r: Resumo) => {
      if (termo && !r.titulo.toLowerCase().includes(termo)) return false;
      if (biblioteca.filtroSituacao !== "todas" && r.situacao !== biblioteca.filtroSituacao)
        return false;
      if (de !== null || ate !== null) {
        const instante = new Date(r.inicio).getTime();
        if (Number.isNaN(instante)) return false;
        if (de !== null && instante < de) return false;
        if (ate !== null && instante >= ate) return false;
      }
      return true;
    });
  });

  const emProcessamento = $derived(
    biblioteca.reunioes.some((r) => r.situacao === "na_fila" || r.situacao === "gravando"),
  );

  async function carregar() {
    biblioteca.carregando = true;
    try {
      biblioteca.reunioes = await reunioesListar();
    } finally {
      biblioteca.carregando = false;
    }
  }

  $effect(() => {
    void carregar();
  });

  // Progress has to reach the screen without the user reloading anything, but
  // only while there is progress to report.
  $effect(() => {
    if (!emProcessamento || gravacaoAtiva) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) void carregar();
    }, INTERVALO_DE_ATUALIZACAO_MS);
    return () => window.clearInterval(timer);
  });

  async function executarBusca(evento: Event) {
    evento.preventDefault();
    if (!termoDeBusca.trim()) return;
    buscando = true;
    falhaDaBusca = null;
    try {
      await buscar(termoDeBusca.trim());
    } catch (erro) {
      falhaDaBusca = comoFalha(erro);
    } finally {
      buscando = false;
    }
  }

  function abrir(uid: string) {
    navegacao.reuniaoAberta = uid;
    navegacao.focoMs = null;
  }
</script>

<div class="biblioteca">
  <div class="painel-lista">
    <div class="filtros">
      <form onsubmit={executarBusca}>
        <input
          type="search"
          placeholder="Buscar em todo o histórico"
          bind:value={termoDeBusca}
          disabled={buscando}
        />
      </form>
      <input type="text" placeholder="Filtrar por título" bind:value={biblioteca.filtroTermo} />
      <select bind:value={biblioteca.filtroSituacao} aria-label="Filtrar por estado">
        <option value="todas">Todos os estados</option>
        <option value="pronta">Pronta</option>
        <option value="na_fila">Aguardando transcrição</option>
        <option value="gravando">Gravando</option>
        <option value="incompleta">Finalização incompleta</option>
      </select>
      <div class="linha" style="gap:6px">
        <input type="date" bind:value={biblioteca.filtroDe} aria-label="De" style="flex:1" />
        <input type="date" bind:value={biblioteca.filtroAte} aria-label="Até" style="flex:1" />
      </div>
      <div class="linha" style="justify-content:space-between">
        <span class="legenda">{visiveis.length} reunião(ões)</span>
        <button class="botao discreto" onclick={carregar} disabled={biblioteca.carregando}>
          Atualizar
        </button>
      </div>
    </div>

    <div class="lista">
      {#if falhaDaBusca}
        <div style="padding:12px">
          <Pendencia falha={falhaDaBusca} titulo="Busca indisponível" />
        </div>
      {/if}

      {#if biblioteca.carregando && biblioteca.reunioes.length === 0}
        <div class="vazio">Carregando...</div>
      {:else if visiveis.length === 0}
        <div class="vazio">
          {biblioteca.reunioes.length === 0
            ? "Nenhuma reunião guardada ainda."
            : "Nenhuma reunião corresponde aos filtros."}
        </div>
      {:else}
        {#each visiveis as r (r.uid)}
          <button
            class="item"
            aria-current={navegacao.reuniaoAberta === r.uid}
            onclick={() => abrir(r.uid)}
          >
            <div class="titulo">{r.titulo}</div>
            <div class="meta">
              <span>{dataCurta(r.inicio)}</span>
              <span>{duracao(r.duracao_ms)}</span>
              <span class={`selo ${r.situacao === "pronta" ? "ok" : r.situacao === "incompleta" ? "erro" : "acento"}`}>
                {ROTULO_SITUACAO[r.situacao] ?? r.situacao}
              </span>
              <span class="selo">{ROTULO_ORIGEM[r.origem] ?? r.origem}</span>
              {#if r.tem_notas}<span class="selo acento">notas</span>{/if}
              {#if r.avisos.length > 0}
                <span class="selo alerta">{r.avisos.length} aviso(s)</span>
              {/if}
              {#if !r.tem_audio}<span class="selo">sem áudio</span>{/if}
            </div>
          </button>
        {/each}
      {/if}
    </div>
  </div>

  <div class="painel-leitura">
    {#if navegacao.reuniaoAberta}
      <Reuniao uid={navegacao.reuniaoAberta} focoMs={navegacao.focoMs} />
    {:else}
      <div class="vazio">Escolha uma reunião na lista para ler a transcrição.</div>
    {/if}
  </div>
</div>
