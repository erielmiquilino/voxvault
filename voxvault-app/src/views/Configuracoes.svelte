<script lang="ts">
  // Settings: the choices that change how the tool behaves, in one place, plus
  // the environment diagnostic made readable for someone who is not going to
  // open a terminal.
  //
  // Every value shown here carries the source that imposed it, because that is
  // what decides whether this screen may offer to change it at all: a value
  // coming from the environment outranks the file this screen writes, and
  // accepting an edit that the precedence would annul is worse than refusing
  // it -- the user would see the change saved and keep operating on the old
  // setting with no signal.
  import { open } from "@tauri-apps/plugin-dialog";

  import {
    comoFalha,
    configuracaoGravar,
    configuracaoLer,
    diagnosticoDoAplicativo,
    diagnosticoDoNucleo,
    diretorioDeDados,
    diretorioDeDadosAlterar,
    diretorioDeDadosValidar,
    dispositivos as lerDispositivos,
    mcpEstado,
    mcpRegistrar,
    type Configuracao,
    type Dispositivo,
    type Doctor,
    type DiretorioDeDados,
    type Falha,
    type ItemDeDiagnostico,
  } from "../lib/api";
  import { recado, shell } from "../lib/estado.svelte";
  import { bytes } from "../lib/format";
  import Pendencia from "../lib/components/Pendencia.svelte";

  /** Characteristics that orient the choice. The memory a model actually needs
   *  on this machine comes from the diagnostic, not from this table. */
  const MOTORES = [
    {
      id: "large-v3",
      nome: "Whisper large-v3",
      memoria: "≈ 5,6 GB de VRAM",
      velocidade: "Referência de qualidade. Decoder de 32 camadas — o mais lento.",
    },
    {
      id: "large-v3-turbo",
      nome: "Whisper large-v3-turbo",
      memoria: "≈ 3,5 GB de VRAM",
      velocidade: "Decoder de 4 camadas: bem mais rápido, com perda pequena mas real em pt-BR.",
    },
    {
      id: "medium",
      nome: "Whisper medium",
      memoria: "≈ 2,5 GB de VRAM",
      velocidade: "Rápido. Perde nomes próprios e siglas com mais frequência.",
    },
  ];

  const PAPEIS = [
    { valor: "comunicacoes", rotulo: "Comunicações" },
    { valor: "multimidia", rotulo: "Multimídia" },
  ];

  const gravacaoAtiva = $derived(shell.servico?.saude?.gravacao_ativa ?? false);
  const servicoOcupado = $derived(
    gravacaoAtiva ||
      (shell.servico?.saude?.fila_pendente ?? 0) > 0 ||
      (shell.servico?.saude?.transcrevendo ?? false),
  );

  let dados = $state<DiretorioDeDados | null>(null);
  let config = $state<Configuracao | null>(null);
  let listaDeDispositivos = $state<Dispositivo[]>([]);
  let doctor = $state<Doctor | null>(null);
  let itensDoApp = $state<ItemDeDiagnostico[]>([]);
  let trecho = $state("");

  let falhaDispositivos = $state<Falha | null>(null);
  let falhaConfig = $state<Falha | null>(null);
  let falhaDoctor = $state<Falha | null>(null);
  let falhaMcp = $state<Falha | null>(null);

  let novoCaminho = $state("");
  let vocabulario = $state("");
  let motorEscolhido = $state("large-v3");
  let papel = $state("comunicacoes");
  let micPolicy = $state("seguir_padrao");
  let systemPolicy = $state("seguir_padrao");
  let micDeviceId = $state("");
  let systemDeviceId = $state("");

  let salvando = $state(false);
  let registrando = $state(false);
  let recarregando = $state(false);

  const valor = (campo: string) => config?.valores?.[campo]?.valor ?? "";
  const origem = (campo: string) => config?.valores?.[campo]?.origem ?? "";

  const entradas = $derived(listaDeDispositivos.filter((d) => d.fluxo === "entrada"));
  const saidas = $derived(listaDeDispositivos.filter((d) => d.fluxo === "saida"));

  /** A pinned device that is no longer present will make the next recording
   *  fail, and saying so now is the whole difference between a tool that warns
   *  and one that surprises. */
  function fixadoIndisponivel(politica: string, id: string, fluxo: "entrada" | "saida") {
    if (politica !== "fixado" || !id) return false;
    return !listaDeDispositivos.some((d) => d.fluxo === fluxo && d.id === id);
  }

  const micFixadoSumiu = $derived(fixadoIndisponivel(micPolicy, micDeviceId, "entrada"));
  const systemFixadoSumiu = $derived(fixadoIndisponivel(systemPolicy, systemDeviceId, "saida"));

  /** The output the system track will actually capture from, given the current
   *  choice. Shown explicitly because "follow the role" is not a device name. */
  const saidaEfetiva = $derived.by(() => {
    if (systemPolicy === "fixado") {
      return saidas.find((d) => d.id === systemDeviceId)?.nome ?? "dispositivo fixado ausente";
    }
    return saidas.find((d) => d.padrao_de.includes(papel))?.nome ?? "nenhum";
  });

  const entradaEfetiva = $derived.by(() => {
    if (micPolicy === "fixado") {
      return entradas.find((d) => d.id === micDeviceId)?.nome ?? "dispositivo fixado ausente";
    }
    return entradas.find((d) => d.padrao_de.includes(papel))?.nome ?? "nenhum";
  });

  $effect(() => {
    void recarregar();
  });

  async function recarregar() {
    recarregando = true;
    try {
      dados = await diretorioDeDados();
      itensDoApp = await diagnosticoDoAplicativo();

      try {
        config = await configuracaoLer();
        falhaConfig = null;
        vocabulario = valor("vocabulary");
        motorEscolhido = valor("model") || "large-v3";
        papel = valor("device_role") || "comunicacoes";
        micPolicy = valor("mic_policy") || "seguir_padrao";
        systemPolicy = valor("system_policy") || "seguir_padrao";
        micDeviceId = valor("mic_device_id");
        systemDeviceId = valor("system_device_id");
      } catch (erro) {
        falhaConfig = comoFalha(erro);
      }

      try {
        listaDeDispositivos = (await lerDispositivos()).dispositivos;
        falhaDispositivos = null;
      } catch (erro) {
        falhaDispositivos = comoFalha(erro);
      }

      try {
        doctor = await diagnosticoDoNucleo();
        falhaDoctor = null;
      } catch (erro) {
        falhaDoctor = comoFalha(erro);
      }

      try {
        trecho = await mcpEstado();
        falhaMcp = null;
      } catch (erro) {
        falhaMcp = comoFalha(erro);
      }
    } finally {
      recarregando = false;
    }
  }

  async function gravarCampo(atribuicoes: string[], sucesso: string) {
    salvando = true;
    try {
      await configuracaoGravar(atribuicoes);
      recado("ok", sucesso);
      recado(
        "info",
        "O serviço residente lê a configuração ao iniciar; a mudança vale a partir do próximo início dele.",
      );
      await recarregar();
    } catch (erro) {
      recado("erro", comoFalha(erro).mensagem);
    } finally {
      salvando = false;
    }
  }

  const gravarDispositivos = () =>
    gravarCampo(
      [
        `device_role=${papel}`,
        `mic_policy=${micPolicy}`,
        `system_policy=${systemPolicy}`,
        `mic_device_id=${micPolicy === "fixado" ? micDeviceId : ""}`,
        `system_device_id=${systemPolicy === "fixado" ? systemDeviceId : ""}`,
      ],
      "Escolha de dispositivos gravada na configuração compartilhada.",
    );

  async function escolherDiretorio() {
    const escolha = await open({ directory: true, title: "Escolha o diretório de dados" });
    if (typeof escolha !== "string") return;
    novoCaminho = escolha;
    try {
      const prova = await diretorioDeDadosValidar(escolha);
      recado("ok", `Caminho gravável. ${bytes(prova.livre_bytes)} livres no volume escolhido.`);
    } catch (erro) {
      recado("erro", comoFalha(erro).mensagem);
    }
  }

  async function aplicarDiretorio() {
    if (!novoCaminho.trim()) return;
    salvando = true;
    try {
      recado("ok", await diretorioDeDadosAlterar(novoCaminho.trim()));
      recado(
        "info",
        "O serviço residente precisa reiniciar para apontar para o novo diretório. " +
          "Ele se encerra sozinho por ociosidade e volta na próxima ação; a barra de " +
          "estado confirma para onde ele voltou.",
      );
      await recarregar();
      novoCaminho = "";
    } catch (erro) {
      recado("erro", comoFalha(erro).mensagem);
    } finally {
      salvando = false;
    }
  }

  async function copiarTrecho() {
    await navigator.clipboard.writeText(trecho);
    recado("ok", "Conteúdo copiado.");
  }

  async function registrarMcp() {
    registrando = true;
    try {
      trecho = await mcpRegistrar();
      recado("ok", "Registro do servidor MCP gravado nos clientes encontrados.");
      recado("info", "Reinicie o cliente de agente para que ele leia o registro novo.");
    } catch (erro) {
      recado("erro", comoFalha(erro).mensagem);
    } finally {
      registrando = false;
    }
  }

  /** The inference item from the diagnostic is what says whether the selected
   *  model can run here at all, before any transcription is attempted. */
  const itemDeInferencia = $derived(doctor?.itens.find((i) => i.chave === "inferencia") ?? null);
</script>

<div class="corpo">
  <div class="coluna">
    <!-- Devices -->
    <section class="cartao">
      <header>
        <h2>Dispositivos de áudio</h2>
        {#if gravacaoAtiva}
          <span class="selo erro">indisponível durante a gravação</span>
        {/if}
      </header>

      <p class="legenda">
        O Windows mantém dois padrões separados: o de <strong>comunicações</strong>, que
        as plataformas de videoconferência seguem, e o de <strong>multimídia</strong>,
        que o restante do sistema usa. Eles podem apontar para dispositivos
        diferentes. O VoxVault segue o de comunicações por omissão, porque é o que
        a reunião usa.
      </p>

      <div class="nota alerta">
        <strong>A trilha do sistema captura tudo</strong>
        <p style="margin:0">
          Ela grava toda a mistura reproduzida pelo dispositivo de saída escolhido —
          não apenas o áudio da reunião. Um vídeo tocando em outra aba entra na
          gravação e será transcrito como se fosse fala de participante.
        </p>
      </div>

      {#if gravacaoAtiva}
        <div class="nota erro" style="margin-top:12px">
          <strong>Alteração bloqueada</strong>
          <p style="margin:0">
            Há uma gravação em andamento. Trocar de dispositivo agora derrubaria a
            captura em curso.
          </p>
        </div>
      {:else if falhaDispositivos}
        <div style="margin-top:12px">
          <Pendencia falha={falhaDispositivos} titulo="Lista de dispositivos indisponível" />
        </div>
      {:else}
        <label class="campo" style="margin:14px 0">
          Papel a seguir quando não houver dispositivo fixado
          <select bind:value={papel} style="width:auto">
            {#each PAPEIS as p (p.valor)}
              <option value={p.valor}>{p.rotulo}</option>
            {/each}
          </select>
        </label>

        <div class="grade-dupla">
          {#each [{ titulo: "Microfone (trilha de entrada)", fluxo: "entrada", lista: entradas, politica: micPolicy, id: micDeviceId, sumiu: micFixadoSumiu, efetivo: entradaEfetiva }, { titulo: "Saída (trilha do sistema)", fluxo: "saida", lista: saidas, politica: systemPolicy, id: systemDeviceId, sumiu: systemFixadoSumiu, efetivo: saidaEfetiva }] as bloco (bloco.fluxo)}
            <div>
              <h3 style="margin-bottom:6px">{bloco.titulo}</h3>
              <p class="legenda" style="margin-bottom:8px">
                Será usado: <strong>{bloco.efetivo}</strong>
              </p>

              <label class="campo" style="margin-bottom:8px">
                <span>
                  <input
                    type="radio"
                    checked={bloco.politica === "seguir_padrao"}
                    onchange={() => {
                      if (bloco.fluxo === "entrada") micPolicy = "seguir_padrao";
                      else systemPolicy = "seguir_padrao";
                    }}
                    style="width:auto"
                  />
                  Seguir o padrão do papel escolhido
                </span>
              </label>
              <label class="campo">
                <span>
                  <input
                    type="radio"
                    checked={bloco.politica === "fixado"}
                    onchange={() => {
                      if (bloco.fluxo === "entrada") micPolicy = "fixado";
                      else systemPolicy = "fixado";
                    }}
                    style="width:auto"
                  />
                  Fixar um dispositivo
                </span>
                <select
                  disabled={bloco.politica !== "fixado"}
                  value={bloco.id}
                  onchange={(e) => {
                    const v = (e.currentTarget as HTMLSelectElement).value;
                    if (bloco.fluxo === "entrada") micDeviceId = v;
                    else systemDeviceId = v;
                  }}
                >
                  <option value="">— escolha —</option>
                  {#each bloco.lista as d (d.id)}
                    <option value={d.id}>
                      {d.nome}{d.padrao_de.length ? ` · padrão de ${d.padrao_de.join(", ")}` : ""}
                    </option>
                  {/each}
                  {#if bloco.sumiu}
                    <option value={bloco.id}>{bloco.id} (não está mais disponível)</option>
                  {/if}
                </select>
              </label>

              {#if bloco.sumiu}
                <div class="nota erro" style="margin-top:8px">
                  <strong>Dispositivo fixado indisponível</strong>
                  <p style="margin:0">
                    O dispositivo escolhido não está mais presente. A gravação
                    desta trilha vai falhar até que a escolha seja corrigida.
                  </p>
                </div>
              {/if}
            </div>
          {/each}
        </div>

        {#if saidas.length > 0 && !saidas.some((d) => d.parece_fone)}
          <div class="nota alerta" style="margin-top:12px">
            <strong>Risco de eco</strong>
            <p style="margin:0">
              Nenhuma saída disponível parece ser um fone. Sem fone, o microfone
              capta a voz dos outros e o mesmo trecho aparece nas duas trilhas.
            </p>
          </div>
        {/if}

        <details style="margin-top:12px">
          <summary class="legenda" style="cursor:pointer">
            Ver todos os dispositivos e seus identificadores persistentes
          </summary>
          <div style="margin-top:8px;display:flex;flex-direction:column;gap:6px">
            {#each listaDeDispositivos as d (d.id)}
              <div class="nota" style="padding:8px 11px">
                <div class="linha" style="justify-content:space-between">
                  <strong style="margin:0">{d.nome}</strong>
                  <span class="linha">
                    <span class="selo">{d.fluxo}</span>
                    <span class={`selo ${d.estado === "ativo" ? "ok" : "erro"}`}>{d.estado}</span>
                    {#if d.parece_fone}<span class="selo acento">fone</span>{/if}
                  </span>
                </div>
                {#if d.padrao_de.length}
                  <div class="legenda">padrão de: {d.padrao_de.join(", ")}</div>
                {/if}
                <div class="mono" style="word-break:break-all;opacity:.7">{d.id}</div>
              </div>
            {/each}
          </div>
        </details>

        <div class="linha fim" style="margin-top:12px">
          <button class="botao primario" disabled={salvando} onclick={gravarDispositivos}>
            Gravar escolha de dispositivos
          </button>
        </div>
      {/if}
    </section>

    <!-- Engine -->
    <section class="cartao">
      <header>
        <h2>Motor de transcrição</h2>
        <span class="legenda">Vale para as próximas transcrições</span>
      </header>

      <p class="legenda">
        Trocar o modelo não altera nada que já foi transcrito. Ele passa a valer
        para as próximas reuniões e para reprocessamentos que você pedir.
      </p>

      <div style="display:flex;flex-direction:column;gap:8px;margin:12px 0">
        {#each MOTORES as motor (motor.id)}
          <label
            class="nota"
            class:acento={motorEscolhido === motor.id}
            style="display:flex;gap:10px;align-items:flex-start;cursor:pointer"
          >
            <input
              type="radio"
              name="motor"
              value={motor.id}
              bind:group={motorEscolhido}
              style="width:auto;margin-top:3px"
            />
            <span>
              <strong>{motor.nome}</strong>
              <span class="legenda">{motor.memoria} · {motor.velocidade}</span>
            </span>
          </label>
        {/each}
      </div>

      {#if itemDeInferencia}
        <div class="nota" class:erro={itemDeInferencia.estado === "falha"} class:acento={itemDeInferencia.estado === "ok"}>
          <strong>Ambiente de inferência</strong>
          <p style="margin:0">{itemDeInferencia.detalhe}</p>
          {#if itemDeInferencia.acao}
            <p class="legenda" style="margin:6px 0 0">{itemDeInferencia.acao}</p>
          {/if}
        </div>
      {/if}

      <div class="linha fim" style="margin-top:12px">
        <button
          class="botao primario"
          disabled={salvando}
          onclick={() =>
            gravarCampo([`model=${motorEscolhido}`], "Modelo gravado na configuração compartilhada.")}
        >
          Gravar o modelo
        </button>
      </div>
    </section>

    <!-- Data directory -->
    <section class="cartao">
      <header>
        <h2>Diretório de dados</h2>
        {#if dados}<span class="selo">origem: {origem("data_dir") || dados.fonte_legivel}</span>{/if}
      </header>

      {#if dados}
        <p class="mono" style="word-break:break-all">{dados.caminho}</p>
        <p class="legenda">
          {bytes(dados.livre_bytes)} livres de {bytes(dados.total_bytes)}. Uma hora de
          reunião ocupa cerca de {dados.mb_por_hora_gravando} MB enquanto grava e
          cerca de {dados.mb_por_hora_comprimido} MB depois de comprimida.
        </p>

        {#if dados.aviso}
          <div class="nota erro"><strong>Espaço baixo</strong><p style="margin:0">{dados.aviso}</p></div>
        {/if}

        {#if shell.servico?.data_dir_do_servico && shell.servico.data_dir_do_servico !== dados.caminho}
          <div class="nota alerta">
            <strong>O serviço está em outro diretório</strong>
            <p style="margin:0">
              O serviço residente em execução resolveu
              <code class="mono">{shell.servico.data_dir_do_servico}</code>, enquanto
              este aplicativo resolve <code class="mono">{dados.caminho}</code>.
              Ele resolve o diretório ao iniciar; reinicie-o para que os dois
              coincidam.
            </p>
          </div>
        {/if}

        {#if !dados.alteravel}
          <div class="nota alerta" style="margin-top:12px">
            <strong>Somente leitura</strong>
            <p>
              O diretório efetivo vem da {dados.fonte_legivel}, que tem precedência
              sobre o arquivo de configuração. Gravar a alteração aqui não surtiria
              efeito, e você continuaria operando sobre o armazenamento antigo sem
              nenhum sinal.
            </p>
            <p style="margin:0">
              Para que a configuração volte a valer, remova a variável
              <code class="mono">VOXVAULT_DATA_DIR</code> do seu ambiente e reabra o
              aplicativo.
            </p>
          </div>
        {:else}
          <div class="linha" style="margin-top:12px">
            <input type="text" bind:value={novoCaminho} placeholder="Novo caminho" style="flex:1;min-width:220px" />
            <button class="botao" onclick={escolherDiretorio}>Escolher...</button>
            <button
              class="botao primario"
              disabled={!novoCaminho.trim() || salvando || servicoOcupado}
              title={servicoOcupado
                ? "Há gravação ativa, transcrição em andamento ou sessões pendentes na fila. O trabalho pendente se refere a arquivos do diretório atual."
                : ""}
              onclick={aplicarDiretorio}
            >
              Alterar
            </button>
          </div>
          {#if servicoOcupado}
            <p class="legenda" style="margin-top:8px">
              Alteração indisponível: há trabalho pendente que se refere a arquivos
              do diretório atual.
            </p>
          {/if}
          <p class="legenda" style="margin-top:8px">
            Alterar o diretório não move nem apaga nada: o conteúdo anterior
            permanece onde está. Servidores MCP em execução continuam ligados ao
            diretório anterior até que a sessão do cliente seja reiniciada.
          </p>
        {/if}
      {/if}
    </section>

    <!-- Vocabulary -->
    <section class="cartao">
      <header>
        <h2>Vocabulário de domínio</h2>
        {#if config}<span class="selo">origem: {origem("vocabulary")}</span>{/if}
      </header>
      <p class="legenda">
        Nomes, siglas e jargão do time. O vocabulário é entregue ao modelo antes da
        transcrição e reduz erros em palavras que ele não conhece. Alterá-lo não
        muda nada já transcrito.
      </p>
      <textarea
        bind:value={vocabulario}
        placeholder="Ex.: Perssua, VoxVault, WASAPI, Eriel, Layane, CTranslate2"
      ></textarea>
      <div class="linha fim" style="margin-top:10px">
        <button
          class="botao primario"
          disabled={salvando}
          onclick={() => gravarCampo([`vocabulary=${vocabulario}`], "Vocabulário global gravado.")}
        >
          Gravar o vocabulário global
        </button>
      </div>
      <p class="legenda" style="margin-top:10px">
        O vocabulário específico por reunião ainda não tem onde ser guardado no
        núcleo: a configuração compartilhada é global. Ele será oferecido a partir
        da própria reunião quando o núcleo aceitar esse campo.
      </p>
    </section>

    <!-- Diagnostics -->
    <section class="cartao" id="diagnostico">
      <header>
        <h2>Diagnóstico de ambiente</h2>
        <button class="botao discreto" onclick={recarregar} disabled={recarregando}>
          {recarregando ? "Verificando..." : "Reexecutar"}
        </button>
      </header>

      {#if doctor}
        <div style="display:flex;flex-direction:column;gap:8px;margin-bottom:10px">
          {#each doctor.itens as item (item.chave)}
            <div
              class="nota"
              class:erro={item.estado === "falha"}
              class:alerta={item.estado === "aviso"}
              class:acento={item.estado === "ok"}
            >
              <div class="linha" style="justify-content:space-between">
                <strong style="margin:0">{item.rotulo}</strong>
                <span class={`selo ${item.estado === "ok" ? "ok" : item.estado === "aviso" ? "alerta" : "erro"}`}>
                  {item.estado === "ok" ? "em ordem" : item.estado === "aviso" ? "atenção" : "em falha"}
                </span>
              </div>
              <p class="legenda" style="margin:6px 0 0;white-space:pre-wrap">{item.detalhe}</p>
              {#if item.acao}
                <p class="legenda" style="margin:6px 0 0"><strong>O que fazer:</strong> {item.acao}</p>
              {/if}
            </div>
          {/each}
        </div>
      {:else if falhaDoctor}
        <Pendencia falha={falhaDoctor} titulo="Diagnóstico do núcleo indisponível" />
      {/if}

      <details>
        <summary class="legenda" style="cursor:pointer">
          Verificações do próprio aplicativo
        </summary>
        <div style="display:flex;flex-direction:column;gap:8px;margin-top:8px">
          {#each itensDoApp as item (item.nome)}
            <div class="nota" class:erro={!item.ok}>
              <div class="linha" style="justify-content:space-between">
                <strong style="margin:0">{item.nome}</strong>
                <span class={`selo ${item.ok ? "ok" : "erro"}`}>
                  {item.ok ? "em ordem" : "em falha"}
                </span>
              </div>
              <p class="legenda" style="margin:6px 0 0;white-space:pre-wrap">{item.detalhe}</p>
              {#if item.acao}
                <p class="legenda" style="margin:6px 0 0"><strong>O que fazer:</strong> {item.acao}</p>
              {/if}
            </div>
          {/each}
        </div>
      </details>

      {#if falhaConfig}
        <div style="margin-top:12px">
          <Pendencia falha={falhaConfig} titulo="Configuração efetiva indisponível" />
        </div>
      {:else if config}
        <details style="margin-top:12px">
          <summary class="legenda" style="cursor:pointer">
            Configuração efetiva, com a origem de cada valor
          </summary>
          <p class="legenda" style="margin:8px 0 4px">
            Arquivo: <code class="mono">{config.arquivo}</code>
          </p>
          <div style="display:grid;grid-template-columns:auto 1fr auto;gap:2px 12px;font-size:12px">
            {#each Object.entries(config.valores) as [campo, v] (campo)}
              <span class="mono">{campo}</span>
              <span class="mono" style="word-break:break-all">{v.valor || "—"}</span>
              <span class="fraco">{v.origem}</span>
            {/each}
          </div>
        </details>
      {/if}
    </section>

    <!-- MCP -->
    <section class="cartao">
      <header>
        <h2>Registro do servidor MCP</h2>
        <div class="linha">
          <button class="botao discreto" onclick={copiarTrecho} disabled={!trecho}>Copiar</button>
          <button class="botao primario" onclick={registrarMcp} disabled={registrando}>
            {registrando ? "Registrando..." : "Registrar automaticamente"}
          </button>
        </div>
      </header>
      <p class="legenda">
        Com o servidor MCP registrado, o seu cliente de agente lê as transcrições
        mesmo com o VoxVault fechado — ele abre o banco direto, em modo somente
        leitura. O registro automático copia o arquivo de configuração atual antes
        de alterá-lo.
      </p>
      {#if falhaMcp}
        <Pendencia falha={falhaMcp} titulo="Registro MCP indisponível" />
      {:else}
        <pre class="trecho">{trecho}</pre>
      {/if}
    </section>
  </div>
</div>
