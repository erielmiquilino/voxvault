// What a deletion confirmation says, built from the core's own preview.
//
// Every number here -- duration, notes, space -- comes from `voxvault delete`
// without `--yes`, never from a sum over the list: the space the person agrees
// to free is the space the core is about to free.

import type { Exclusao, ItemDaExclusao, Resumo } from "./api";
import { bytes, dataHora, duracao } from "./format";

function plural(n: number, um: string, varios: string): string {
  return `${n} ${n === 1 ? um : varios}`;
}

/** Why a meeting cannot be deleted in its current state, or null if it can. */
export function motivoParaNaoExcluir(resumo: Resumo): string | null {
  if (resumo.situacao === "gravando") {
    return "A reunião está sendo gravada. Encerre a gravação para poder excluí-la.";
  }
  if (resumo.situacao === "transcrevendo") {
    return "A transcrição desta reunião está em andamento. Aguarde terminar para excluí-la.";
  }
  return null;
}

/** A refusal in the interface's own words, chosen by the core's cause code. */
export function motivoLegivel(item: ItemDaExclusao): string {
  switch (item.causa) {
    case "gravando":
      return "está sendo gravada";
    case "transcrevendo":
      return "a transcrição dela está em andamento";
    case "em_uso":
      return "um arquivo dela está aberto em outro programa; feche-o e tente de novo";
    case "inexistente":
      return "ela não existe mais";
    case "identificador":
      return "o identificador não corresponde a uma única reunião";
    default:
      return item.motivo;
  }
}

function emLista(partes: string[]): string {
  if (partes.length <= 1) return partes.join("");
  return `${partes.slice(0, -1).join(", ")} e ${partes[partes.length - 1]}`;
}

function oQueSeraApagado(previa: Exclusao): string {
  const { total } = previa;
  const arquivos = previa.itens
    .filter((item) => !item.motivo)
    .reduce((soma, item) => soma + item.arquivos.length, 0);
  const partes = ["o áudio"];
  if (total.revisoes > 0) {
    partes.push(`as transcrições (${plural(total.revisoes, "revisão", "revisões")})`);
  }
  if (total.notas > 0) {
    partes.push(total.notas === 1 ? "a nota" : `as ${total.notas} notas`);
  }
  partes.push("as exportações");
  return (
    `Serão apagados ${emLista(partes)}: ${plural(arquivos, "arquivo", "arquivos")}, ` +
    `${bytes(total.bytes)} liberados.`
  );
}

const DEFINITIVA = "A exclusão é definitiva e não pode ser desfeita.";

export function corpoDeUma(previa: Exclusao): string {
  const item = previa.itens[0];
  return [
    `«${item.titulo}»`,
    `${dataHora(item.inicio ?? "")} · ${duracao(item.duracao_ms)} · ` +
      plural(item.notas, "nota", "notas"),
    "",
    oQueSeraApagado(previa),
    "",
    DEFINITIVA,
  ].join("\n");
}

const LISTADAS = 8;

export function corpoDeVarias(previa: Exclusao): string {
  const { total } = previa;
  const excluiveis = previa.itens.filter((item) => !item.motivo);
  const recusadas = previa.itens.filter((item) => item.motivo);
  const linhas = [
    `${plural(total.reunioes, "reunião", "reuniões")} · ${duracao(total.duracao_ms)} · ` +
      `${plural(total.notas, "nota", "notas")} · ${bytes(total.bytes)}`,
    "",
    ...excluiveis
      .slice(0, LISTADAS)
      .map((item) => `• ${item.titulo} — ${dataHora(item.inicio ?? "")}`),
  ];
  if (excluiveis.length > LISTADAS) {
    linhas.push(`• e mais ${excluiveis.length - LISTADAS}`);
  }
  linhas.push("", oQueSeraApagado(previa), "", DEFINITIVA);
  if (recusadas.length > 0) {
    linhas.push(
      "",
      "Não serão excluídas agora:",
      ...recusadas.map((item) => `• ${item.titulo || item.uid} — ${motivoLegivel(item)}`),
    );
  }
  return linhas.join("\n");
}
