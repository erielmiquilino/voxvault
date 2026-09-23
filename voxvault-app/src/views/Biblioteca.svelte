<script lang="ts">
  // The library: finding a meeting again, and reading it.
  //
  // The list is the core's answer, not a reconstruction. It reports whether a
  // transcript is available and what the current attempt is doing as two
  // separate attributes, so a failed transcription is shown as failed and a
  // meeting being reprocessed is shown as still readable -- neither of which a
  // single status string could express.
  //
  // The refresh policy is a budget decision: the list re-reads itself only
  // while something is actually being processed, never during a recording, and
  // never while the window is hidden.
  //
  // Selection mode deletes several meetings at once. Its sums -- duration and
  // space -- are the core's preview of exactly those meetings, not a sum made
  // here, so the figure confirmed is the figure the core then frees.
  import { tick } from "svelte";

  import {
    buscar,
    comoFalha,
    reunioesExcluir,
    reunioesExcluirPrevia,
    reunioesListar,
    type EscopoDeBusca,
    type Exclusao,
    type Falha,
    type ItemDaExclusao,
    type ResultadoDeBusca,
    type Resumo,
  } from "../lib/api";
  import { biblioteca, navegacao, recado, shell } from "../lib/estado.svelte";
  import { corpoDeVarias, motivoLegivel, motivoParaNaoExcluir } from "../lib/exclusao";
  import {
    bytes,
    carimbo,
    dataCurta,
    duracao,
    ROTULO_ORIGEM,
    ROTULO_SITUACAO,
    tomDaSituacao,
  } from "../lib/format";
  import Confirmacao from "../lib/components/Confirmacao.svelte";
  import Pendencia from "../lib/components/Pendencia.svelte";
  import Reuniao from "./Reuniao.svelte";

  const INTERVALO_DE_ATUALIZACAO_MS = 5000;

  let termoDeBusca = $state("");
  let escopo = $state<EscopoDeBusca>("ambos");
  let resultados = $state<ResultadoDeBusca[] | null>(null);
  let falhaDaBusca = $state<Falha | null>(null);
  let falhaDaLista = $state<Falha | null>(null);
  let buscando = $state(false);

  const gravacaoAtiva = $derived(shell.servico?.saude?.gravacao_ativa ?? false);

  const porUid = $derived(
    new Map(biblioteca.reunioes.map((r: Resumo) => [r.uid, r])),
  );
  const aberta = $derived(
    navegacao.reuniaoAberta ? (porUid.get(navegacao.reuniaoAberta) ?? null) : null,
  );

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
    biblioteca.reunioes.some(
      (r) => r.situacao === "na_fila" || r.situacao === "transcrevendo" || r.situacao === "gravando",
    ),
  );

  export async function carregar() {
    biblioteca.carregando = true;
    try {
      biblioteca.reunioes = await reunioesListar();
      falhaDaLista = null;
    } catch (erro) {
      falhaDaLista = comoFalha(erro);
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

  // -- selection and deletion ------------------------------------------------

  let selecionando = $state(false);
  let marcadas = $state<string[]>([]);
  let previaDaSelecao = $state<Exclusao | null>(null);
  let calculando = $state(false);
  let confirmacaoDoLote = $state<{ corpo: string; uids: string[] } | null>(null);
  let excluindoLote = $state(false);
  let recusas = $state<ItemDaExclusao[]>([]);
  let pedidoDePrevia = 0;

  /** Marks on meetings that still exist: one deleted elsewhere drops out. */
  const marcadasExistentes = $derived(marcadas.filter((uid) => porUid.has(uid)));
  // A string, so the preview below re-runs when the selection changes and not
  // every time the periodic refresh hands back an equal list.
  const chaveDaSelecao = $derived(marcadasExistentes.join(","));

  function entrarNaSelecao() {
    selecionando = true;
    recusas = [];
  }

  function sairDaSelecao() {
    selecionando = false;
    marcadas = [];
    previaDaSelecao = null;
  }

  function alternar(uid: string) {
    marcadas = marcadas.includes(uid) ? marcadas.filter((u) => u !== uid) : [...marcadas, uid];
  }

  function marcarVisiveis() {
    const novas = visiveis.filter((r) => !motivoParaNaoExcluir(r)).map((r) => r.uid);
    marcadas = [...new Set([...marcadas, ...novas])];
  }

  $effect(() => {
    const chave = chaveDaSelecao;
    if (!selecionando || !chave) {
      previaDaSelecao = null;
      calculando = false;
      return;
    }
    calculando = true;
    const pedido = ++pedidoDePrevia;
    const uids = chave.split(",");
    // Clicking through a few boxes asks the core once, not once per click.
    const espera = window.setTimeout(async () => {
      try {
        const previa = await reunioesExcluirPrevia(uids);
        if (pedido === pedidoDePrevia) previaDaSelecao = previa;
      } catch (erro) {
        if (pedido === pedidoDePrevia) {
          previaDaSelecao = null;
          recado("erro", comoFalha(erro).mensagem);
        }
      } finally {
        if (pedido === pedidoDePrevia) calculando = false;
      }
    }, 250);
    return () => window.clearTimeout(espera);
  });

  function aoTeclar(evento: KeyboardEvent) {
    // A dialog open on top owns Escape; only a bare selection mode leaves.
    if (evento.key !== "Escape" || !selecionando) return;
    if (document.querySelector(".modal-fundo")) return;
    sairDaSelecao();
  }

  async function pedirExclusaoDoLote() {
    const uids = marcadasExistentes;
    if (uids.length === 0) return;
    let previa: Exclusao;
    try {
      previa = await reunioesExcluirPrevia(uids);
    } catch (erro) {
      recado("erro", comoFalha(erro).mensagem);
      return;
    }
    previaDaSelecao = previa;
    if (previa.total.reunioes === 0) {
      recusas = previa.itens.filter((item) => item.motivo);
      recado("erro", "Nenhuma das reuniões marcadas pode ser excluída agora.");
      return;
    }
    confirmacaoDoLote = { corpo: corpoDeVarias(previa), uids };
  }

  async function excluirLote() {
    if (!confirmacaoDoLote) return;
    const uids = confirmacaoDoLote.uids;
    excluindoLote = true;
    // The open meeting's player holds its audio files open. Leaving it first
    // takes the player out of the page, so the webview lets go of them.
    if (navegacao.reuniaoAberta && uids.includes(navegacao.reuniaoAberta)) {
      navegacao.reuniaoAberta = null;
      await tick();
    }
    try {
      let itens = (await reunioesExcluir(uids)).itens;
      const emUso = itens.filter((item) => item.causa === "em_uso").map((item) => item.uid);
      if (emUso.length > 0) {
        await new Promise((pronto) => setTimeout(pronto, 500));
        const segunda = new Map((await reunioesExcluir(emUso)).itens.map((i) => [i.uid, i]));
        itens = itens.map((item) => segunda.get(item.uid) ?? item);
      }
      const excluidas = itens.filter((item) => item.resultado === "excluida");
      recusas = itens.filter((item) => item.resultado !== "excluida");
      if (excluidas.length > 0) {
        const liberados = excluidas.reduce((soma, item) => soma + item.bytes, 0);
        recado(
          "ok",
          `${excluidas.length === 1 ? "1 reunião excluída" : `${excluidas.length} reuniões excluídas`}. ` +
            `${bytes(liberados)} liberados.`,
        );
      }
      sairDaSelecao();
      await carregar();
    } catch (erro) {
      recado("erro", comoFalha(erro).mensagem);
    } finally {
      excluindoLote = false;
      confirmacaoDoLote = null;
    }
  }

  /** The reading view deleted its meeting: leave it and re-ask the core. */
  async function aoExcluir(uid: string) {
    if (navegacao.reuniaoAberta === uid) navegacao.reuniaoAberta = null;
    marcadas = marcadas.filter((u) => u !== uid);
    await carregar();
  }

  async function executarBusca(evento: Event) {
    evento.preventDefault();
    sairDaSelecao();
    if (!termoDeBusca.trim()) {
      resultados = null;
      return;
    }
    buscando = true;
    falhaDaBusca = null;
    try {
      const resposta = await buscar(termoDeBusca.trim(), escopo);
      resultados = resposta.resultados;
      if (resultados.length === 0) recado("info", "Nada encontrado para esse termo.");
    } catch (erro) {
      falhaDaBusca = comoFalha(erro);
    } finally {
      buscando = false;
    }
  }

  function abrir(uid: string, focoMs: number | null = null) {
    navegacao.reuniaoAberta = uid;
    navegacao.focoMs = focoMs;
  }

  /** Opening a search result positions the reading view at the hit. A note hit
   *  has no instant, so it opens the meeting without pretending to have one. */
  function abrirResultado(resultado: ResultadoDeBusca) {
    abrir(resultado.reuniao, resultado.inicio_ms ?? null);
    if (resultado.natureza === "nota") {
      recado("info", "A nota está na área de notas, abaixo da linha do tempo.");
    }
  }
</script>

<svelte:window onkeydown={aoTeclar} />

{#snippet conteudoDoItem(r: Resumo)}
  <div class="titulo">{r.titulo}</div>
  <div class="meta">
    <span>{dataCurta(r.inicio)}</span>
    <span>{duracao(r.duracao_ms)}</span>
    <span class={`selo ${tomDaSituacao(r.situacao)}`}>
      {ROTULO_SITUACAO[r.situacao] ?? r.situacao}
    </span>
    <!-- Availability is shown alongside the attempt, never folded
         into it: a meeting being reprocessed is readable right now. -->
    {#if r.transcricao_disponivel && r.situacao !== "pronta"}
      <span class="selo ok">transcrição legível</span>
    {/if}
    {#if r.completude === "parcial"}<span class="selo alerta">parcial</span>{/if}
    <span class="selo">{ROTULO_ORIGEM[r.origem] ?? r.origem}</span>
    {#if r.tem_notas}<span class="selo acento">notas</span>{/if}
    {#if r.avisos.length > 0}
      <span class="selo alerta">{r.avisos.length} aviso(s)</span>
    {/if}
    {#if !r.tem_audio}<span class="selo">sem áudio</span>{/if}
  </div>
  {#if r.motivo_da_falha}
    <div class="legenda" style="margin-top:4px;color:var(--gravando)">
      {r.motivo_da_falha}
    </div>
  {/if}
{/snippet}

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
      <div class="trilha-escolha">
        {#each [["ambos", "Tudo"], ["transcricoes", "Transcrições"], ["notas", "Notas"]] as [valor, rotulo] (valor)}
          <button
            type="button"
            aria-pressed={escopo === valor}
            onclick={() => {
              escopo = valor as EscopoDeBusca;
              if (termoDeBusca.trim()) void executarBusca(new Event("submit"));
            }}>{rotulo}</button
          >
        {/each}
      </div>

      {#if resultados === null}
        <input type="text" placeholder="Filtrar por título" bind:value={biblioteca.filtroTermo} />
        <select bind:value={biblioteca.filtroSituacao} aria-label="Filtrar por estado">
          <option value="todas">Todos os estados</option>
          <option value="pronta">Pronta</option>
          <option value="na_fila">Aguardando transcrição</option>
          <option value="transcrevendo">Transcrevendo</option>
          <option value="falhou">Falhou</option>
          <option value="gravando">Gravando</option>
          <option value="incompleta">Finalização incompleta</option>
        </select>
        <div class="linha" style="gap:6px">
          <input type="date" bind:value={biblioteca.filtroDe} aria-label="De" style="flex:1" />
          <input type="date" bind:value={biblioteca.filtroAte} aria-label="Até" style="flex:1" />
        </div>
      {:else}
        <div class="linha" style="justify-content:space-between">
          <span class="legenda">{resultados.length} resultado(s)</span>
          <button
            class="botao discreto"
            onclick={() => {
              resultados = null;
              termoDeBusca = "";
            }}>Voltar à lista</button
          >
        </div>
      {/if}

      {#if resultados === null}
        <div class="linha" style="justify-content:space-between">
          <span class="legenda">{visiveis.length} reunião(ões)</span>
          <div class="linha" style="gap:4px">
            {#if !selecionando}
              <button
                class="botao discreto"
                onclick={entrarNaSelecao}
                disabled={biblioteca.reunioes.length === 0}
                title="Marcar várias reuniões para excluí-las de uma vez"
              >
                Selecionar
              </button>
            {/if}
            <button class="botao discreto" onclick={carregar} disabled={biblioteca.carregando}>
              Atualizar
            </button>
          </div>
        </div>
      {/if}
    </div>

    <div class="lista">
      {#if falhaDaBusca}
        <div style="padding:12px"><Pendencia falha={falhaDaBusca} titulo="Busca indisponível" /></div>
      {/if}
      {#if falhaDaLista}
        <div style="padding:12px"><Pendencia falha={falhaDaLista} titulo="Lista indisponível" /></div>
      {/if}
      {#if recusas.length > 0}
        <div class="nota erro" style="margin:12px">
          <strong>
            {recusas.length === 1
              ? "Uma reunião não foi excluída"
              : `${recusas.length} reuniões não foram excluídas`}
          </strong>
          <ul style="margin:6px 0 8px;padding-left:18px">
            {#each recusas as item (item.uid)}
              <li><strong>{item.titulo || item.uid}</strong> — {motivoLegivel(item)}</li>
            {/each}
          </ul>
          <button class="botao discreto" onclick={() => (recusas = [])}>Entendi</button>
        </div>
      {/if}

      {#if resultados !== null}
        {#if resultados.length === 0}
          <div class="vazio">Nenhum resultado.</div>
        {:else}
          {#each resultados as r, i (`${r.reuniao}-${r.nota ?? r.inicio_ms ?? i}`)}
            <button class="item" onclick={() => abrirResultado(r)}>
              <div class="titulo">{r.titulo}</div>
              <div class="meta" style="margin-bottom:4px">
                {#if r.natureza === "nota"}
                  <span class="selo acento">nota{r.tipo ? ` · ${r.tipo}` : ""}</span>
                {:else}
                  <span class="selo">transcrição</span>
                  {#if r.inicio_ms !== undefined}
                    <span class="mono">{carimbo(r.inicio_ms)}</span>
                  {/if}
                {/if}
              </div>
              <div style="font-size:12.5px;color:var(--texto-suave)">{r.recorte}</div>
            </button>
          {/each}
        {/if}
      {:else if biblioteca.carregando && biblioteca.reunioes.length === 0}
        <div class="vazio">Carregando...</div>
      {:else if visiveis.length === 0}
        <div class="vazio">
          {biblioteca.reunioes.length === 0
            ? "Nenhuma reunião guardada ainda."
            : "Nenhuma reunião corresponde aos filtros."}
        </div>
      {:else}
        {#each visiveis as r (r.uid)}
          {#if selecionando}
            {@const bloqueio = motivoParaNaoExcluir(r)}
            {@const marcada = marcadas.includes(r.uid)}
            <!-- A meeting that cannot be deleted now cannot be marked; one
                 marked before it started transcribing can still be unmarked. -->
            <label
              class="item selecionavel"
              class:bloqueado={!!bloqueio && !marcada}
              title={bloqueio ?? ""}
            >
              <input
                type="checkbox"
                checked={marcada}
                disabled={!!bloqueio && !marcada}
                onchange={() => alternar(r.uid)}
                aria-label={`Marcar ${r.titulo}`}
              />
              <div class="corpo-do-item">
                {@render conteudoDoItem(r)}
                {#if bloqueio}
                  <div class="legenda" style="margin-top:4px">{bloqueio}</div>
                {/if}
              </div>
            </label>
          {:else}
            <button
              class="item"
              aria-current={navegacao.reuniaoAberta === r.uid}
              onclick={() => abrir(r.uid)}
            >
              {@render conteudoDoItem(r)}
            </button>
          {/if}
        {/each}
      {/if}
    </div>

    {#if selecionando}
      <div class="barra-selecao">
        <div class="linha" style="justify-content:space-between">
          <button class="botao discreto" onclick={marcarVisiveis}>Marcar todas as visíveis</button>
          <button class="botao discreto" onclick={sairDaSelecao}>Cancelar</button>
        </div>
        <div class="linha" style="justify-content:space-between">
          <span class="legenda" aria-live="polite">
            {marcadasExistentes.length === 1 ? "1 selecionada" : `${marcadasExistentes.length} selecionadas`}
            {#if marcadasExistentes.length > 0}
              · {calculando || !previaDaSelecao
                ? "calculando..."
                : `${duracao(previaDaSelecao.total.duracao_ms)} · ${bytes(previaDaSelecao.total.bytes)}`}
            {/if}
          </span>
          <button
            class="botao perigo"
            disabled={marcadasExistentes.length === 0 || excluindoLote}
            onclick={pedirExclusaoDoLote}
          >
            Excluir
          </button>
        </div>
      </div>
    {/if}
  </div>

  <div class="painel-leitura">
    {#if aberta}
      <Reuniao
        resumo={aberta}
        focoMs={navegacao.focoMs}
        onMudou={carregar}
        onExcluida={aoExcluir}
      />
    {:else if navegacao.reuniaoAberta}
      <div class="vazio">
        Esta reunião não está mais na lista. Atualize para recarregar.
      </div>
    {:else}
      <div class="vazio">Escolha uma reunião na lista para ler a transcrição.</div>
    {/if}
  </div>
</div>

{#if confirmacaoDoLote}
  <Confirmacao
    titulo="Excluir as reuniões selecionadas definitivamente?"
    corpo={confirmacaoDoLote.corpo}
    confirmar={excluindoLote ? "Excluindo..." : "Excluir definitivamente"}
    perigo
    ocupado={excluindoLote}
    onConfirmar={excluirLote}
    onCancelar={() => (confirmacaoDoLote = null)}
  />
{/if}
