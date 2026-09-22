<script lang="ts">
  // Reading one meeting: the second operation the terminal gets in the way of.
  //
  // The notes area is deliberately separate from the timeline and never
  // interleaved with it. A summary produced by a model can be wrong, and the
  // distinction between "this was said" and "this was interpreted" has to
  // survive a hurried reading months later.
  import { convertFileSrc } from "@tauri-apps/api/core";

  import {
    comoFalha,
    reuniaoAbrirPasta,
    reuniaoDetalhar,
    reuniaoExportar,
    reuniaoRemoverAudio,
    reuniaoRenomear,
    reuniaoReprocessar,
    notasListar,
    type Detalhe,
    type Falha,
  } from "../lib/api";
  import { recado, shell } from "../lib/estado.svelte";
  import { carimbo, dataHora, duracao, falante, papelDoFalante, ROTULO_ORIGEM, ROTULO_SITUACAO } from "../lib/format";
  import Confirmacao from "../lib/components/Confirmacao.svelte";
  import Pendencia from "../lib/components/Pendencia.svelte";

  let { uid, focoMs = null }: { uid: string; focoMs?: number | null } = $props();

  let detalhe = $state<Detalhe | null>(null);
  let falha = $state<Falha | null>(null);
  let falhaNotas = $state<Falha | null>(null);
  let carregando = $state(false);

  let trilhaEscolhida = $state<"mic" | "system" | "ambas">("ambas");
  let posicaoMs = $state(0);
  let tocando = $state(false);
  let elementos = $state<Record<string, HTMLAudioElement | null>>({});
  let selecionados = $state<number[]>([]);
  let confirmacao = $state<null | { titulo: string; corpo: string; ao: () => void }>(null);
  let renomeando = $state(false);
  let novoTitulo = $state("");

  // A recording in progress is captured from the output device, so playing an
  // old meeting now would be folded into it as if it were a participant. The
  // restriction comes from the capture, not from a choice of interface.
  const gravacaoAtiva = $derived(shell.servico?.saude?.gravacao_ativa ?? false);

  const trilhasDisponiveis = $derived(detalhe?.trilhas ?? []);
  const temAudio = $derived(trilhasDisponiveis.length > 0);

  const motivoSemReproducao = $derived.by(() => {
    if (gravacaoAtiva) {
      return "Há uma gravação em andamento. Reproduzir agora incorporaria este áudio à gravação em curso pela trilha do sistema, como se fosse fala de participante.";
    }
    if (!temAudio) {
      return "O áudio desta reunião foi removido. A transcrição continua legível, mas não há o que reproduzir.";
    }
    return null;
  });

  const trilhasParaTocar = $derived.by(() => {
    if (trilhaEscolhida === "ambas") return trilhasDisponiveis;
    return trilhasDisponiveis.filter((t) => t.nome === trilhaEscolhida);
  });

  const segmentoAtual = $derived.by(() => {
    const segmentos = detalhe?.segmentos ?? [];
    for (let i = segmentos.length - 1; i >= 0; i -= 1) {
      if (segmentos[i].inicio_ms <= posicaoMs) return segmentos[i].id;
    }
    return null;
  });

  $effect(() => {
    void carregar(uid);
  });

  async function carregar(alvo: string) {
    carregando = true;
    falha = null;
    detalhe = null;
    selecionados = [];
    posicaoMs = 0;
    tocando = false;
    try {
      detalhe = await reuniaoDetalhar(alvo);
      novoTitulo = detalhe.resumo.titulo;
      trilhaEscolhida = detalhe.trilhas.length > 1 ? "ambas" : ((detalhe.trilhas[0]?.nome as "mic" | "system") ?? "ambas");
    } catch (erro) {
      falha = comoFalha(erro);
    } finally {
      carregando = false;
    }
    try {
      await notasListar(alvo);
      falhaNotas = null;
    } catch (erro) {
      falhaNotas = comoFalha(erro);
    }
  }

  // Position the reading view at a specific moment, which is what a search
  // result or a warning with a timestamp asks for.
  $effect(() => {
    if (focoMs === null || !detalhe) return;
    posicaoMs = focoMs;
    const alvo = document.querySelector<HTMLElement>(`[data-instante="${segmentoAtual}"]`);
    alvo?.scrollIntoView({ block: "center" });
  });

  function irPara(ms: number) {
    posicaoMs = ms;
    for (const elemento of Object.values(elementos)) {
      if (elemento) elemento.currentTime = ms / 1000;
    }
  }

  function acionarSegmento(id: number, inicioMs: number, evento: MouseEvent) {
    if (evento.shiftKey && selecionados.length > 0) {
      const segmentos = detalhe?.segmentos ?? [];
      const de = segmentos.findIndex((s) => s.id === selecionados[0]);
      const ate = segmentos.findIndex((s) => s.id === id);
      if (de >= 0 && ate >= 0) {
        const [inicio, fim] = de <= ate ? [de, ate] : [ate, de];
        selecionados = segmentos.slice(inicio, fim + 1).map((s) => s.id);
      }
      return;
    }
    selecionados = [id];
    if (!motivoSemReproducao) irPara(inicioMs);
  }

  async function copiarSelecao() {
    const segmentos = (detalhe?.segmentos ?? []).filter((s) => selecionados.includes(s.id));
    if (segmentos.length === 0) return;
    const texto = segmentos
      .map((s) => `[${carimbo(s.inicio_ms)}] ${falante(s.falante, s.trilha)}: ${s.texto}`)
      .join("\n");
    await navigator.clipboard.writeText(texto);
    recado("ok", `${segmentos.length} trecho(s) copiado(s) com instante e falante.`);
  }

  function alternarReproducao() {
    const alvos = trilhasParaTocar
      .map((t) => elementos[t.nome])
      .filter((e): e is HTMLAudioElement => !!e);
    if (alvos.length === 0) return;
    if (tocando) {
      for (const alvo of alvos) alvo.pause();
      tocando = false;
    } else {
      for (const alvo of alvos) {
        alvo.currentTime = posicaoMs / 1000;
        void alvo.play();
      }
      tocando = true;
    }
  }

  // One element drives the clock; the others follow it. Reading `currentTime`
  // from every element on every tick would multiply the work for nothing.
  function aoAvancar(evento: Event) {
    const alvo = evento.currentTarget as HTMLAudioElement;
    posicaoMs = alvo.currentTime * 1000;
  }

  async function executar(acao: () => Promise<unknown>, sucesso: string) {
    try {
      await acao();
      recado("ok", sucesso);
      await carregar(uid);
    } catch (erro) {
      const f = comoFalha(erro);
      recado("erro", f.mensagem);
    }
  }

  function pedirRemocaoDeAudio() {
    confirmacao = {
      titulo: "Remover o áudio desta reunião?",
      corpo:
        "Os arquivos de áudio serão apagados do disco em definitivo. A transcrição já produzida continua legível e pesquisável.\n\n" +
        "Depois disso a reunião não poderá mais ser reprocessada: reprocessar exige o áudio original, e ele não existirá mais.",
      ao: () => {
        confirmacao = null;
        void executar(() => reuniaoRemoverAudio(uid), "Áudio removido.");
      },
    };
  }
</script>

{#if carregando}
  <div class="vazio">Carregando a reunião...</div>
{:else if falha}
  <Pendencia {falha} titulo="Não foi possível abrir a reunião" />
{:else if detalhe}
  {@const r = detalhe.resumo}

  <header style="margin-bottom:16px">
    {#if renomeando}
      <div class="linha" style="margin-bottom:8px">
        <input type="text" bind:value={novoTitulo} style="flex:1;min-width:240px" />
        <button
          class="botao primario"
          onclick={() =>
            executar(() => reuniaoRenomear(uid, novoTitulo), "Reunião renomeada.").then(
              () => (renomeando = false),
            )}
        >
          Gravar
        </button>
        <button class="botao" onclick={() => (renomeando = false)}>Cancelar</button>
      </div>
    {:else}
      <h1 style="margin-bottom:6px">{r.titulo}</h1>
    {/if}

    <div class="linha legenda">
      <span>{dataHora(r.inicio)}</span>
      <span>·</span>
      <span>{duracao(r.duracao_ms)}</span>
      <span>·</span>
      <span class="selo">{ROTULO_ORIGEM[r.origem] ?? r.origem}</span>
      <span class={`selo ${r.situacao === "pronta" ? "ok" : r.situacao === "incompleta" ? "erro" : "acento"}`}>
        {ROTULO_SITUACAO[r.situacao] ?? r.situacao}
      </span>
      {#if r.motor}<span class="selo">{r.motor}</span>{/if}
    </div>

    <div class="linha" style="margin-top:12px">
      <button class="botao discreto" onclick={() => (renomeando = true)}>Renomear</button>
      <button
        class="botao discreto"
        disabled={!r.tem_audio}
        title={r.tem_audio
          ? "Enfileira uma nova transcrição; a atual continua legível até a nova ser publicada."
          : "Reprocessar exige o áudio original, que foi removido desta reunião."}
        onclick={() => executar(() => reuniaoReprocessar(uid), "Enfileirada para nova transcrição.")}
      >
        Reprocessar
      </button>
      <button
        class="botao discreto"
        disabled={r.situacao !== "pronta"}
        title={r.situacao === "pronta" ? "" : "A reunião ainda não tem transcrição publicada."}
        onclick={() => executar(() => reuniaoExportar(uid), "Exportações regeneradas.")}
      >
        Exportar
      </button>
      <button class="botao discreto" onclick={() => reuniaoAbrirPasta(uid)}>
        Abrir a pasta
      </button>
      <button class="botao discreto" disabled={!r.tem_audio} onclick={pedirRemocaoDeAudio}>
        Remover o áudio
      </button>
    </div>
  </header>

  {#if r.avisos.length > 0}
    <div class="nota alerta" style="margin-bottom:14px">
      <strong>Avisos registrados na gravação</strong>
      <ul style="margin:6px 0 0;padding-left:18px">
        {#each r.avisos as aviso}
          <li>{aviso}</li>
        {/each}
      </ul>
    </div>
  {/if}

  <!-- Player -->
  <div class="reprodutor">
    {#if motivoSemReproducao}
      <div class="nota alerta" style="margin:0">
        <strong>Reprodução indisponível</strong>
        <p style="margin:0">{motivoSemReproducao}</p>
      </div>
    {:else}
      <div class="linha">
        <button class="botao primario" onclick={alternarReproducao}>
          {tocando ? "Pausar" : "Reproduzir"}
        </button>
        <span class="mono" style="font-variant-numeric:tabular-nums">
          {carimbo(posicaoMs)} / {carimbo(r.duracao_ms)}
        </span>
        <span style="flex:1"></span>
        <div class="trilha-escolha">
          {#if trilhasDisponiveis.length > 1}
            <button
              aria-pressed={trilhaEscolhida === "ambas"}
              onclick={() => (trilhaEscolhida = "ambas")}>Ambas</button
            >
          {/if}
          {#each trilhasDisponiveis as t (t.nome)}
            <button
              aria-pressed={trilhaEscolhida === t.nome}
              onclick={() => (trilhaEscolhida = t.nome as "mic" | "system")}
            >
              {t.nome === "mic" ? "Microfone" : t.nome === "system" ? "Sistema" : "Importada"}
            </button>
          {/each}
        </div>
      </div>

      <input
        type="range"
        min="0"
        max={Math.max(1, r.duracao_ms)}
        value={posicaoMs}
        oninput={(e) => irPara(Number((e.currentTarget as HTMLInputElement).value))}
        aria-label="Posição da reprodução"
      />

      {#each trilhasDisponiveis as t (t.caminho)}
        <audio
          bind:this={elementos[t.nome]}
          src={convertFileSrc(t.caminho)}
          preload="metadata"
          muted={!trilhasParaTocar.some((p) => p.nome === t.nome)}
          ontimeupdate={t.nome === trilhasParaTocar[0]?.nome ? aoAvancar : undefined}
          onended={() => (tocando = false)}
        ></audio>
      {/each}
    {/if}
  </div>

  <!-- Timeline -->
  <section style="margin-bottom:22px">
    <div class="linha" style="justify-content:space-between;margin-bottom:8px">
      <h2>Linha do tempo</h2>
      <button class="botao discreto" disabled={selecionados.length === 0} onclick={copiarSelecao}>
        Copiar trecho ({selecionados.length})
      </button>
    </div>

    {#if detalhe.segmentos.length === 0}
      <div class="vazio">
        {#if r.situacao === "na_fila"}
          Esta reunião ainda está aguardando transcrição. O texto aparece aqui
          quando a fila chegar nela.
        {:else if r.situacao === "incompleta"}
          A finalização desta gravação não chegou ao fim, então não há
          transcrição publicada.
        {:else}
          Nenhum segmento transcrito.
        {/if}
      </div>
    {:else}
      <div class="linha-do-tempo">
        {#each detalhe.segmentos as s (s.id)}
          {@const papel = papelDoFalante(s.falante, s.trilha)}
          <div
            class={`segmento ${papel}`}
            class:sobreposto={s.sobreposto}
            data-instante={s.id}
            role="button"
            tabindex="0"
            aria-current={selecionados.includes(s.id) || segmentoAtual === s.id}
            onclick={(e) => acionarSegmento(s.id, s.inicio_ms, e)}
            onkeydown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                acionarSegmento(s.id, s.inicio_ms, e as unknown as MouseEvent);
              }
            }}
          >
            <span class="instante">{carimbo(s.inicio_ms)}</span>
            <div>
              <div class="quem">
                {falante(s.falante, s.trilha)}
                {#if s.sobreposto}<span class="marca-sobreposicao">fala sobreposta</span>{/if}
              </div>
              <div>{s.texto}</div>
            </div>
          </div>
        {/each}
      </div>
    {/if}
  </section>

  <!-- Notes: own area, never interleaved with the timeline -->
  <section>
    <h2 style="margin-bottom:8px">Notas</h2>
    {#if falhaNotas}
      <Pendencia falha={falhaNotas} titulo="Notas ainda não disponíveis" />
    {:else}
      <div class="vazio">Esta reunião não tem notas.</div>
    {/if}
  </section>
{/if}

{#if confirmacao}
  <Confirmacao
    titulo={confirmacao.titulo}
    corpo={confirmacao.corpo}
    confirmar="Remover o áudio"
    perigo
    onConfirmar={confirmacao.ao}
    onCancelar={() => (confirmacao = null)}
  />
{/if}
