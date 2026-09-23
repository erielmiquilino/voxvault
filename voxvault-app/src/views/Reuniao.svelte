<script lang="ts">
  // Reading one meeting: the second operation the terminal gets in the way of.
  //
  // The notes area is deliberately separate from the timeline and never
  // interleaved with it. A summary produced by a model can be wrong, and the
  // distinction between "this was said" and "this was interpreted" has to
  // survive a hurried reading months later.
  import { convertFileSrc } from "@tauri-apps/api/core";
  import { tick } from "svelte";

  import {
    comoFalha,
    reunioesExcluir,
    reunioesExcluirPrevia,
    reuniaoAbrirPasta,
    reuniaoDetalhar,
    reuniaoExportar,
    reuniaoRemoverAudio,
    reuniaoRenomear,
    reuniaoRemoverAudioPrevia,
    reuniaoReprocessar,
    notaAlterar,
    notaCriar,
    notaRemover,
    type Detalhe,
    type Falha,
    type Resumo,
    type TipoDeNota,
  } from "../lib/api";
  import { recado, shell } from "../lib/estado.svelte";
  import { corpoDeUma, motivoLegivel, motivoParaNaoExcluir } from "../lib/exclusao";
  import {
    bytes,
    carimbo,
    dataHora,
    duracao,
    falante,
    papelDoFalante,
    ROTULO_ORIGEM,
    ROTULO_SITUACAO,
    tomDaSituacao,
  } from "../lib/format";
  import Confirmacao from "../lib/components/Confirmacao.svelte";
  import Pendencia from "../lib/components/Pendencia.svelte";

  // The summary is handed in rather than re-derived: the list already holds the
  // core's authoritative answer, and a second derivation here would be a weaker
  // copy of the same state one screen away.
  let {
    resumo,
    focoMs = null,
    onMudou,
    onExcluida,
  }: {
    resumo: Resumo;
    focoMs?: number | null;
    onMudou: () => void | Promise<void>;
    /** The meeting is gone: the caller leaves it and refreshes the list. */
    onExcluida: (uid: string) => void | Promise<void>;
  } = $props();

  const uid = $derived(resumo.uid);

  let detalhe = $state<Detalhe | null>(null);
  let falha = $state<Falha | null>(null);
  let carregando = $state(false);

  const TIPOS_DE_NOTA: { valor: TipoDeNota; rotulo: string }[] = [
    { valor: "resumo", rotulo: "Resumo" },
    { valor: "decisoes", rotulo: "Decisões" },
    { valor: "pendencias", rotulo: "Pendências" },
    { valor: "livre", rotulo: "Livre" },
  ];

  let novaNotaTipo = $state<TipoDeNota>("livre");
  let novaNotaTexto = $state("");
  let notaEmEdicao = $state<string | null>(null);
  let textoEmEdicao = $state("");

  let trilhaEscolhida = $state<"mic" | "system" | "ambas">("ambas");
  let posicaoMs = $state(0);
  let tocando = $state(false);
  let elementos = $state<Record<string, HTMLAudioElement | null>>({});
  let selecionados = $state<number[]>([]);
  let confirmacao = $state<null | {
    titulo: string;
    corpo: string;
    rotulo: string;
    ao: () => void;
  }>(null);
  let renomeando = $state(false);
  let novoTitulo = $state("");
  let excluindo = $state(false);
  /** True while a deletion runs: the audio elements are taken out of the page
   *  so the webview lets go of the files they hold open. */
  let reprodutorLiberado = $state(false);

  const motivoSemExclusao = $derived(motivoParaNaoExcluir(resumo));

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
    void carregar();
  });

  async function carregar() {
    carregando = true;
    falha = null;
    detalhe = null;
    selecionados = [];
    posicaoMs = 0;
    tocando = false;
    try {
      detalhe = await reuniaoDetalhar(
        resumo.uid,
        resumo.diretorio,
        resumo.revisao_ativa || null,
      );
      novoTitulo = resumo.titulo;
      trilhaEscolhida =
        detalhe.trilhas.length > 1
          ? "ambas"
          : ((detalhe.trilhas[0]?.nome as "mic" | "system") ?? "ambas");
    } catch (erro) {
      falha = comoFalha(erro);
    } finally {
      carregando = false;
    }
  }

  async function criarNota() {
    if (!novaNotaTexto.trim()) return;
    await executar(
      () => notaCriar(uid, novaNotaTipo, novaNotaTexto.trim()),
      "Nota gravada com autoria de usuário.",
    );
    novaNotaTexto = "";
  }

  async function salvarEdicao(id: string) {
    if (!textoEmEdicao.trim()) return;
    await executar(() => notaAlterar(id, textoEmEdicao.trim()), "Nota alterada.");
    notaEmEdicao = null;
  }

  function pedirRemocaoDeNota(id: string, tipo: string) {
    confirmacao = {
      titulo: "Remover esta nota?",
      corpo:
        `A nota do tipo "${tipo}" será apagada em definitivo. A transcrição da ` +
        "reunião não é afetada: nota e linha do tempo são coisas separadas.",
      rotulo: "Remover a nota",
      ao: () => {
        confirmacao = null;
        void executar(() => notaRemover(id), "Nota removida.");
      },
    };
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
      await carregar();
      // The summary lives in the list, so anything that changes it has to make
      // the list re-ask the core rather than patching a local copy.
      await onMudou();
    } catch (erro) {
      recado("erro", comoFalha(erro).mensagem);
    }
  }

  /// Deleting starts from the core's own preview: the confirmation lists what
  /// the core is about to remove and the space it will free, and the button
  /// names the action. The focus starts on "Cancelar".
  async function pedirExclusao() {
    let previa;
    try {
      previa = await reunioesExcluirPrevia([uid]);
    } catch (erro) {
      recado("erro", comoFalha(erro).mensagem);
      return;
    }
    const item = previa.itens[0];
    if (!item || item.motivo) {
      recado(
        "erro",
        item ? `A reunião não pode ser excluída agora: ${motivoLegivel(item)}.` : "O núcleo não descreveu a reunião.",
      );
      return;
    }
    confirmacao = {
      titulo: "Excluir esta reunião definitivamente?",
      corpo: corpoDeUma(previa),
      rotulo: "Excluir definitivamente",
      ao: () => void excluir(),
    };
  }

  /// The player is stopped and taken out of the page before the call, because
  /// the webview keeps the FLAC open while an audio element exists -- and an
  /// open file is exactly what makes the core refuse. If the file is still held
  /// for a moment after that, one more attempt follows half a second later.
  async function excluir() {
    excluindo = true;
    await liberarReprodutor();
    try {
      let resultado = await reunioesExcluir([uid]);
      if (resultado.itens[0]?.causa === "em_uso") {
        await new Promise((pronto) => setTimeout(pronto, 500));
        resultado = await reunioesExcluir([uid]);
      }
      const item = resultado.itens[0];
      if (item?.resultado === "excluida") {
        confirmacao = null;
        recado("ok", `Reunião excluída. ${bytes(resultado.total.bytes)} liberados.`);
        await onExcluida(uid);
        return;
      }
      confirmacao = null;
      reprodutorLiberado = false;
      recado(
        "erro",
        item ? `A reunião não foi excluída: ${motivoLegivel(item)}.` : "O núcleo não respondeu sobre a exclusão.",
      );
    } catch (erro) {
      confirmacao = null;
      reprodutorLiberado = false;
      recado("erro", comoFalha(erro).mensagem);
    } finally {
      excluindo = false;
    }
  }

  async function liberarReprodutor() {
    for (const elemento of Object.values(elementos)) {
      if (!elemento) continue;
      elemento.pause();
      elemento.removeAttribute("src");
      // Without a source, load() makes the element drop the resource it held.
      elemento.load();
    }
    tocando = false;
    reprodutorLiberado = true;
    await tick();
  }

  /// The confirmation quotes the core's own dry run, so what the user reads is
  /// exactly what the core is about to do -- including its refusal to remove
  /// audio from a meeting that has no transcript, where removing it would erase
  /// the meeting entirely.
  async function pedirRemocaoDeAudio() {
    let previa: string;
    try {
      previa = await reuniaoRemoverAudioPrevia(uid);
    } catch (erro) {
      recado("erro", comoFalha(erro).mensagem);
      return;
    }
    confirmacao = {
      titulo: "Remover o áudio desta reunião?",
      corpo: previa.trim(),
      rotulo: "Remover o áudio",
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
  {@const r = resumo}

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
      <span class={`selo ${tomDaSituacao(r.situacao)}`}>
        {ROTULO_SITUACAO[r.situacao] ?? r.situacao}
      </span>
      {#if r.transcricao_disponivel && r.situacao !== "pronta"}
        <span class="selo ok">transcrição legível</span>
      {/if}
      {#if r.completude === "parcial"}<span class="selo alerta">parcial</span>{/if}
      {#if r.motor}<span class="selo">{r.motor}</span>{/if}
    </div>

    {#if r.motivo_da_falha}
      <div class="nota erro" style="margin-top:10px">
        <strong>A transcrição falhou</strong>
        <p style="margin:0">{r.motivo_da_falha}</p>
        {#if r.tem_audio}
          <p class="legenda" style="margin:6px 0 0">
            O áudio continua no disco, então reprocessar é possível.
          </p>
        {/if}
      </div>
    {/if}

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
        disabled={!r.transcricao_disponivel}
        title={r.transcricao_disponivel
          ? ""
          : "A reunião ainda não tem transcrição publicada, então não há o que exportar."}
        onclick={() => executar(() => reuniaoExportar(uid), "Exportações regeneradas.")}
      >
        Exportar
      </button>
      <button class="botao discreto" onclick={() => reuniaoAbrirPasta(resumo.diretorio)}>
        Abrir a pasta
      </button>
      <button class="botao discreto" disabled={!r.tem_audio} onclick={pedirRemocaoDeAudio}>
        Remover o áudio
      </button>
      <button
        class="botao discreto perigo"
        disabled={!!motivoSemExclusao || excluindo}
        title={motivoSemExclusao ??
          "Apaga a reunião inteira: áudio, transcrições, notas e exportações."}
        onclick={pedirExclusao}
      >
        Excluir reunião
      </button>
    </div>
    {#if motivoSemExclusao}
      <p class="legenda" style="margin:6px 0 0">{motivoSemExclusao}</p>
    {/if}
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

      {#if !reprodutorLiberado}
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
          Esta reunião está aguardando transcrição. O texto aparece aqui quando a
          fila chegar nela.
        {:else if r.situacao === "transcrevendo"}
          Esta reunião está sendo transcrita agora. O texto aparece aqui quando a
          revisão for publicada.
        {:else if r.situacao === "falhou"}
          A transcrição desta reunião falhou; o motivo está acima.
        {:else if r.situacao === "incompleta"}
          A finalização desta gravação não chegou ao fim, então não há
          transcrição publicada.
        {:else if r.transcricao_disponivel}
          <!-- A published revision with no segments is not a missing
               transcription: it is a transcription that found no speech. -->
          A transcrição foi concluída e não encontrou fala nenhuma. O áudio
          existe, mas o filtro de voz não reconheceu nada dentro dele.
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
    <div class="linha" style="justify-content:space-between;margin-bottom:8px">
      <h2>Notas</h2>
      <span class="legenda">Interpretação, não transcrição — área separada de propósito</span>
    </div>

    {#if detalhe.notas.length === 0}
      <div class="vazio">Esta reunião não tem notas.</div>
    {:else}
      <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:14px">
        {#each detalhe.notas as nota (nota.uid)}
          <article class="cartao" style="padding:13px">
            <div class="linha" style="justify-content:space-between;margin-bottom:6px">
              <div class="linha">
                <span class="selo acento">{nota.tipo}</span>
                <span class="legenda">
                  {nota.autoria_tipo === "agente"
                    ? `agente${nota.autoria_cliente ? `: ${nota.autoria_cliente}` : ""}`
                    : "você"}
                </span>
                <span class="legenda">· criada em {dataHora(nota.criada_em)}</span>
                {#if nota.alterada_em && nota.alterada_em !== nota.criada_em}
                  <span class="legenda">· alterada em {dataHora(nota.alterada_em)}</span>
                {/if}
              </div>
              <div class="linha">
                <button
                  class="botao discreto"
                  onclick={() => {
                    notaEmEdicao = nota.uid;
                    textoEmEdicao = nota.conteudo;
                  }}>Editar</button
                >
                <button
                  class="botao discreto"
                  onclick={() => pedirRemocaoDeNota(nota.uid, nota.tipo)}>Remover</button
                >
              </div>
            </div>

            {#if notaEmEdicao === nota.uid}
              <textarea bind:value={textoEmEdicao}></textarea>
              <div class="linha fim" style="margin-top:8px">
                <button class="botao" onclick={() => (notaEmEdicao = null)}>Cancelar</button>
                <button class="botao primario" onclick={() => salvarEdicao(nota.uid)}>
                  Gravar
                </button>
              </div>
            {:else}
              <p style="margin:0;white-space:pre-wrap">{nota.conteudo}</p>
            {/if}
          </article>
        {/each}
      </div>
    {/if}

    <div class="cartao" style="padding:13px">
      <div class="linha" style="margin-bottom:8px">
        <select bind:value={novaNotaTipo} style="width:auto" aria-label="Tipo da nota">
          {#each TIPOS_DE_NOTA as tipo (tipo.valor)}
            <option value={tipo.valor}>{tipo.rotulo}</option>
          {/each}
        </select>
      </div>
      <textarea bind:value={novaNotaTexto} placeholder="Escreva uma nota sobre esta reunião"
      ></textarea>
      <div class="linha fim" style="margin-top:8px">
        <button class="botao primario" disabled={!novaNotaTexto.trim()} onclick={criarNota}>
          Gravar nota
        </button>
      </div>
    </div>
  </section>
{/if}

{#if confirmacao}
  <Confirmacao
    titulo={confirmacao.titulo}
    corpo={confirmacao.corpo}
    confirmar={excluindo ? "Excluindo..." : confirmacao.rotulo}
    perigo
    ocupado={excluindo}
    onConfirmar={confirmacao.ao}
    onCancelar={() => (confirmacao = null)}
  />
{/if}
