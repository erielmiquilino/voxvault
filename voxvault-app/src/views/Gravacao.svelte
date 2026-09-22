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
  } from "../lib/estado.svelte";
  import { carimbo } from "../lib/format";
  import Medidor from "../lib/components/Medidor.svelte";
  import Pendencia from "../lib/components/Pendencia.svelte";

  const servicoPronto = $derived(shell.servico?.estado === "conectado");
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
        <span class="legenda">Atualização limitada a 20 vezes por segundo por trilha</span>
      </header>
      <div class="grade-dupla">
        <Medidor
          rotulo="Microfone — a sua voz"
          nivel={gravacao.trilhas.mic.nivel}
          capturando={gravacao.trilhas.mic.capturando}
          motivo={gravacao.trilhas.mic.motivo}
        />
        <Medidor
          rotulo="Sistema — o que sai pela saída"
          nivel={gravacao.trilhas.system.nivel}
          capturando={gravacao.trilhas.system.capturando}
          motivo={gravacao.trilhas.system.motivo}
        />
      </div>
      {#if ocioso}
        <p class="legenda" style="margin:12px 0 0">
          Os níveis aparecem durante a gravação. Eles vêm do mesmo ponto em que o
          áudio já é processado pela captura — a interface não processa áudio.
        </p>
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
