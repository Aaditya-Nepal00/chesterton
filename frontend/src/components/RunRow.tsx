import { Link } from "react-router";
import { modelsAnswered, runDurationMs, runPr } from "../lib/data";
import { formatDuration, prUrl, relativeTime, toDate } from "../lib/format";
import { inferKind } from "../lib/nodes";
import { runStatus } from "../lib/status";
import type { RunDoc } from "../lib/types";
import { KindChip, Pill, Skeleton } from "./ui";

export function RunRow({ run, now }: { run: RunDoc; now: number }) {
  const pr = runPr(run);
  const models = modelsAnswered(run);
  const started = toDate(run.started_at);
  const kind = inferKind(run);
  return (
    <li className="row-in grid grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-1 border-b border-line px-4 py-3 last:border-b-0 md:grid-cols-[76px_minmax(170px,1fr)_196px_minmax(180px,1.1fr)_72px_96px_88px]">
      <KindChip kind={kind} />
      <div className="min-w-0">
        {pr != null ? (
          // Only the repo name truncates; the PR number always stays visible.
          <a
            href={prUrl(run.repo, pr)}
            target="_blank"
            rel="noreferrer"
            title={`${run.repo}#${pr}`}
            className="mono flex min-w-0 text-sm"
          >
            <span className="truncate">{run.repo}</span>
            <span className="shrink-0">#{pr}</span>
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        ) : (
          <span className="mono block truncate text-sm">{run.repo}</span>
        )}
      </div>
      <div className="justify-self-end md:justify-self-start">
        <Pill info={runStatus(run.status)} size="xs" />
      </div>
      <div className="mono col-span-3 truncate text-xs text-ink-3 md:col-span-1" title="Model that answered">
        {models.length ? models.join(", ") : run.llm_calls === 0 ? "no LLM call" : "—"}
      </div>
      <div className="mono text-sm text-ink-2 md:text-right">{formatDuration(runDurationMs(run))}</div>
      <div className="text-sm text-ink-3 md:text-right">
        <time dateTime={started?.toISOString()}>{relativeTime(started, now)}</time>
      </div>
      <div className="text-right">
        <Link to={`/runs/${run.id}`} className="text-sm font-medium">
          View trace<span className="sr-only"> for {run.repo}#{pr}</span>
        </Link>
      </div>
    </li>
  );
}

export function RunRowSkeleton() {
  return (
    <li className="flex items-center gap-4 border-b border-line px-4 py-3.5 last:border-b-0">
      <Skeleton className="h-5 w-16" />
      <Skeleton className="h-4 w-48" />
      <Skeleton className="ml-auto h-5 w-24" />
    </li>
  );
}
