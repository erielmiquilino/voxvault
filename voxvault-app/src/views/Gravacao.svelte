<script lang="ts">
  // Recording: the operation the terminal gets in the way of.
  //
  // Two things are non-negotiable here. Only the actions valid for the current
  // state are offered, and the evidence that both tracks are actually capturing
  // is on screen without asking -- discovering that the microphone was muted
  // after the meeting is this tool's worst possible failure.
  import { getCurrentWebview } from "@tauri-apps/api/webview";
  import { listen } from "@tauri-apps/api/event";
  import { open } from "@tauri-apps/plugin-dialog";

  import {
    comoFalha,
    gravacaoEncerrar,
    gravacaoIniciar,
    gravacaoPausar,
    gravacaoRetomar,
    importar,
    type Falha,
    type ResultadoImportacao,
  } from "../lib/api";
  import {
    gravacao,
    iniciarRelogio,
    pararRelogio,
    pausarRelogio,
    recado,
    registrarAviso,
    registrarNivel,
    shell,
    zerarGravacao,
    type EstadoDaGravacao,
  } from "../lib/estado.svelte";
  import { carimbo } from "../lib/format";
  import Medidor from "../lib/components/Medidor.svelte";
  import Pendencia from "../lib/components/Pendencia.svelte";

  const servicoPronto = $derived(shell.servico?.estado === "conectado");

  // Detection suggests; it never acts. Starting a recording because an
  // application opened would capture things nobody agreed to record, and the
  // one thing this product cannot afford is to be surprising about that.
  const deteccao = $derived(shell.servico?.deteccao?.deteccao ?? null);
  let sugestaoDispensada = $state<string | null>(null);
  const sugestaoVisivel = $derived(
    !!deteccao &&
      gravacao.estado === "ocioso" &&
      servicoPronto &&
      sugestaoDispensada !== deteccao.aplicativo,
  );
  const causaIndisponivel = $derived(
    shell.servico?.detalhe ?? "O estado do serviço local ainda não foi verificado.",
  );

  let falha = $state<Falha | null>(null);
  let ocupado = $state(false);
  let sobreZona = $state(false);
  let importando = $state<{ atual: number; total: number; arquivo: string } | null>(null);
  let resultados = $state<ResultadoImportacao[]>([]);

  // -- live updates from the resident service --------------------------------
  //
  // Levels, track state and warnings are pushed; nothing here polls. The 20 Hz
  // cap is applied in `registrarNivel`, on arrival.
  $effect(() => {
    const assinaturas = [
      // The service is the authority on the recording's state, not this window.
      // A recording started from the command line therefore arrives here as a
      // pushed state and appears as active -- it is the same recording, of the
      // same owner.
      listen<{ estado: EstadoDaGravacao; decorrido_ms?: number; titulo?: string; uid?: string }>(
        "gravacao://estado",
        (evento) => {
          const { estado, decorrido_ms, titulo, uid } = evento.payload;
          gravacao.estado = estado;
          if (titulo) gravacao.titulo = titulo;
          if (uid) gravacao.uid = uid;
          if (estado === "gravando") iniciarRelogio(decorrido_ms ?? gravacao.decorridoMs);
          else if (estado === "pausado") pausarRelogio();
          else {
            pararRelogio();
            zerarGravacao();
          }
        },
      ),
      listen<{ trilha: "mic" | "system"; nivel: number }>("gravacao://nivel", (evento) => {
        registrarNivel(evento.payload.trilha, evento.payload.nivel);
      }),
      listen<{ trilha: "mic" | "system"; capturando: boolean; motivo?: string }>(
        "gravacao://trilha",
        (evento) => {
          const trilha = gravacao.trilhas[evento.payload.trilha];
          if (!trilha) return;
          trilha.capturando = evento.payload.capturando;
          trilha.motivo = evento.payload.motivo ?? null;
        },
      ),
      listen<{ texto: string; instante_ms?: number }>("gravacao://aviso", (evento) => {
        registrarAviso(evento.payload.texto, evento.payload.instante_ms ?? null);
        recado("info", evento.payload.texto);
      }),
      listen<{ indice: number; total: number; arquivo: string }>(
        "importacao://progresso",
        (evento) => {
          importando = {
            atual: evento.payload.indice + 1,
            total: evento.payload.total,
            arquivo: evento.payload.arquivo,
          };
        },
      ),
    ];
    return () => {
      for (const assinatura of assinaturas) assinatura.then((cancelar) => cancelar());
    };
  });

  // -- the service's view of the recording -----------------------------------
  //
  // The service owns the recording; this window only reflects it. The state is
  // therefore taken from the polled snapshot rather than from what this window
  // believes it did -- which is also what makes a recording started from the
  // command line appear here as active.
  let duracoesAnteriores: Record<string, number> = {};

  $effect(() => {
    const vista = shell.servico?.gravacao;
    if (shell.servico?.estado !== "conectado" || !vista) return;

    const estado: EstadoDaGravacao = !vista.ativa
      ? "ocioso"
      : vista.pausada
        ? "pausado"
        : "gravando";

    if (estado !== gravacao.estado) {
      gravacao.estado = estado;
      if (estado === "gravando") iniciarRelogio(vista.duracao_ms);
      else if (estado === "pausado") pausarRelogio();
      else {
        pararRelogio();
        zerarGravacao();
        duracoesAnteriores = {};
        return;
      }
    }

    if (vista.ativa) {
      gravacao.uid = vista.uid || null;
      if (vista.titulo) gravacao.titulo = vista.titulo;

      // Levels arrive with the poll, and the peak is reset by the read, so the
      // rate here is this app's polling rate -- one sample every two seconds,
      // far below the twenty per second the requirement allows.
      for (const [trilha, medida] of Object.entries(vista.niveis ?? {})) {
        if (trilha === "mic" || trilha === "system") {
          registrarNivel(trilha, medida.pico);
          gravacao.trilhas[trilha].silencioHaS = medida.silencio_ha_s;
        }
      }

      // A track that stopped capturing stops accumulating milliseconds. That
      // is the honest signal available: the service reports what each track
      // has written, and a track whose figure froze while the other grew is
      // the one that died.
      for (const trilha of ["mic", "system"] as const) {
        const escrito = vista.trilhas?.[trilha];
        const presente = escrito !== undefined;
        const anterior = duracoesAnteriores[trilha];
        const parou =
          presente &&
          estado === "gravando" &&
          anterior !== undefined &&
          escrito === anterior;
        gravacao.trilhas[trilha].capturando = presente && !parou;
        gravacao.trilhas[trilha].motivo = parou
          ? "Esta trilha parou de crescer: o dispositivo dela provavelmente foi perdido. A outra continua gravando."
          : presente
            ? null
            : "Esta trilha não foi aberta nesta gravação.";
        if (presente) duracoesAnteriores[trilha] = escrito;
      }

      for (const aviso of vista.avisos ?? []) {
        if (!gravacao.avisos.some((existente) => existente.texto === aviso)) {
          registrarAviso(aviso);
          recado("info", aviso);
        }
      }
    }
  });

  // -- drag and drop ---------------------------------------------------------

  $effect(() => {
    const assinatura = getCurrentWebview().onDragDropEvent((evento) => {
      if (evento.payload.type === "over") sobreZona = true;
      else if (evento.payload.type === "leave") sobreZona = false;
      else if (evento.payload.type === "drop") {
        sobreZona = false;
        void enviarParaImportacao(evento.payload.paths);
      }
    });
    return () => {
      assinatura.then((cancelar) => cancelar());
    };
  });

  async function escolherArquivos() {
    const escolha = await open({
      multiple: true,
      title: "Escolha os arquivos de áudio ou vídeo",
      filters: [
        {
          name: "Áudio e vídeo",
          extensions: ["mp3", "wav", "flac", "m4a", "aac", "ogg", "opus", "mp4", "mkv", "mov", "webm"],
        },
      ],
    });
    if (!escolha) return;
    await enviarParaImportacao(Array.isArray(escolha) ? escolha : [escolha]);
  }

  async function enviarParaImportacao(arquivos: string[]) {
    if (arquivos.length === 0) return;
    resultados = [];
    importando = { atual: 0, total: arquivos.length, arquivo: "" };
    try {
      resultados = await importar(arquivos);
      const bons = resultados.filter((r) => r.ok).length;
      const maus = resultados.length - bons;
      if (bons > 0) {
        recado(
          "ok",
          `${bons} arquivo(s) importado(s) e enfileirado(s) para transcrição.`,
        );
      }
      if (maus > 0) recado("erro", `${maus} arquivo(s) não puderam ser importados.`);
    } finally {
      importando = null;
    }
  }

  // -- controls --------------------------------------------------------------

  async function acionar(acao: () => Promise<unknown>, depois: () => void) {
    ocupado = true;
    falha = null;
    try {
      await acao();
      depois();
    } catch (erro) {
      // The state stays where it was: a start that failed leaves the app idle,
      // and the cause names the device rather than saying "erro".
      falha = comoFalha(erro);
    } finally {
      ocupado = false;
    }
  }

  const iniciar = () =>
    acionar(
      () => gravacaoIniciar(gravacao.titulo.trim()),
      () => {
        gravacao.estado = "gravando";
        gravacao.avisos = [];
        gravacao.trilhas.mic.capturando = true;
        gravacao.trilhas.system.capturando = true;
        iniciarRelogio(0);
      },
    );

  const pausar = () =>
    acionar(gravacaoPausar, () => {
      gravacao.estado = "pausado";
      pausarRelogio();
    });

  const retomar = () =>
    acionar(gravacaoRetomar, () => {
      gravacao.estado = "gravando";
      iniciarRelogio(gravacao.decorridoMs);
    });

  const encerrar = () =>
    acionar(gravacaoEncerrar, () => {
      pararRelogio();
      zerarGravacao();
      recado("ok", "Gravação encerrada e enfileirada para transcrição.");
    });

  const ocioso = $derived(gravacao.estado === "ocioso");
</script>

<div class="corpo">
  <div class="coluna">
    {#if !servicoPronto}
      <div class="nota alerta">
        <strong>Gravação indisponível</strong>
        <p>{causaIndisponivel}</p>
        <p class="legenda">
          A gravação é executada pelo serviço residente do núcleo, nunca por esta
          janela: dois donos dos dispositivos de áudio e da fila produziriam
          gravações concorrentes sobre os mesmos dispositivos.
        </p>
      </div>
    {/if}

    {#if sugestaoVisivel && deteccao}
      <div class="nota acento">
        <div class="linha" style="justify-content:space-between;align-items:flex-start">
          <div>
            <strong>Parece que uma reunião começou</strong>
            <p style="margin:0">
              {deteccao.aplicativo} está usando o microfone há
              {Math.round(deteccao.sustentado_ha_s)} s ({deteccao.sinal}).
              {#if deteccao.fraco}
                O sinal é fraco: um navegador não diz o que está fazendo, então
                pode não ser uma reunião.
              {/if}
            </p>
            <p class="legenda" style="margin:6px 0 0">
              Nada foi gravado. O VoxVault nunca começa uma gravação sozinho.
            </p>
          </div>
          <div class="linha">
            <button
              class="botao discreto"
              onclick={() => (sugestaoDispensada = deteccao.aplicativo)}>Agora não</button
            >
            <button class="botao perigo" onclick={iniciar} disabled={ocupado}>
              Gravar
            </button>
          </div>
        </div>
      </div>
    {/if}

    <!-- Recording control -->
    <div class="cartao">
      <header>
        <div class="linha">
          <i class="pulso" class:ativo={gravacao.estado === "gravando"}
             style={gravacao.estado === "ocioso" ? "background:var(--texto-fraco)" : ""}></i>
          <h2>
            {#if gravacao.estado === "gravando"}Gravando
            {:else if gravacao.estado === "pausado"}Pausada
            {:else}Ocioso{/if}
          </h2>
        </div>
        {#if !ocioso}
          <span class="relogio">{carimbo(gravacao.decorridoMs)}</span>
        {/if}
      </header>

      {#if ocioso}
        <label class="campo" style="margin-bottom:14px">
          Título da reunião (opcional)
          <input
            type="text"
            bind:value={gravacao.titulo}
            placeholder="Sem título, a reunião recebe o instante de início"
            disabled={!servicoPronto || ocupado}
          />
        </label>
        <button
          class="botao perigo grande"
          onclick={iniciar}
          disabled={!servicoPronto || ocupado}
          title={servicoPronto ? "" : causaIndisponivel}
        >
          Iniciar gravação
        </button>
      {:else}
        <div class="linha">
          {#if gravacao.estado === "gravando"}
            <button class="botao grande" onclick={pausar} disabled={ocupado}>Pausar</button>
          {:else}
            <button class="botao primario grande" onclick={retomar} disabled={ocupado}>
              Retomar
            </button>
          {/if}
          <button class="botao perigo grande" onclick={encerrar} disabled={ocupado}>
            Encerrar
          </button>
        </div>
        <p class="legenda" style="margin-top:10px">
          O tempo apresentado exclui os períodos pausados.
        </p>
      {/if}

      {#if falha}
        <div style="margin-top:14px">
          <Pendencia {falha} titulo="Não foi possível" />
        </div>
      {/if}
    </div>

    <!-- Live tracks -->
    <div class="cartao">
      <header>
        <h2>Trilhas</h2>
        <span class="legenda">
          O pico é zerado a cada leitura, então a taxa é a da consulta — bem
          abaixo do teto de 20 por segundo por trilha
        </span>
      </header>
      <div class="grade-dupla">
        <Medidor
          rotulo="Microfone — a sua voz"
          nivel={gravacao.trilhas.mic.nivel}
          capturando={gravacao.trilhas.mic.capturando}
          motivo={gravacao.trilhas.mic.motivo}
          disponivel={gravacao.niveisVivos}
          silencioHaS={gravacao.trilhas.mic.silencioHaS}
        />
        <Medidor
          rotulo="Sistema — o que sai pela saída"
          nivel={gravacao.trilhas.system.nivel}
          capturando={gravacao.trilhas.system.capturando}
          motivo={gravacao.trilhas.system.motivo}
          disponivel={gravacao.niveisVivos}
          silencioHaS={gravacao.trilhas.system.silencioHaS}
        />
      </div>
      {#if ocioso}
        <p class="legenda" style="margin:12px 0 0">
          Os níveis aparecem durante a gravação. Eles vêm do mesmo ponto em que o
          áudio já é processado pela captura — a interface não processa áudio.
        </p>
      {:else if !gravacao.niveisVivos}
        <div class="nota alerta" style="margin-top:12px">
          <strong>Sem medição de nível</strong>
          <p style="margin:0">
            Nenhuma medição chegou nos últimos segundos. O indicador de captura
            acima continua valendo: ele vem do que cada trilha já escreveu em
            disco.
          </p>
        </div>
      {/if}
    </div>

    <!-- Warnings -->
    {#if gravacao.avisos.length > 0}
      <div class="cartao">
        <header>
          <h2>Avisos desta gravação</h2>
          <span class="legenda">Ficam consultáveis na reunião depois de encerrada</span>
        </header>
        <div style="display:flex;flex-direction:column;gap:8px">
          {#each gravacao.avisos as aviso (aviso.em)}
            <div class="nota alerta">
              <p style="margin:0">{aviso.texto}</p>
              {#if aviso.instanteMs !== null}
                <span class="legenda mono">em {carimbo(aviso.instanteMs)}</span>
              {/if}
            </div>
          {/each}
        </div>
      </div>
    {/if}

    <!-- Import -->
    <div class="cartao">
      <header>
        <h2>Importar mídia</h2>
        <button class="botao" onclick={escolherArquivos} disabled={!!importando}>
          Escolher arquivos
        </button>
      </header>

      <div class="zona-solta" class:ativa={sobreZona}>
        {#if importando}
          Importando {importando.atual} de {importando.total}
          {#if importando.arquivo}
            <div class="legenda mono" style="margin-top:6px">{importando.arquivo}</div>
          {/if}
        {:else}
          Arraste arquivos de áudio ou vídeo para esta janela.
          <div class="legenda" style="margin-top:4px">
            Cada arquivo vira uma reunião e entra na fila de transcrição.
          </div>
        {/if}
      </div>

      {#if resultados.length > 0}
        <div style="display:flex;flex-direction:column;gap:6px;margin-top:12px">
          {#each resultados as resultado (resultado.arquivo)}
            <div class="nota" class:erro={!resultado.ok}>
              <div class="linha" style="justify-content:space-between">
                <span class="mono" style="word-break:break-all">{resultado.arquivo}</span>
                <span class={`selo ${resultado.ok ? "ok" : "erro"}`}>
                  {resultado.ok ? "importado" : "recusado"}
                </span>
              </div>
              {#if resultado.detalhe}
                <p class="legenda" style="margin:6px 0 0;white-space:pre-wrap">
                  {resultado.detalhe}
                </p>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </div>
</div>
