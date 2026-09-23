<script lang="ts">
  // The gate before the main interface, and the first thing a new user sees.
  //
  // Two promises live here. Nothing is downloaded before the user has seen the
  // hardware found, the data folder with its free space, and what will be
  // fetched with its size -- and a volume without room refuses the start
  // instead of failing halfway. And when the environment is not ready, no
  // window opens whose commands merely look like they work.
  //
  // A preparation for an older version or lock runs by itself: the user
  // already agreed to it once, and the steps that are in date pass in seconds.
  import { listen } from "@tauri-apps/api/event";
  import { open } from "@tauri-apps/plugin-dialog";
  import { onMount } from "svelte";
  import {
    comoFalha,
    preparoIniciar,
    preparoPlano,
    type Ambiente,
    type Falha,
    type PlanoDoPreparo,
    type ProgressoDoPreparo,
  } from "../lib/api";
  import { bytes } from "../lib/format";
  import Pendencia from "../lib/components/Pendencia.svelte";

  let { ambiente, onPronto }: { ambiente: Ambiente; onPronto: () => void } = $props();

  const ROTULOS: Record<string, string> = {
    interpretador: "Interpretador Python",
    dependencias: "Dependências do núcleo",
    gpu: "Aceleração por GPU",
    configuracao: "Pasta de dados",
    modelo: "Modelo de transcrição",
    verificacao: "Verificação final",
  };
  const ESTADOS: Record<string, string> = {
    em_andamento: "em andamento",
    concluida: "concluída",
    pulada: "já estava pronta",
    falhou: "falhou",
  };

  let plano = $state<PlanoDoPreparo | null>(null);
  let pasta = $state<string | null>(null);
  let falhaDoPlano = $state<Falha | null>(null);
  let preparando = $state(false);
  let falha = $state<Falha | null>(null);
  let etapas = $state<Record<string, ProgressoDoPreparo>>({});
  let linhas = $state<string[]>([]);

  const automatico = $derived(ambiente.situacao.tipo === "desatualizado");
  const retomada = $derived(
    falha !== null ||
      (ambiente.situacao.tipo === "primeiro" && ambiente.situacao.retomada),
  );
  const ordem = $derived(
    Object.keys(ROTULOS).filter((etapa) => etapa !== "gpu" || plano?.escolha.etapa_gpu),
  );

  async function planejar() {
    falhaDoPlano = null;
    try {
      plano = await preparoPlano(pasta);
      pasta = plano.pasta;
    } catch (erro) {
      falhaDoPlano = comoFalha(erro);
    }
  }

  async function escolherPasta() {
    const escolha = await open({ directory: true, title: "Escolha a pasta de dados" });
    if (typeof escolha === "string" && escolha) {
      pasta = escolha;
      await planejar();
    }
  }

  async function preparar() {
    preparando = true;
    falha = null;
    etapas = {};
    linhas = [];
    try {
      await preparoIniciar(pasta);
      onPronto();
    } catch (erro) {
      falha = comoFalha(erro);
      // The step that was running is the one that failed.
      for (const [nome, etapa] of Object.entries(etapas)) {
        if (etapa.estado === "em_andamento") etapas[nome] = { ...etapa, estado: "falhou" };
      }
      await planejar();
    } finally {
      preparando = false;
    }
  }

  $effect(() => {
    const assinaturas = [
      listen<ProgressoDoPreparo>("preparo://etapa", (evento) => {
        const anterior = etapas[evento.payload.etapa];
        etapas[evento.payload.etapa] = {
          ...evento.payload,
          // A measurement carries only bytes; the words of the step stay.
          detalhe: evento.payload.detalhe || anterior?.detalhe || "",
        };
      }),
      listen<{ etapa: string; linha: string }>("preparo://linha", (evento) => {
        // Only the tail: a dependency resolution is hundreds of lines, and
        // holding them all would be memory spent on noise.
        linhas = [...linhas.slice(-60), evento.payload.linha];
      }),
    ];
    return () => {
      for (const assinatura of assinaturas) assinatura.then((cancelar) => cancelar());
    };
  });

  // Once, on arrival. As an effect it would track `pasta`, which the plan
  // itself fills in: a second plan for the folder as if chosen by hand -- the
  // provenance lost -- and, for an update, a second preparation on top.
  onMount(() => {
    void planejar().then(() => {
      if (automatico && plano) void preparar();
    });
  });

  function espaco(volume: { livre: number | null; necessario: number; falta: number }) {
    const livre = volume.livre == null ? "espaço livre desconhecido" : `${bytes(volume.livre)} livres`;
    return `precisa de ${bytes(volume.necessario)} · ${livre}`;
  }
</script>

<div class="corpo" style="display:grid;place-items:center">
  <div class="coluna" style="max-width:720px;width:100%">
    <div class="cartao">
      <h1 style="margin-bottom:10px">
        {automatico ? "Atualizando o ambiente do VoxVault" : "Preparar o VoxVault"}
      </h1>
      <p style="color:var(--texto-suave)">{ambiente.detalhe}</p>

      {#if ambiente.situacao.tipo === "sem_recursos"}
        <Pendencia
          falha={{ mensagem: ambiente.detalhe, acao: ambiente.acao }}
          titulo="Instalação incompleta"
        />
      {:else if falhaDoPlano && !plano}
        <Pendencia falha={falhaDoPlano} titulo="Não foi possível montar o plano do preparo" />
      {:else if !plano}
        <p class="legenda">Verificando o hardware e o espaço em disco...</p>
      {:else}
        <section style="margin-top:14px" id="preparo-hardware">
          <h2 class="legenda" style="margin:0 0 4px">Hardware</h2>
          <p style="margin:0">
            {#if plano.hardware.gpu}
              GPU NVIDIA: <strong>{plano.hardware.gpu.nome}</strong>, {plano.hardware.gpu.memoria_mb} MB.
            {:else}
              <strong>Sem GPU NVIDIA compatível.</strong> {plano.hardware.detalhe}
            {/if}
          </p>
          <p class="legenda" style="margin:4px 0 0">
            Modelo para este hardware: <strong>{plano.escolha.modelo}</strong>, na
            {plano.escolha.dispositivo === "cuda" ? "GPU" : "CPU"}.
            {#if plano.escolha.dispositivo === "cpu"}
              Sem GPU, a transcrição leva cerca de 45 min para cada hora de reunião.
            {/if}
          </p>
        </section>

        <section style="margin-top:14px" id="preparo-pasta">
          <h2 class="legenda" style="margin:0 0 4px">Pasta de dados</h2>
          <div class="linha" style="gap:8px;align-items:center">
            <code style="flex:1;overflow-wrap:anywhere">{plano.pasta}</code>
            <button class="botao" onclick={escolherPasta} disabled={preparando}>Escolher outra…</button>
          </div>
          <p class="legenda" style="margin:4px 0 0">
            {#if plano.pasta_origem === "padrao_anterior"}
              Já existem reuniões nesta pasta; elas continuam onde estão.
            {:else}
              Onde ficam as gravações, o banco e o modelo. A desinstalação não a apaga.
            {/if}
          </p>
        </section>

        <section style="margin-top:14px" id="preparo-downloads">
          <h2 class="legenda" style="margin:0 0 4px">O que será baixado</h2>
          <table style="width:100%;border-collapse:collapse">
            <tbody>
              {#each plano.itens as item (item.etapa)}
                <tr>
                  <td style="padding:2px 0">{item.rotulo}</td>
                  <td style="padding:2px 0;text-align:right;white-space:nowrap">
                    {item.presente ? "já presente" : bytes(item.bytes)}
                  </td>
                </tr>
              {/each}
              <tr>
                <td style="padding-top:6px"><strong>Total</strong></td>
                <td style="padding-top:6px;text-align:right"><strong>{bytes(plano.total_bytes)}</strong></td>
              </tr>
            </tbody>
          </table>
          <p class="legenda" style="margin:6px 0 0">
            {#each plano.volumes as volume, i (volume.raiz)}
              <!-- A drive root already ends in its colon ("C:"). -->
              {i ? " · " : ""}Volume {volume.raiz} {espaco(volume)}
            {/each}
          </p>
          {#if plano.recusa}
            <div class="nota erro" style="margin-top:8px" id="preparo-recusa">
              <strong>Espaço insuficiente</strong>
              <p style="margin:4px 0 0">{plano.recusa}</p>
            </div>
          {/if}
        </section>

        {#if preparando || Object.keys(etapas).length}
          <section style="margin-top:16px" id="preparo-etapas">
            <h2 class="legenda" style="margin:0 0 4px">Progresso</h2>
            <ol style="margin:0;padding-left:18px">
              {#each ordem as nome (nome)}
                {@const etapa = etapas[nome]}
                <li style="padding:2px 0">
                  {ROTULOS[nome]} —
                  <span class:legenda={!etapa}>
                    {etapa ? ESTADOS[etapa.estado] : "aguardando"}
                  </span>
                  {#if etapa?.baixado != null && etapa.estado === "em_andamento"}
                    <span class="legenda">
                      · {bytes(etapa.baixado)}{etapa.total ? ` de ${bytes(etapa.total)}` : ""}
                    </span>
                  {/if}
                </li>
              {/each}
            </ol>
          </section>
        {/if}

        {#if falha}
          <div style="margin-top:12px">
            <Pendencia {falha} titulo="O preparo parou" />
          </div>
        {/if}

        {#if linhas.length}
          <details style="margin-top:10px">
            <summary class="legenda" style="cursor:pointer">Ver a saída detalhada</summary>
            <pre class="trecho" style="max-height:220px;white-space:pre-wrap;margin-top:8px">{linhas.join("\n")}</pre>
          </details>
        {/if}

        {#if !preparando}
          <div class="linha" style="margin-top:16px">
            <button
              class="botao primario grande"
              id="preparo-iniciar"
              onclick={preparar}
              disabled={!plano.pode_iniciar}
            >
              {retomada ? "Retomar" : "Preparar o VoxVault"}
            </button>
          </div>
          <p class="legenda" style="margin-top:8px">
            Retomar aproveita tudo o que já foi baixado. O preparo acessa
            releases.astral.sh (interpretador), pypi.org e files.pythonhosted.org
            (pacotes) e huggingface.co com sua CDN (modelo); depois dele, o VoxVault não
            usa a rede.
          </p>
        {/if}
      {/if}
    </div>
  </div>
</div>
