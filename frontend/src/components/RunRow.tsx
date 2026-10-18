import { Link } from "react-router";
import { modelsAnswered, runDurationMs, runPr } from "../lib/data";
import { backfillPath, ratePercent } from "../lib/backfill";
import { formatDuration, prUrl, relativeTime, shortRepo, toDate } from "../lib/format";
import { inferKind } from "../lib/nodes";
import { backfillStatus, runStatusOf } from "../lib/status";
import type { BackfillDoc, RunDoc } from "../lib/types";
import { ModelChips } from "./ModelChips";
import { KindChip, Pill, Skeleton } from "./ui";

/* One row per run. Every cell is one line in a fixed 20px track, so all rows are the same
   height; the long values (owner, full model ids) live in tooltips and the link.
   Phone: three lines (kind repo · duration / status · time / models · trace link).
   lg+: one line of seven aligned columns. */
const ROW =
  "grid auto-rows-[20px] grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2 border-b border-line px-4 py-3 last:border-b-0 " +
  "lg:grid-cols-[76px_minmax(0,1fr)_184px_216px_72px_104px_80px]";
// Phone placement for the cells after kind + repo; reset to DOM order at lg.
const AT = {
  status: "col-span-2 col-start-1 row-start-2 justify-self-start lg:col-span-1 lg:col-start-auto lg:row-start-auto",
  models: "col-span-2 col-start-1 row-start-3 lg:col-span-1 lg:col-start-auto lg:row-start-auto",
  duration: "col-start-3 row-start-1 text-right lg:col-start-auto lg:row-start-auto",
  time: "col-start-3 row-start-2 text-right lg:col-start-auto lg:row-start-auto",
  trace: "col-start-3 row-start-3 text-right lg:col-start-auto lg:row-start-auto",
};

function Models({ run }: { run: RunDoc }) {
  const models = modelsAnswered(run);
  if (!models.length) {
    return <span className="text-xs text-ink-3">{run.llm_calls === 0 ? "No model call" : "—"}</span>;
  }
  return <ModelChips models={models} max={2} />;
}

export function RunRow({ run, now }: { run: RunDoc; now: number }) {
  const pr = runPr(run);
  const started = toDate(run.started_at);
  const kind = inferKind(run);
  const full = pr != null ? `${run.repo}#${pr}` : run.repo;
  return (
    <li className={`row-in ${ROW}`}>
      <KindChip kind={kind} />
      <div className="min-w-0">
        {/* Short label on screen; owner/repo#n in the tooltip, the link and for screen readers. */}
        <a
          href={pr != null ? prUrl(run.repo, pr) : `https://github.com/${run.repo}`}
          target="_blank"
          rel="noreferrer"
          title={full}
          aria-label={`${full} (opens in a new tab)`}
          className="mono flex min-w-0 text-sm"
        >
          <span className="truncate">{shortRepo(run.repo)}</span>
          {pr != null && <span className="shrink-0">#{pr}</span>}
        </a>
      </div>
      <div className={AT.status}>
        <Pill info={runStatusOf(run)} size="xs" />
      </div>
      <div className={`min-w-0 ${AT.models}`}>
        <Models run={run} />
      </div>
      <div className={`mono whitespace-nowrap text-sm text-ink-2 ${AT.duration}`}>{formatDuration(runDurationMs(run))}</div>
      <div className={`whitespace-nowrap text-sm text-ink-3 ${AT.time}`}>
        <time dateTime={started?.toISOString()} title={started?.toLocaleString("en")}>
          {relativeTime(started, now)}
        </time>
      </div>
      <div className={AT.trace}>
        <Link to={`/runs/${run.id}`} className="whitespace-nowrap text-sm font-medium">
          View trace<span className="sr-only"> for {full}</span>
        </Link>
      </div>
    </li>
  );
}

/** All runs of one backfill as a single row (the sample's result lives on the Backfill page). */
export function BackfillRow({ repo, runs, b, now }: { repo: string; runs: RunDoc[]; b?: BackfillDoc; now: number }) {
  const latest = toDate(runs[0]?.started_at);
  const prs = b?.considered ?? runs.length;
  return (
    <li className={`row-in bg-surface-2/60 ${ROW}`}>
      <KindChip kind="backfill" />
      <div className="min-w-0">
        <a
          href={`https://github.com/${repo}`}
          target="_blank"
          rel="noreferrer"
          title={`${repo}: read-only backfill, nothing posted`}
          aria-label={`${repo} (opens in a new tab)`}
          className="mono flex min-w-0 text-sm"
        >
          <span className="truncate">{shortRepo(repo)}</span>
        </a>
      </div>
      <div className={AT.status}>
        {b ? <Pill info={backfillStatus(b.status)} size="xs" /> : <span className="text-xs text-ink-3">—</span>}
      </div>
      <div className={`min-w-0 whitespace-nowrap text-sm text-ink-2 ${AT.models}`}>
        {b ? (
          <>
            <span className="mono font-medium text-ink">{ratePercent(b)}</span> why-gap
            <span className="text-ink-3"> · {prs} PRs</span>
          </>
        ) : (
          `${runs.length} PRs`
        )}
      </div>
      <div className={`whitespace-nowrap text-sm text-ink-3 ${AT.duration}`}>
        <span className="mono text-ink-2">{runs.length}</span> runs
      </div>
      <div className={`whitespace-nowrap text-sm text-ink-3 ${AT.time}`}>
        <time dateTime={latest?.toISOString()} title={latest?.toLocaleString("en")}>
          {relativeTime(latest, now)}
        </time>
      </div>
      <div className={AT.trace}>
        <Link to={backfillPath(repo)} className="whitespace-nowrap text-sm font-medium">
          View sample<span className="sr-only"> for the {repo} backfill</span>
        </Link>
      </div>
    </li>
  );
}

export function RunRowSkeleton() {
  return (
    <li className={ROW}>
      <Skeleton className="h-[18px] w-[68px]" />
      <Skeleton className="h-4 w-36" />
      <Skeleton className={`h-[18px] w-24 ${AT.status}`} />
      <Skeleton className={`h-5 w-40 ${AT.models}`} />
      <Skeleton className={`ml-auto h-4 w-12 ${AT.duration}`} />
      <Skeleton className={`ml-auto h-4 w-20 ${AT.time}`} />
      <Skeleton className={`ml-auto h-4 w-16 ${AT.trace}`} />
    </li>
  );
}
