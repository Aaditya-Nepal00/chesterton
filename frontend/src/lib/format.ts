import type { TimeLike } from "./types";

export function toDate(t: TimeLike): Date | null {
  if (!t) return null;
  if (typeof t === "string") {
    const d = new Date(t);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  if (typeof (t as { toDate?: () => Date }).toDate === "function") return t.toDate();
  return null;
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
  const m = Math.floor(s / 60);
  return `${m} m ${Math.round(s % 60)} s`;
}

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

export function relativeTime(d: Date | null, now = Date.now()): string {
  if (!d) return "—";
  const diff = (d.getTime() - now) / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return "just now";
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), "day");
  return d.toLocaleDateString("en", { year: "numeric", month: "short", day: "numeric" });
}

export function formatDateTime(d: Date | null): string {
  if (!d) return "—";
  return d.toLocaleString("en", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function percent(n: number, d: number): string {
  if (!d) return "—";
  return `${Math.round((100 * n) / d)}%`;
}

/** Doc ids are "<owner>__<repo>__<n>". */
export function prDocId(repo: string, n: number | string): string {
  const [owner, name] = repo.split("/");
  return `${owner}__${name}__${n}`;
}

export function prUrl(repo: string, n: number | string): string {
  return `https://github.com/${repo}/pull/${n}`;
}

/** "Aaditya-Nepal00/chesterton-demo" → "chesterton-demo" (the full name goes in a tooltip). */
export function shortRepo(repo: string): string {
  return repo.slice(repo.indexOf("/") + 1);
}

const cap = (w: string) => (w ? w[0].toUpperCase() + w.slice(1) : w);

/** "gemini-3.5-flash-lite" → "3.5 Flash-Lite"; anything else is returned unchanged. */
export function shortModel(model: string): string {
  const m = /^(?:models\/)?gemini-(\d+(?:\.\d+)?)-(.+)$/i.exec(model);
  if (!m) return model;
  const name = m[2].replace(/-preview.*$/i, "").split("-").map(cap).join("-");
  return `${m[1]} ${name}${/-preview/i.test(m[2]) ? " Preview" : ""}`;
}
