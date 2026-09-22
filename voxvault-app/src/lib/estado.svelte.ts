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
}

export const gravacao = $state<{
  estado: EstadoDaGravacao;
  titulo: string;
  uid: string | null;
  /** Milliseconds already recorded, excluding paused stretches. */
  decorridoMs: number;
  trilhas: { mic: Trilha; system: Trilha };
  avisos: { texto: string; em: number; instanteMs: number | null }[];
}>({
  estado: "ocioso",
  titulo: "",
  uid: null,
  decorridoMs: 0,
  trilhas: {
    mic: { capturando: false, nivel: 0, motivo: null },
    system: { capturando: false, nivel: 0, motivo: null },
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
