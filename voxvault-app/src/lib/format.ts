// Every user-visible string in this application is pt-BR. Formatting lives in
// one module so a date never appears in two shapes in the same window.

const DATA_HORA = new Intl.DateTimeFormat("pt-BR", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

const DATA_CURTA = new Intl.DateTimeFormat("pt-BR", {
  day: "2-digit",
  month: "short",
});

/** `hh:mm:ss` for a position inside a meeting. Hours are dropped below one. */
export function carimbo(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const s = total % 60;
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  const dois = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${dois(m)}:${dois(s)}` : `${dois(m)}:${dois(s)}`;
}

/** A duration in words, for a list where precision to the second is noise. */
export function duracao(ms: number): string {
  if (ms <= 0) return "—";
  const minutos = Math.round(ms / 60000);
  if (minutos < 1) return "menos de 1 min";
  if (minutos < 60) return `${minutos} min`;
  const horas = Math.floor(minutos / 60);
  const resto = minutos % 60;
  return resto === 0 ? `${horas} h` : `${horas} h ${resto} min`;
}

export function dataHora(iso: string): string {
  if (!iso) return "—";
  const data = new Date(iso);
  return Number.isNaN(data.getTime()) ? "—" : DATA_HORA.format(data);
}

export function dataCurta(iso: string): string {
  if (!iso) return "—";
  const data = new Date(iso);
  return Number.isNaN(data.getTime()) ? "—" : DATA_CURTA.format(data);
}

export function bytes(valor: number | null | undefined): string {
  if (valor == null) return "—";
  if (valor < 1024) return `${valor} B`;
  const unidades = ["KB", "MB", "GB", "TB"];
  let n = valor / 1024;
  let i = 0;
  while (n >= 1024 && i < unidades.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(n >= 100 ? 0 : 1).replace(".", ",")} ${unidades[i]}`;
}

/**
 * How a track's speaker is named in the reading view.
 *
 * The attribution is by physical origin, not identity: the microphone track is
 * the user, the system track is whatever the output device was playing. An
 * imported file has no such separation, so it is unknown and must not be
 * presented as if it were the user.
 */
export function falante(valor: string, trilha: string): string {
  const bruto = (valor || "").toLowerCase();
  if (bruto.includes("voc") || bruto === "eu" || trilha === "mic") return "Você";
  if (trilha === "system" || bruto.includes("particip")) return "Participantes";
  return "Falante desconhecido";
}

export function papelDoFalante(valor: string, trilha: string): "eu" | "outros" | "desconhecido" {
  const bruto = (valor || "").toLowerCase();
  if (bruto.includes("voc") || bruto === "eu" || trilha === "mic") return "eu";
  if (trilha === "system" || bruto.includes("particip")) return "outros";
  return "desconhecido";
}

export const ROTULO_SITUACAO: Record<string, string> = {
  gravando: "Gravando",
  na_fila: "Aguardando transcrição",
  pronta: "Pronta",
  incompleta: "Finalização incompleta",
};

export const ROTULO_ORIGEM: Record<string, string> = {
  gravada: "Gravada",
  importada: "Importada",
};
