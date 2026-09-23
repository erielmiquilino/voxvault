// Typed bridge to the host process.
//
// Every failure crossing this boundary is a `Falha`: a cause in pt-BR, an
// optional corrective action, and -- when the obstacle is that the core has no
// machine-readable output for something yet -- the exact command and flag that
// would remove it. The interface uses `pendencia` to disable an action and
// explain it up front rather than letting it fail at the click.

import { invoke } from "@tauri-apps/api/core";

export interface Falha {
  mensagem: string;
  acao: string | null;
}

export function ehFalha(valor: unknown): valor is Falha {
  return typeof valor === "object" && valor !== null && "mensagem" in valor;
}

/** Normalises anything thrown across the bridge into a `Falha`. */
export function comoFalha(erro: unknown): Falha {
  if (ehFalha(erro)) return erro;
  return { mensagem: String(erro), acao: null };
}

export type EstadoDoServico =
  | "desconhecido"
  | "procurando"
  | "conectado"
  | "indisponivel"
  | "falho";

export interface Saude {
  gravacao_ativa: boolean;
  fila_pendente: number;
  transcrevendo: boolean;
  data_dir: string;
  versao: string;
}

/** The service's own view of the recording in progress. */
export interface GravacaoView {
  ativa: boolean;
  pausada: boolean;
  uid: string;
  titulo: string;
  inicio: string;
  duracao_ms: number;
  divergencia_ms: number;
  /** Track name to milliseconds written. A track that stopped capturing stops
   *  growing, which is how the interface knows which one died. */
  trilhas: Record<string, number>;
  avisos: string[];
  /** Per-track peak since the last read, plus how long that track has been
   *  silent. Reading resets the peak, so the update rate is this app's polling
   *  rate and the 20/s cap is respected by construction. */
  niveis: Record<string, { pico: number; silencio_ha_s: number }>;
}

/** Whether a meeting looks like it has started, and on what evidence.
 *  A suggestion only: nothing here ever starts a recording on its own. */
export interface Deteccao {
  disponivel: boolean;
  motivo?: string;
  gravando?: boolean;
  deteccao: {
    aplicativo: string;
    sinal: string;
    /** True when the evidence is circumstantial — a browser, which says less
     *  about what it is doing than a meeting client does. */
    fraco: boolean;
    sustentado_ha_s: number;
  } | null;
  candidatos?: { aplicativo: string; sinal: string }[];
}

export interface ServicoSnapshot {
  estado: EstadoDoServico;
  endereco: string | null;
  data_dir_do_servico: string | null;
  saude: Saude | null;
  gravacao: GravacaoView | null;
  deteccao: Deteccao | null;
  detalhe: string;
  acao: string | null;
  tentativas_recentes: number;
  pode_tentar_de_novo: boolean;
  caminho_do_log: string | null;
  pid_do_servico: number | null;
}

export type SituacaoDoAmbiente =
  | { tipo: "pronto" }
  | { tipo: "desenvolvimento" }
  | { tipo: "primeiro"; retomada: boolean }
  | { tipo: "desatualizado"; motivo: string }
  | { tipo: "sem_recursos" };

export interface Ambiente {
  preparado: boolean;
  situacao: SituacaoDoAmbiente;
  raiz_do_nucleo: string | null;
  executavel: string | null;
  detalhe: string;
  acao: string | null;
}

export type Situacao =
  | "gravando"
  | "na_fila"
  | "transcrevendo"
  | "pronta"
  | "falhou"
  | "incompleta";

export interface Resumo {
  uid: string;
  titulo: string;
  inicio: string;
  fim: string;
  duracao_ms: number;
  situacao: Situacao;
  /** Availability and attempt stay separate: a meeting being reprocessed is
   *  readable and busy at the same time. */
  transcricao_disponivel: boolean;
  completude: string;
  revisao_ativa: string;
  estado_da_tentativa: string;
  motivo_da_falha: string | null;
  origem: string;
  tem_audio: boolean;
  tem_notas: boolean;
  avisos: string[];
  diretorio: string;
  motor: string;
}

export interface Segmento {
  id: number;
  trilha: string;
  falante: string;
  inicio_ms: number;
  fim_ms: number;
  texto: string;
  sobreposto: boolean;
}

export interface Trilha {
  nome: string;
  caminho: string;
  bytes: number;
}

export type TipoDeNota = "resumo" | "decisoes" | "pendencias" | "livre";

export interface Nota {
  uid: string;
  tipo: string;
  conteudo: string;
  autoria_tipo: string;
  autoria_cliente: string;
  criada_em: string;
  alterada_em: string;
}

/** Per-meeting content. The summary is not repeated here — the caller already
 *  holds the core's authoritative one from the list. */
export interface Detalhe {
  segmentos: Segmento[];
  notas: Nota[];
  trilhas: Trilha[];
  dispositivos: unknown;
  pausas: unknown;
  alinhamento: unknown;
  revisao: unknown;
  caminho_legivel: string | null;
  caminho_estruturado: string | null;
}

export interface DiretorioDeDados {
  caminho: string;
  fonte: "environment" | "config_file" | "built_in_default" | "padrao_anterior";
  fonte_legivel: string;
  alteravel: boolean;
  existe: boolean;
  livre_bytes: number | null;
  total_bytes: number | null;
  mb_por_hora_gravando: number;
  mb_por_hora_comprimido: number;
  aviso: string | null;
}

export interface ItemDeDiagnostico {
  nome: string;
  ok: boolean;
  detalhe: string;
  acao: string | null;
}

export interface ResultadoImportacao {
  arquivo: string;
  ok: boolean;
  detalhe: string;
}

// -- environment and service ------------------------------------------------

export const ambienteEstado = () => invoke<Ambiente>("ambiente_estado");

export interface Gpu {
  nome: string;
  memoria_mb: number;
}

export interface ItemDoPlano {
  etapa: string;
  rotulo: string;
  bytes: number;
  bytes_em_disco: number;
  presente: boolean;
  volume: string;
}

export interface VolumeDoPlano {
  raiz: string;
  livre: number | null;
  necessario: number;
  falta: number;
}

export interface PlanoDoPreparo {
  hardware: { gpu: Gpu | null; cpu_forcada: boolean; detalhe: string };
  escolha: { modelo: string; dispositivo: string; etapa_gpu: boolean };
  pasta: string;
  pasta_origem: DiretorioDeDados["fonte"];
  pasta_origem_legivel: string;
  itens: ItemDoPlano[];
  total_bytes: number;
  volumes: VolumeDoPlano[];
  pode_iniciar: boolean;
  recusa: string | null;
  situacao: SituacaoDoAmbiente;
}

/** One step's news: `em_andamento`, `concluida`, `pulada` or `falhou`. */
export interface ProgressoDoPreparo {
  etapa: string;
  estado: "em_andamento" | "concluida" | "pulada" | "falhou";
  detalhe: string;
  baixado: number | null;
  total: number | null;
}

export const preparoPlano = (pasta: string | null) =>
  invoke<PlanoDoPreparo>("preparo_plano", { pasta });
export const preparoIniciar = (pasta: string | null) =>
  invoke<string>("preparo_iniciar", { pasta });
export const modeloBaixar = (modelo: string) => invoke<string>("modelo_baixar", { modelo });
export const servicoEstado = () => invoke<ServicoSnapshot>("servico_estado");
export const servicoRearmar = () => invoke<ServicoSnapshot>("servico_rearmar");

// -- recording ---------------------------------------------------------------

export const gravacaoEstado = () => invoke<unknown>("gravacao_estado");
export const gravacaoIniciar = (titulo: string) =>
  invoke<unknown>("gravacao_iniciar", { titulo });
export const gravacaoPausar = () => invoke<unknown>("gravacao_pausar");
export const gravacaoRetomar = () => invoke<unknown>("gravacao_retomar");
export const gravacaoEncerrar = () => invoke<unknown>("gravacao_encerrar");

// -- library -----------------------------------------------------------------

export const reunioesListar = (limite = 200) => invoke<Resumo[]>("reunioes_listar", { limite });
export const reuniaoDetalhar = (uid: string, diretorio: string, revisaoAtiva: string | null) =>
  invoke<Detalhe>("reuniao_detalhar", { uid, diretorio, revisaoAtiva });
export const reuniaoDoNucleo = (uid: string) => invoke<unknown>("reuniao_do_nucleo", { uid });
export const reuniaoReprocessar = (uid: string) => invoke<string>("reuniao_reprocessar", { uid });
export const reuniaoExportar = (uid: string) => invoke<string[]>("reuniao_exportar", { uid });
export const reuniaoRenomear = (uid: string, titulo: string) =>
  invoke<string>("reuniao_renomear", { uid, titulo });
/** Dry run: the core describes what it would remove, and that description is
 *  what the confirmation shows. */
export const reuniaoRemoverAudioPrevia = (uid: string) =>
  invoke<string>("reuniao_remover_audio_previa", { uid });
export const reuniaoRemoverAudio = (uid: string) =>
  invoke<string>("reuniao_remover_audio", { uid });
/** One meeting of a deletion report, exactly as `voxvault delete --json`
 *  prints it. `motivo` is for a person, `causa` is the same refusal as a word
 *  to branch on; both are empty when nothing refuses. `resultado` only exists
 *  once the deletion was confirmed. */
export interface ItemDaExclusao {
  uid: string;
  titulo: string;
  inicio: string | null;
  duracao_ms: number;
  revisoes: number;
  notas: number;
  diretorio: string;
  arquivos: { caminho: string; bytes: number }[];
  bytes: number;
  motivo: string;
  causa: "" | "gravando" | "transcrevendo" | "em_uso" | "inexistente" | "identificador" | "falha";
  resultado?: "excluida" | "recusada";
}

export interface Exclusao {
  itens: ItemDaExclusao[];
  /** Sums over the meetings that are, or would be, deleted. */
  total: { reunioes: number; duracao_ms: number; revisoes: number; notas: number; bytes: number };
}

/** Dry run: what deleting these meetings would remove. Changes nothing. */
export const reunioesExcluirPrevia = (uids: string[]) =>
  invoke<Exclusao>("reunioes_excluir_previa", { uids });
/** Deletes for good; each meeting is deleted or refused on its own. */
export const reunioesExcluir = (uids: string[]) =>
  invoke<Exclusao>("reunioes_excluir", { uids });
export const reuniaoAbrirPasta = (diretorio: string) =>
  invoke<void>("reuniao_abrir_pasta", { diretorio });
export const abrirCaminho = (caminho: string) => invoke<void>("abrir_caminho", { caminho });

export type EscopoDeBusca = "transcricoes" | "notas" | "ambos";

/** A transcript hit carries an instant and a speaker; a note hit carries
 *  neither, because a note has no position in the timeline. */
export interface ResultadoDeBusca {
  reuniao: string;
  titulo: string;
  natureza: "transcricao" | "nota";
  recorte: string;
  texto: string;
  inicio_ms?: number;
  fim_ms?: number;
  falante?: string;
  trilha?: string;
  nota?: string;
  tipo?: string;
}

export const buscar = (termo: string, escopo: EscopoDeBusca = "ambos", limite = 50) =>
  invoke<{ termo: string; escopo: string; resultados: ResultadoDeBusca[] }>("busca", {
    termo,
    escopo,
    limite,
  });

// Notes are read from the meeting's structured export, which the core
// regenerates on every note write; there is no separate listing call.
export const notaCriar = (uid: string, tipo: TipoDeNota, conteudo: string) =>
  invoke<string>("nota_criar", { uid, tipo, conteudo });
export const notaAlterar = (id: string, conteudo: string) =>
  invoke<string>("nota_alterar", { id, conteudo });
export const notaRemover = (id: string) => invoke<string>("nota_remover", { id });

// -- import ------------------------------------------------------------------

export const importar = (arquivos: string[]) =>
  invoke<ResultadoImportacao[]>("importar", { arquivos });

// -- settings ----------------------------------------------------------------

export const diretorioDeDados = () => invoke<DiretorioDeDados>("diretorio_de_dados");
export const diretorioDeDadosValidar = (caminho: string) =>
  invoke<DiretorioDeDados>("diretorio_de_dados_validar", { caminho });
export const diretorioDeDadosAlterar = (caminho: string) =>
  invoke<string>("diretorio_de_dados_alterar", { caminho });
/** Effective configuration: each value with the source that imposed it. */
export interface Configuracao {
  arquivo: string;
  valores: Record<string, { valor: string; origem: string }>;
  /** The model a transcription would use now: the hardware's while nobody chose. */
  modelo_efetivo?: string;
}

export interface Dispositivo {
  id: string;
  nome: string;
  fluxo: "entrada" | "saida";
  /** Which Windows roles this endpoint is the default for. Meeting platforms
   *  follow "comunicacoes". */
  padrao_de: string[];
  estado: string;
  formato: string;
  parece_fone: boolean;
}

export interface ItemDoDoctor {
  chave: string;
  rotulo: string;
  estado: "ok" | "aviso" | "falha";
  detalhe: string;
  acao: string | null;
}

export interface Doctor {
  itens: ItemDoDoctor[];
  falhou: boolean;
  avisou: boolean;
  configuracao: Record<string, { valor: string; origem: string }>;
}

export const configuracaoLer = () => invoke<Configuracao>("configuracao_ler");
export const configuracaoGravar = (atribuicoes: string[]) =>
  invoke<string>("configuracao_gravar", { atribuicoes });
export const dispositivos = () =>
  invoke<{ dispositivos: Dispositivo[] }>("dispositivos");
export const diagnosticoDoNucleo = () => invoke<Doctor>("diagnostico_do_nucleo");
export const diagnosticoDoAplicativo = () =>
  invoke<ItemDeDiagnostico[]>("diagnostico_do_aplicativo");
export const mcpEstado = () => invoke<string>("mcp_estado");
export const mcpRegistrar = () => invoke<string>("mcp_registrar");

// -- the resident app --------------------------------------------------------

/** The route the window reopens on: `gravacao`, `biblioteca`,
 *  `biblioteca/<uid>` or `configuracoes`. Kept by the host process, because
 *  the window itself is destroyed whenever it goes to the tray. */
export const appRegistrarRota = (rota: string) => invoke<void>("app_registrar_rota", { rota });
export const appRotaInicial = () => invoke<string>("app_rota_inicial");
export interface MedidaDeAbertura {
  ms_desde_o_pedido: number | null;
  ms_na_interface: number;
}
export const appJanelaPronta = (msNaInterface: number) =>
  invoke<MedidaDeAbertura>("app_janela_pronta", { msNaInterface });
export const appUltimaAbertura = () => invoke<MedidaDeAbertura | null>("app_ultima_abertura");

export type CategoriaDeNotificacao =
  | "gravacao_iniciada"
  | "gravacao_encerrada"
  | "transcricao_concluida"
  | "transcricao_falha"
  | "avisos_de_captura"
  | "reuniao_detectada";

export type ChavesDeNotificacao = Record<CategoriaDeNotificacao, boolean>;

export interface Aplicativo {
  preferencias: {
    atalho: string;
    notificacoes: ChavesDeNotificacao;
    aviso_da_bandeja_mostrado: boolean;
  };
  preferencias_ilegiveis: string | null;
  /** From the registry; null when it could not be read. */
  inicio_com_o_windows: boolean | null;
  inicio_com_o_windows_erro: string | null;
  atalho: { em_vigor: string | null; conflito: string | null };
}

export const aplicativoLer = () => invoke<Aplicativo>("aplicativo_ler");
export const notificacoesDefinir = (chaves: ChavesDeNotificacao) =>
  invoke<Aplicativo>("notificacoes_definir", { chaves });
export const atalhoTrocar = (atalho: string) => invoke<Aplicativo>("atalho_trocar", { atalho });
export const inicioComOWindowsDefinir = (ligado: boolean) =>
  invoke<Aplicativo>("inicio_com_o_windows_definir", { ligado });
export const notificacaoDeTeste = (categoria: CategoriaDeNotificacao) =>
  invoke<void>("notificacao_de_teste", { categoria });
