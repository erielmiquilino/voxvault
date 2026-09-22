// Typed bridge to the host process.
//
// Every failure crossing this boundary is a `Falha`: a cause in pt-BR, an
// optional corrective action, and -- when the obstacle is that the core has no
// machine-readable output for something yet -- the exact command and flag that
// would remove it. The interface uses `pendencia` to disable an action and
// explain it up front rather than letting it fail at the click.

import { invoke } from "@tauri-apps/api/core";

export interface Pendencia {
  comando: string;
  sinalizador: string;
}

export interface Falha {
  mensagem: string;
  acao: string | null;
  pendencia: Pendencia | null;
}

export function ehFalha(valor: unknown): valor is Falha {
  return typeof valor === "object" && valor !== null && "mensagem" in valor;
}

/** Normalises anything thrown across the bridge into a `Falha`. */
export function comoFalha(erro: unknown): Falha {
  if (ehFalha(erro)) return erro;
  return { mensagem: String(erro), acao: null, pendencia: null };
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

export interface ServicoSnapshot {
  estado: EstadoDoServico;
  endereco: string | null;
  data_dir_do_servico: string | null;
  saude: Saude | null;
  detalhe: string;
  acao: string | null;
  tentativas_recentes: number;
  pode_tentar_de_novo: boolean;
  caminho_do_log: string | null;
}

export interface Ambiente {
  preparado: boolean;
  raiz_do_nucleo: string | null;
  executavel: string | null;
  detalhe: string;
  acao: string | null;
}

export type Situacao = "gravando" | "na_fila" | "pronta" | "incompleta";

export interface Resumo {
  uid: string;
  titulo: string;
  inicio: string;
  fim: string;
  duracao_ms: number;
  situacao: Situacao;
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

export interface Detalhe {
  resumo: Resumo;
  segmentos: Segmento[];
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
  fonte: "environment" | "config_file" | "built_in_default";
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

export interface EstadoDeFechamento {
  gravando: boolean;
  fila_pendente: number;
  duracao_ms: number;
}

// -- environment and service ------------------------------------------------

export const ambienteEstado = () => invoke<Ambiente>("ambiente_estado");
export const ambientePreparar = () => invoke<string>("ambiente_preparar");
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

export const reunioesListar = () => invoke<Resumo[]>("reunioes_listar");
export const reuniaoDetalhar = (uid: string) => invoke<Detalhe>("reuniao_detalhar", { uid });
export const reuniaoDoNucleo = (uid: string) => invoke<unknown>("reuniao_do_nucleo", { uid });
export const reuniaoReprocessar = (uid: string) => invoke<string>("reuniao_reprocessar", { uid });
export const reuniaoExportar = (uid: string) => invoke<string[]>("reuniao_exportar", { uid });
export const reuniaoRenomear = (uid: string, titulo: string) =>
  invoke<void>("reuniao_renomear", { uid, titulo });
export const reuniaoRemoverAudio = (uid: string) => invoke<void>("reuniao_remover_audio", { uid });
export const reuniaoAbrirPasta = (uid: string) => invoke<void>("reuniao_abrir_pasta", { uid });
export const abrirCaminho = (caminho: string) => invoke<void>("abrir_caminho", { caminho });
export const buscar = (termo: string, limite = 50) => invoke<unknown>("busca", { termo, limite });
export const notasListar = (uid: string) => invoke<unknown>("notas_listar", { uid });

// -- import ------------------------------------------------------------------

export const importar = (arquivos: string[]) =>
  invoke<ResultadoImportacao[]>("importar", { arquivos });

// -- settings ----------------------------------------------------------------

export const diretorioDeDados = () => invoke<DiretorioDeDados>("diretorio_de_dados");
export const diretorioDeDadosValidar = (caminho: string) =>
  invoke<DiretorioDeDados>("diretorio_de_dados_validar", { caminho });
export const diretorioDeDadosAlterar = (caminho: string) =>
  invoke<string>("diretorio_de_dados_alterar", { caminho });
export const configuracaoLer = () => invoke<unknown>("configuracao_ler");
export const configuracaoGravar = (atribuicoes: string[]) =>
  invoke<string>("configuracao_gravar", { atribuicoes });
export const dispositivos = () => invoke<unknown>("dispositivos");
export const diagnosticoDoNucleo = () => invoke<unknown>("diagnostico_do_nucleo");
export const diagnosticoDoAplicativo = () =>
  invoke<ItemDeDiagnostico[]>("diagnostico_do_aplicativo");
export const trechoMcp = () => invoke<string>("trecho_mcp");

// -- closing -----------------------------------------------------------------

export const estadoDeFechamento = () => invoke<EstadoDeFechamento>("estado_de_fechamento");
