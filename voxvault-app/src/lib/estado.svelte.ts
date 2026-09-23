// Shared reactive state, on Svelte 5 runes.
//
// Everything presented here comes from the core or from the host process. The
// one thing this module owns is the *rate* at which it is allowed to reach the
// screen, and that is deliberate: the level meters are capped at 20 updates per
// second per track, and the elapsed clock stops ticking when the window is
// hidden. Both are budget decisions, not cosmetics -- the webview is where a
// badly built interface spends its processor time during a meeting.

import type { Ambiente, Resumo, ServicoSnapshot } from "./api";

export type Tela = "gravacao" | "biblioteca" | "configuracoes";
export type EstadoDaGravacao = "ocioso" | "gravando" | "pausado";

/** The spec's ceiling, enforced here rather than trusted upstream. */
const INTERVALO_MINIMO_DE_NIVEL_MS = 1000 / 20;

export const navegacao = $state<{ tela: Tela; reuniaoAberta: string | null; focoMs: number | null }>({
  tela: "gravacao",
  reuniaoAberta: null,
  /** Position the reading view should scroll to, set when a search result or a
   *  warning points at a specific moment. */
  focoMs: null,
});

/** The route as one string, the form the host keeps between windows. */
export function rotaAtual(): string {
  if (navegacao.tela === "biblioteca" && navegacao.reuniaoAberta) {
    return `biblioteca/${navegacao.reuniaoAberta}`;
  }
  return navegacao.tela;
}

/** Go to a route reported by the host: a restored window, a tray action or a
 *  notification that was clicked. Anything unrecognized lands on Gravação. */
export function irParaRota(rota: string) {
  const [tela, uid] = rota.split("/", 2);
  if (tela === "biblioteca") {
    navegacao.tela = "biblioteca";
    navegacao.reuniaoAberta = uid || null;
    navegacao.focoMs = null;
  } else if (tela === "configuracoes") {
    navegacao.tela = "configuracoes";
  } else {
    navegacao.tela = "gravacao";
  }
}

export const shell = $state<{
  ambiente: Ambiente | null;
  servico: ServicoSnapshot | null;
  carregado: boolean;
}>({
  ambiente: null,
  servico: null,
  carregado: false,
});

export interface Trilha {
  capturando: boolean;
  nivel: number;
  /** Set when the track stopped for a reason, so the interface can say which
   *  one stopped and that the other continues. */
  motivo: string | null;
  /** How long this track has been silent. It is what separates "nobody is
   *  talking right now" from "this track has been recording silence for twenty
   *  minutes and nobody noticed". */
  silencioHaS: number;
}

export const gravacao = $state<{
  estado: EstadoDaGravacao;
  titulo: string;
  uid: string | null;
  /** Milliseconds already recorded, excluding paused stretches. */
  decorridoMs: number;
  trilhas: { mic: Trilha; system: Trilha };
  avisos: { texto: string; em: number; instanteMs: number | null }[];
  /** Whether level samples are actually arriving. False means nobody is
   *  publishing them, which the meter must say rather than show a flat bar. */
  niveisVivos: boolean;
}>({
  estado: "ocioso",
  titulo: "",
  uid: null,
  decorridoMs: 0,
  niveisVivos: false,
  trilhas: {
    mic: { capturando: false, nivel: 0, motivo: null, silencioHaS: 0 },
    system: { capturando: false, nivel: 0, motivo: null, silencioHaS: 0 },
  },
  avisos: [],
});

export const biblioteca = $state<{
  reunioes: Resumo[];
  carregando: boolean;
  filtroTermo: string;
  filtroSituacao: string;
  filtroDe: string;
  filtroAte: string;
}>({
  reunioes: [],
  carregando: false,
  filtroTermo: "",
  filtroSituacao: "todas",
  filtroDe: "",
  filtroAte: "",
});

/** Transient messages. Warnings during a recording never block it, so they
 *  land here and in `gravacao.avisos`, never in a modal. */
export const recados = $state<{ itens: { id: number; tom: "info" | "erro" | "ok"; texto: string }[] }>({
  itens: [],
});

let proximoRecado = 1;

export function recado(tom: "info" | "erro" | "ok", texto: string) {
  const id = proximoRecado++;
  recados.itens.push({ id, tom, texto });
  // Long enough to read a sentence, short enough not to accumulate.
  setTimeout(() => {
    const posicao = recados.itens.findIndex((item) => item.id === id);
    if (posicao >= 0) recados.itens.splice(posicao, 1);
  }, tom === "erro" ? 9000 : 4500);
}

export function dispensarRecado(id: number) {
  const posicao = recados.itens.findIndex((item) => item.id === id);
  if (posicao >= 0) recados.itens.splice(posicao, 1);
}

// -- level meters ------------------------------------------------------------

const ultimoNivelEm: Record<string, number> = { mic: 0, system: 0 };
let expiracaoDeNivel: number | null = null;

/** Levels stop being shown when they stop arriving, rather than freezing at
 *  the last value -- a frozen bar is a lie about what the microphone is doing.
 *
 *  Three polling periods: one missed sample is jitter, three in a row means
 *  nobody is publishing any more. */
const VALIDADE_DO_NIVEL_MS = 6500;

/**
 * Accept a level sample for one track, dropping anything above 20 Hz.
 *
 * The cap is applied on arrival rather than on render: a dropped sample costs
 * nothing, while a sample that reaches the DOM costs a frame whether or not the
 * eye could have told the difference.
 */
export function registrarNivel(trilha: "mic" | "system", valor: number) {
  const agora = performance.now();
  if (agora - ultimoNivelEm[trilha] < INTERVALO_MINIMO_DE_NIVEL_MS) return;
  ultimoNivelEm[trilha] = agora;
  gravacao.trilhas[trilha].nivel = Math.max(0, Math.min(1, valor));

  gravacao.niveisVivos = true;
  if (expiracaoDeNivel !== null) window.clearTimeout(expiracaoDeNivel);
  expiracaoDeNivel = window.setTimeout(() => {
    gravacao.niveisVivos = false;
    gravacao.trilhas.mic.nivel = 0;
    gravacao.trilhas.system.nivel = 0;
  }, VALIDADE_DO_NIVEL_MS);
}

export function registrarAviso(texto: string, instanteMs: number | null = null) {
  gravacao.avisos.push({ texto, em: Date.now(), instanteMs });
}

export function zerarGravacao() {
  gravacao.estado = "ocioso";
  gravacao.uid = null;
  gravacao.decorridoMs = 0;
  gravacao.avisos = [];
  for (const trilha of ["mic", "system"] as const) {
    gravacao.trilhas[trilha].capturando = false;
    gravacao.trilhas[trilha].nivel = 0;
    gravacao.trilhas[trilha].motivo = null;
    gravacao.trilhas[trilha].silencioHaS = 0;
  }
}

// -- elapsed clock -----------------------------------------------------------
//
// One interval for the whole application, running only while recording and only
// while the window is visible. A minimised window that keeps re-rendering a
// clock nobody is looking at is exactly the cost this product exists to avoid.

let relogio: number | null = null;
let ancora = 0;
let baseMs = 0;

function tick() {
  gravacao.decorridoMs = baseMs + (performance.now() - ancora);
}

export function iniciarRelogio(decorridoInicialMs = 0) {
  baseMs = decorridoInicialMs;
  ancora = performance.now();
  gravacao.decorridoMs = baseMs;
  if (relogio !== null) return;
  relogio = window.setInterval(() => {
    if (!document.hidden) tick();
  }, 1000);
}

export function pausarRelogio() {
  if (relogio === null) return;
  tick();
  baseMs = gravacao.decorridoMs;
  window.clearInterval(relogio);
  relogio = null;
}

export function pararRelogio() {
  pausarRelogio();
  baseMs = 0;
}

// Coming back from minimised must not show a clock that stopped while hidden:
// the recording never paused, only the rendering did.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && relogio !== null) tick();
});
