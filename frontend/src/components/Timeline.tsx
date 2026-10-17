import { useEffect, useMemo, useRef, useState } from "react";
import { failedCallsForPath, modelForPath } from "../lib/data";
import { formatDuration } from "../lib/format";
import { GEMINI_NODES, nodeName, pathDepth, VERIFIER_NODES, workerIndex } from "../lib/nodes";
import type { RunDoc, TraceEventDoc } from "../lib/types";
import { cx, EngineMarker, StatusIcon } from "./ui";

export interface TimelineRow {
  path: string;
  node: string;
  depth: number;
  firstSeq: number;
  start: number | null;
  end: number | null;
  status: "running" | "finished" | "error";
  summary: string;
  retries: TraceEventDoc[];
  model: string | null;
  isWorkerGroup: boolean;
  workers: number;
}

function ms(ts: string | undefined): number | null {
  if (!ts) return null;
  const t = Date.parse(ts);
  return Number.isNaN(t) ? null : t;
}

/** Fold the event stream (started / error* / finished per path) into one row per node. */
export function buildRows(events: TraceEventDoc[], run: RunDoc | null): TimelineRow[] {
  const rows = new Map<string, TimelineRow>();
  for (const e of [...events].sort((a, b) => a.seq - b.seq)) {
    let r = rows.get(e.path);
    if (!r) {
      r = {
        path: e.path,
        node: e.node,
        depth: pathDepth(e.path),
        firstSeq: e.seq,
        start: null,
        end: null,
        status: "running",
        summary: "",
        retries: [],
        model: null,
        isWorkerGroup: false,
        workers: 0,
      };
      rows.set(e.path, r);
    }
    const t = ms(e.ts);
    if (e.status === "started") {
      r.start = t;
    } else if (e.status === "error") {
      r.retries.push(e);
      r.status = "error";
      r.end = t;
      if (e.model) r.model = e.model;
    } else {
      r.status = "finished";
      r.end = t;
      r.summary = e.summary;
      if (r.start == null && t != null && e.duration_ms != null) r.start = t - e.duration_ms;
      if (e.model) r.model = e.model;
    }
  }
  const list = [...rows.values()];
  // Parallel-worker containers (e.g. rationale@1 with rationale@1/rationale@N children)
  for (const r of list) {
    const kids = list.filter((k) => k.path.startsWith(r.path + "/"));
    if (kids.length) {
      r.isWorkerGroup = true;
      r.workers = kids.length;
      const starts = kids.map((k) => k.start).filter((x): x is number => x != null);
      if (starts.length) r.start = Math.min(...starts, r.start ?? Infinity);
    }
    if (r.start == null) r.start = r.end; // instant steps (e.g. the join)
    if (!r.model) r.model = modelForPath(run, r.path);
  }
  return list.sort(
    (a, b) => (a.start ?? Infinity) - (b.start ?? Infinity) || a.depth - b.depth || a.firstSeq - b.firstSeq,
  );
}

function useTicker(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

export function Timeline({ events, run, live }: { events: TraceEventDoc[]; run: RunDoc | null; live: boolean }) {
  const rows = useMemo(() => buildRows(events, run), [events, run]);
  const running = live && rows.some((r) => r.status === "running");
  const now = useTicker(running);
  const seen = useRef<Set<string> | null>(null);
  if (seen.current === null) seen.current = new Set(rows.map((r) => r.path)); // no animation on first paint

  const t0 = Math.min(...rows.map((r) => r.start ?? Infinity));
  const t1 = Math.max(...rows.map((r) => (r.status === "running" ? now : r.end ?? r.start ?? 0)));
  const span = Math.max(1, t1 - t0);

  return (
    <div className="overflow-x-auto rounded-[var(--radius)] border border-line bg-surface" role="region" aria-label="Agent timeline" tabIndex={0}>
      <div className="md:min-w-[760px]">
        <div className="flex justify-between border-b border-line px-4 py-2 text-xs text-ink-3 md:hidden">
          <span>Time scale (shared)</span>
          <span className="mono">0 s → {formatDuration(span)}</span>
        </div>
        <div className="hidden grid-cols-[minmax(220px,1.1fr)_178px_minmax(240px,2fr)_72px] gap-x-4 border-b border-line px-4 py-2 text-xs font-medium uppercase tracking-[0.08em] text-ink-3 md:grid">
          <span>Step</span>
          <span>Engine</span>
          <span className="flex justify-between">
            <span>0 s</span>
            <span className="mono normal-case tracking-normal">{formatDuration(span)}</span>
          </span>
          <span className="text-right">Time</span>
        </div>
        <ol>
          {rows.map((r) => {
            const isNew = !seen.current!.has(r.path);
            if (isNew) seen.current!.add(r.path);
            const end = r.status === "running" ? now : (r.end ?? r.start ?? t0);
            const start = r.start ?? end;
            const left = ((start - t0) / span) * 100;
            const width = Math.max(0.6, ((end - start) / span) * 100);
            const gemini = GEMINI_NODES.has(r.node) && !r.isWorkerGroup;
            const verifier = VERIFIER_NODES.has(r.node);
            const failed = r.status === "error";
            const tone = failed ? "fail" : r.status === "running" ? "accent" : "ok";
            const failedCalls = failedCallsForPath(run, r.path);
            const worker = workerIndex(r.path);
            return (
              <li
                key={r.path}
                className={cx(
                  // phones: name + time, then engine, then a full-width bar; md+: four columns
                  "grid grid-cols-[1fr_auto] items-start gap-x-3 gap-y-1.5 border-b border-line px-4 py-2.5 last:border-b-0 md:grid-cols-[minmax(220px,1.1fr)_178px_minmax(240px,2fr)_72px] md:gap-x-4 md:gap-y-0",
                  verifier &&"bg-[color-mix(in_oklab,var(--accent-soft)_55%,transparent)]",
                  isNew && "row-in",
                )}
              >
                <div className={cx("order-1 min-w-0", r.depth > 1 && "pl-5")}>
                  <div className="flex items-center gap-2">
                    <StatusIcon tone={tone} className={tone === "ok" ? "text-[var(--ok)]" : tone === "fail" ? "text-[var(--fail)]" : "text-accent"} />
                    <span className="truncate font-medium text-ink">
                      {r.depth > 1 && <span className="mr-1 text-ink-3" aria-hidden="true">└</span>}
                      {nodeName(r.node)}
                      {worker && <span className="mono ml-1 text-xs text-ink-3">#{worker}</span>}
                    </span>
                    {verifier && (
                      <span className="rounded border border-accent/40 px-1.5 py-px text-[11px] font-medium uppercase tracking-wide text-accent">
                        self-check
                      </span>
                    )}
                    <span className="sr-only">
                      {failed ? "failed" : r.status === "running" ? "running" : "finished"}
                    </span>
                  </div>
                  {r.summary && <p className="mt-0.5 line-clamp-2 text-sm text-ink-2">{r.summary}</p>}
                  {(r.retries.length > 0 || failedCalls.length > 0) && (
                    <p className="mt-0.5 text-xs text-[var(--fail)]">
                      {r.status === "finished" ? "Recovered after " : ""}
                      {Math.max(r.retries.length, failedCalls.length)} failed attempt
                      {Math.max(r.retries.length, failedCalls.length) > 1 ? "s" : ""}
                      {": "}
                      <span className="mono">
                        {(r.retries[0]?.summary || failedCalls[0]?.error || "").split(".")[0].slice(0, 80)}
                      </span>
                    </p>
                  )}
                </div>
                <div className={cx("order-3 col-span-2 pt-0.5 md:order-2 md:col-span-1", r.depth > 1 && "pl-5 md:pl-0")}>
                  {r.isWorkerGroup ? (
                    <span className="mono text-xs text-ink-3">parallel ×{r.workers}</span>
                  ) : (
                    <EngineMarker gemini={gemini} model={r.model} />
                  )}
                </div>
                <div className="relative order-4 col-span-2 mt-0.5 h-3 rounded-sm bg-surface-2 md:order-3 md:col-span-1 md:mt-1.5" aria-hidden="true">
                  <div
                    className={cx(
                      "absolute top-0 h-3 rounded-sm",
                      failed ? "bar-fail" : r.status === "running" ? "bar-running pulse" : gemini ? "bar-accent" : r.isWorkerGroup ? "bar-code opacity-50" : "bar-code",
                    )}
                    style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }}
                  />
                </div>
                <div className="mono order-2 pt-0.5 text-right text-sm text-ink-2 md:order-4">
                  {r.status === "running" ? "…" : formatDuration(end - start)}
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}
