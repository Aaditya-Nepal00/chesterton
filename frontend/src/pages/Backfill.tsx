import { useMemo, type ReactNode } from "react";
import { Link, Navigate, useParams } from "react-router";
import { DecisionBar, MethodPopover } from "../components/Backfill";
import {
  Card,
  Code,
  CopyButton,
  EmptyState,
  ErrorNote,
  Ext,
  InlineMd,
  PageTitle,
  Pill,
  SectionHeading,
  Skeleton,
  Stat,
  StatusIcon,
} from "../components/ui";
import { asBackfill, asBackfillItem, backfillItemsQuery, backfillRef, backfillsQuery, repoDocId } from "../lib/data";
import { backfillPath, gapCounts, headline, ratePercent } from "../lib/backfill";
import { prUrl, toDate } from "../lib/format";
import { useLiveDoc, useLiveQuery } from "../lib/hooks";
import { backfillItemStatus, backfillStatus, rationaleStatus, transientModelError, type Tone } from "../lib/status";
import type { BackfillDoc, BackfillItemDoc, RationaleStatus } from "../lib/types";

const ORDER: RationaleStatus[] = ["missing", "partial", "found", "self_evident"];

function DecisionSummary({ item }: { item: BackfillItemDoc }) {
  if (item.status === "error") {
    const cause = transientModelError(item.error);
    return <span className="text-ink-3">{cause ? `${cause}; retried on --resume` : "Analysis failed; retried on --resume"}</span>;
  }
  if (item.status !== "analyzed") {
    return <span className="text-ink-3">{item.skip_reason ? item.skip_reason.replace(/^screen: /, "") : "—"}</span>;
  }
  const c = item.counts;
  if (!c || !item.decisions.length) return <span className="text-ink-3">no decisions extracted</span>;
  return (
    <span className="flex flex-wrap gap-1.5">
      {ORDER.filter((s) => c[s] > 0).map((s) => (
        <Pill key={s} size="xs" info={{ ...rationaleStatus(s), label: `${c[s]} ${rationaleStatus(s).label.toLowerCase()}` }} />
      ))}
    </span>
  );
}

const NOT_REACHED: Record<BackfillDoc["status"], string> = {
  running: "The backfill is still working through them",
  complete: "",
  stopped_budget: "The run stopped at its call budget",
  paused_quota: "Gemini's daily free-tier quota ran out",
  incomplete: "The run ended before reaching them",
};

/** What the rate covers: each PR of the sample is accounted for once. */
function Coverage({ b, rows }: { b: BackfillDoc; rows: BackfillItemDoc[] }) {
  const failed = rows.filter((it) => it.status === "error");
  const waiting = rows.filter((it) => it.status === "pending");
  const notCounted = b.sample_size - b.considered;
  const budget = b.status === "stopped_budget" && b.budget_calls ? ` of ${b.budget_calls} Gemini calls` : "";
  const lines: { tone: Tone; head: ReactNode; body: ReactNode }[] = [
    {
      tone: "ok",
      head: `${b.considered} of ${b.sample_size} PRs counted`,
      body: (
        <>
          Newest first: {b.skipped_deterministic} skipped by rule, {b.screened} screened, {b.analyzed} analyzed for
          decisions. The {ratePercent(b)} comes from those {b.analyzed} analyzed PRs.
        </>
      ),
    },
    ...failed.map((it) => {
      const cause = transientModelError(it.error);
      return {
        tone: "slate" as Tone,
        head: (
          <>
            <Ext href={it.url ?? prUrl(b.repo, it.pr)} className="mono">#{it.pr}</Ext> not analyzed
          </>
        ),
        body: (
          <>
            {cause ? `${cause} on this PR` : "Analysis of this PR failed"}, so its decisions are not in the rate.{" "}
            A <Code>--resume</Code> run retries it.
          </>
        ),
      };
    }),
  ];
  if (notCounted > 0) {
    const reason = NOT_REACHED[b.status];
    lines.push({
      tone: "muted",
      head: `${notCounted} older PR${notCounted > 1 ? "s" : ""} not counted yet`,
      body: (
        <>
          {reason ? `${reason}${budget}` : "They have no result yet"}
          {waiting.length ? ` (${waiting.length} screened and waiting)` : ""}. A <Code>--resume</Code> run continues
          with {notCounted > 1 ? "them" : "it"}; until then the numbers describe the newest {b.considered} only.
        </>
      ),
    });
  }
  return (
    <Card>
      <ul className="divide-y divide-line">
        {lines.map((l, i) => (
          <li key={i} className="grid grid-cols-[auto_1fr] gap-x-3 px-4 py-3 text-sm sm:px-5">
            <span className="pt-[3px]" style={{ color: `var(--${l.tone})` }}>
              <StatusIcon tone={l.tone} />
            </span>
            <span>
              <span className="font-medium text-ink">{l.head}.</span> <span className="text-ink-2">{l.body}</span>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function NoBackfill({ repo }: { repo?: string }) {
  const cmd = `uv run python scripts/backfill.py ${repo ?? "owner/repo"} --limit 22 --budget-calls 40`;
  return (
    <>
      <PageTitle
        eyebrow={
          repo ? (
            <span>Backfill · <Ext href={`https://github.com/${repo}`} className="mono">{repo}</Ext></span>
          ) : (
            "Backfill"
          )
        }
      >
        {repo ? "No backfill for this repo yet" : "No backfill yet"}
      </PageTitle>
      <EmptyState title="Nothing measured here yet." action={<CopyButton text={cmd} label="Copy command" />}>
        A backfill reads the last merged PRs of a public repo (read-only), skips bot, release, docs-only and
        tests-only PRs by rule, and checks each consequential decision for a written reason. It never posts
        anything. Run it from <span className="mono">backend/</span>:
        <pre className="mono mt-2 whitespace-pre-wrap break-words rounded bg-surface-2 px-3 py-2 text-xs text-ink">{cmd}</pre>
        Add <Code>--dry-run</Code> first to see how many Gemini calls it needs.
      </EmptyState>
    </>
  );
}

function BackfillSkeleton() {
  return (
    <div className="grid gap-4">
      <Skeleton className="h-9 w-1/2" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

/** /backfill (the nav entry): opens the most recently updated backfill. */
export function BackfillLatest() {
  const latest = useLiveQuery(backfillsQuery, asBackfill, []);
  if (latest.loading) return <BackfillSkeleton />;
  if (latest.error) return <ErrorNote error={latest.error} />;
  const b = latest.data[0];
  return b ? <Navigate to={backfillPath(b.repo)} replace /> : <NoBackfill />;
}

export function Backfill() {
  const { owner = "", repo: name = "" } = useParams();
  const repo = `${owner}/${name}`;
  const id = repoDocId(repo);
  const bf = useLiveDoc(() => backfillRef(id), asBackfill, [id]);
  const items = useLiveQuery(() => backfillItemsQuery(id), asBackfillItem, [id]);
  const b = bf.data;

  // Only this sample's items (a --restart starts a new epoch), newest merge first.
  const rows = useMemo(() => {
    if (!b) return [];
    const mine = items.data.filter((it) => !b.epoch || !it.epoch || it.epoch === b.epoch);
    const order = b.sample_prs ?? [];
    const rank = new Map(order.map((n, i) => [n, i]));
    return mine
      .filter((it) => !order.length || rank.has(it.pr))
      .sort((x, y) => (rank.get(x.pr) ?? 0) - (rank.get(y.pr) ?? 0));
  }, [b, items.data]);

  if (bf.loading) return <BackfillSkeleton />;
  if (bf.error) return <ErrorNote error={bf.error} />;
  if (!b) return <NoBackfill repo={repo} />;

  return <BackfillReport repo={repo} b={b} rows={rows} itemsLoading={items.loading} itemsError={items.error} />;
}

/** The populated page, from data (no Firestore access here). */
export function BackfillReport({
  repo,
  b,
  rows,
  itemsLoading,
  itemsError,
}: {
  repo: string;
  b: BackfillDoc;
  rows: BackfillItemDoc[];
  itemsLoading: boolean;
  itemsError: string | null;
}) {
  const { gaps, checked } = gapCounts(b.decisions);
  const sentence = headline(b);
  const finished = toDate(b.finished_at ?? null);

  return (
    <div className="grid gap-10">
      <div>
        <PageTitle
          eyebrow={
            <span className="flex flex-wrap items-center gap-x-3">
              <span>Backfill</span>
              <Ext href={`https://github.com/${repo}`} className="mono">{repo}</Ext>
              {finished && <span>measured {finished.toLocaleDateString("en", { year: "numeric", month: "short", day: "numeric" })}</span>}
            </span>
          }
          aside={<Pill info={backfillStatus(b.status)} />}
        >
          Why-gap in the last {b.considered} merged PRs
        </PageTitle>
        <p className="-mt-3 max-w-[760px] font-display text-xl leading-8 text-ink">
          {sentence ?? `No consequential decision has been checked yet in ${repo}.`}
        </p>
        <p className="mt-2 text-sm text-ink-3">
          In this sample only. <MethodPopover b={b} />
        </p>
      </div>

      <section aria-labelledby="bf-numbers">
        <h2 id="bf-numbers" className="sr-only">Numbers</h2>
        <div className="grid grid-cols-2 gap-y-6 sm:grid-cols-3 lg:grid-cols-5">
          <Stat label="Why-gap rate" value={ratePercent(b)} sub={checked ? `${gaps} of ${checked} decisions` : "no decisions yet"} />
          <Stat label="PRs in sample" value={b.considered} sub={`of ${b.sample_size}; ${b.skipped_deterministic} skipped by rule`} />
          <Stat label="Screened" value={b.screened} sub={`${b.consequential} consequential`} />
          <Stat label="PRs with a gap" value={b.prs_with_gaps ?? "—"} sub={`of ${b.analyzed} analyzed`} />
          <Stat label="Gemini calls" value={b.calls_used} sub={b.budget_calls ? `last run's budget ${b.budget_calls}` : undefined} />
        </div>
      </section>

      <section aria-labelledby="bf-status">
        <SectionHeading id="bf-status" aside={`${b.decisions_total ?? checked + b.decisions.self_evident} decisions`}>
          Decisions by status
        </SectionHeading>
        {checked + b.decisions.self_evident ? (
          <Card className="p-4 sm:p-5">
            <DecisionBar counts={b.decisions} />
          </Card>
        ) : (
          <EmptyState title="No decisions checked yet.">
            Decisions appear here as the backfill analyzes consequential PRs.
          </EmptyState>
        )}
        <div className="mt-4">
          <Coverage b={b} rows={rows} />
        </div>
      </section>

      <section aria-labelledby="bf-prs">
        <SectionHeading id="bf-prs" aside={`${rows.length} of ${b.sample_size} PRs have a result`}>
          Pull requests in the sample
        </SectionHeading>
        {itemsError && <ErrorNote error={itemsError} />}
        {itemsLoading ? (
          <Skeleton className="h-48 w-full" />
        ) : rows.length === 0 ? (
          <EmptyState title="No PR results yet.">Each PR appears here as soon as the backfill finishes it.</EmptyState>
        ) : (
          <Card className="overflow-hidden">
            <table className="w-full table-fixed border-collapse text-left text-sm">
              <thead className="border-b border-line bg-surface-2 text-xs uppercase tracking-wide text-ink-3">
                <tr>
                  <th scope="col" className="w-[76px] px-3 py-2 font-medium sm:w-[90px] sm:px-4">PR</th>
                  <th scope="col" className="px-3 py-2 font-medium sm:px-4">Title</th>
                  <th scope="col" className="hidden w-[150px] px-4 py-2 font-medium md:table-cell">Status</th>
                  <th scope="col" className="hidden w-[30%] px-4 py-2 font-medium lg:table-cell">Decisions</th>
                  <th scope="col" className="w-[84px] px-3 py-2 text-right font-medium sm:w-[96px] sm:px-4">Analysis</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((it) => (
                  <tr key={it.id} className="border-b border-line align-top last:border-b-0">
                    <td className="px-3 py-2.5 sm:px-4">
                      <Ext href={it.url ?? prUrl(repo, it.pr)} className="mono">#{it.pr}</Ext>
                    </td>
                    <td className="px-3 py-2.5 sm:px-4">
                      <div className="break-words text-ink"><InlineMd text={it.title ?? ""} /></div>
                      <div className="mt-1 md:hidden">
                        <Pill size="xs" info={backfillItemStatus(it.status, it.error)} />
                      </div>
                      <div className="mt-1 text-xs text-ink-2 lg:hidden">
                        <DecisionSummary item={it} />
                      </div>
                    </td>
                    <td className="hidden px-4 py-2.5 md:table-cell">
                      <Pill size="xs" info={backfillItemStatus(it.status, it.error)} />
                    </td>
                    <td className="hidden px-4 py-2.5 text-xs lg:table-cell">
                      <DecisionSummary item={it} />
                    </td>
                    <td className="px-3 py-2.5 text-right sm:px-4">
                      {it.analysis_id ? <Link to={`/pr/${repo}/${it.pr}`}>View</Link> : <span className="text-ink-3">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {b.sample_size > rows.length && (
              <p className="border-t border-line bg-surface-2 px-3 py-2.5 text-sm text-ink-3 sm:px-4">
                {b.sample_size - rows.length} older PR{b.sample_size - rows.length > 1 ? "s" : ""} of the sample not
                reached yet. They appear here when a <Code>--resume</Code> run gets to them.
              </p>
            )}
          </Card>
        )}
      </section>
    </div>
  );
}
