<script lang="ts">
  // Settings: the choices that change how the tool behaves, in one place, plus
  // the environment diagnostic made readable for someone who is not going to
  // open a terminal.
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
    trechoMcp,
    type DiretorioDeDados,
    type Falha,
    type ItemDeDiagnostico,
  } from "../lib/api";
  import { recado, shell } from "../lib/estado.svelte";
  import { bytes } from "../lib/format";
  import Pendencia from "../lib/components/Pendencia.svelte";

  /** Characteristics that orient the choice, from the project's own design
   *  notes. The memory a model actually needs on this machine is computed by
   *  the core's diagnostic, which is why it is labelled approximate here. */
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
      velocidade:
        "Decoder de 4 camadas: bem mais rápido, com perda pequena mas real em pt-BR.",
    },
    {
      id: "medium",
      nome: "Whisper medium",
      memoria: "≈ 2,5 GB de VRAM",
      velocidade: "Rápido. Perde nomes próprios e siglas com mais frequência.",
    },
  ];

  const gravacaoAtiva = $derived(shell.servico?.saude?.gravacao_ativa ?? false);
  const servicoOcupado = $derived(
    gravacaoAtiva ||
      (shell.servico?.saude?.fila_pendente ?? 0) > 0 ||
      (shell.servico?.saude?.transcrevendo ?? false),
  );

  let dados = $state<DiretorioDeDados | null>(null);
  let novoCaminho = $state("");
  let falhaDispositivos = $state<Falha | null>(null);
  let falhaConfig = $state<Falha | null>(null);
  let falhaDoctor = $state<Falha | null>(null);
  let itens = $state<ItemDeDiagnostico[]>([]);
  let trecho = $state("");
  let vocabulario = $state("");
  let motorEscolhido = $state("large-v3");
  let salvando = $state(false);

  $effect(() => {
    void recarregar();
  });

  async function recarregar() {
    dados = await diretorioDeDados();
    itens = await diagnosticoDoAplicativo();
    trecho = await trechoMcp();
    try {
      await lerDispositivos();
      falhaDispositivos = null;
    } catch (erro) {
      falhaDispositivos = comoFalha(erro);
    }
    try {
      await configuracaoLer();
      falhaConfig = null;
    } catch (erro) {
      falhaConfig = comoFalha(erro);
    }
    try {
      await diagnosticoDoNucleo();
      falhaDoctor = null;
    } catch (erro) {
      falhaDoctor = comoFalha(erro);
    }
  }

  async function escolherDiretorio() {
    const escolha = await open({ directory: true, title: "Escolha o diretório de dados" });
    if (typeof escolha !== "string") return;
    novoCaminho = escolha;
    try {
      const prova = await diretorioDeDadosValidar(escolha);
      recado(
        "ok",
        `Caminho gravável. ${bytes(prova.livre_bytes)} livres no volume escolhido.`,
      );
    } catch (erro) {
      recado("erro", comoFalha(erro).mensagem);
    }
  }

  async function aplicarDiretorio() {
    if (!novoCaminho.trim()) return;
    salvando = true;
    try {
      const mensagem = await diretorioDeDadosAlterar(novoCaminho.trim());
      recado("ok", mensagem);
      // The resident service resolves the data directory when it starts, so the
      // change does not take effect until it comes back. Saying "gravado" and
      // stopping there would leave the user operating on the old storage.
      recado(
        "info",
        "O serviço residente precisa reiniciar para passar a apontar para o novo " +
          "diretório. Ele se encerra sozinho por ociosidade e volta na próxima " +
          "ação; a barra de estado confirma para onde ele voltou.",
      );
      await recarregar();
      novoCaminho = "";
    } catch (erro) {
      recado("erro", comoFalha(erro).mensagem);
    } finally {
      salvando = false;
    }
  }

  async function gravarCampo(atribuicoes: string[], sucesso: string) {
    salvando = true;
    try {
      await configuracaoGravar(atribuicoes);
      recado("ok", sucesso);
    } catch (erro) {
      recado("erro", comoFalha(erro).mensagem);
    } finally {
      salvando = false;
    }
  }

  async function copiarTrecho() {
    await navigator.clipboard.writeText(trecho);
    recado("ok", "Trecho de configuração copiado.");
  }
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

      <div class="nota alerta">
        <strong>Não é possível verificar se o modelo cabe neste ambiente</strong>
        <p style="margin:0">
          A memória livre da GPU e a usabilidade do runtime de inferência vêm do
          diagnóstico do núcleo, que ainda não tem saída legível por máquina.
          Até lá, a incompatibilidade só apareceria na primeira transcrição.
        </p>
      </div>

      <div class="linha fim" style="margin-top:12px">
        <button
          class="botao primario"
          disabled={salvando}
          onclick={() => gravarCampo([`model=${motorEscolhido}`], "Modelo gravado na configuração compartilhada.")}
        >
          Gravar o modelo
        </button>
      </div>
    </section>

    <!-- Data directory -->
    <section class="cartao">
      <header>
        <h2>Diretório de dados</h2>
        {#if dados}<span class="selo">origem: {dados.fonte_legivel}</span>{/if}
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
      <header><h2>Vocabulário de domínio</h2></header>
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
        <button class="botao discreto" onclick={recarregar}>Reexecutar</button>
      </header>

      <div style="display:flex;flex-direction:column;gap:8px">
        {#each itens as item (item.nome)}
          <div class="nota" class:erro={!item.ok} class:acento={item.ok}>
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

      {#if falhaDoctor}
        <div style="margin-top:12px">
          <Pendencia falha={falhaDoctor} titulo="Diagnóstico do núcleo indisponível" />
          <p class="legenda">
            Os itens acima são os que o aplicativo verifica por conta própria. Os do
            núcleo — bibliotecas de áudio, decodificador de mídia, runtime de
            inferência e captura — só aparecem aqui quando o comando publicar saída
            legível por máquina.
          </p>
        </div>
      {/if}

      {#if falhaConfig}
        <div style="margin-top:12px">
          <Pendencia falha={falhaConfig} titulo="Configuração efetiva indisponível" />
        </div>
      {/if}
    </section>

    <!-- MCP -->
    <section class="cartao">
      <header>
        <h2>Registro do servidor MCP</h2>
        <button class="botao discreto" onclick={copiarTrecho}>Copiar</button>
      </header>
      <p class="legenda">
        Adicione este trecho à configuração do seu cliente de agente para que ele
        leia as transcrições mesmo com o VoxVault fechado.
      </p>
      <pre class="trecho">{trecho}</pre>
    </section>
  </div>
</div>
