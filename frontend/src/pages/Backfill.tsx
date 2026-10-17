import { useMemo } from "react";
import { Link, useParams } from "react-router";
import { DecisionBar, MethodPopover } from "../components/Backfill";
import { Card, Code, CopyButton, EmptyState, ErrorNote, Ext, PageTitle, Pill, SectionHeading, Skeleton, Stat } from "../components/ui";
import { asBackfill, asBackfillItem, backfillItemsQuery, backfillRef, repoDocId } from "../lib/data";
import { gapCounts, headline, ratePercent } from "../lib/backfill";
import { prUrl, toDate } from "../lib/format";
import { useLiveDoc, useLiveQuery } from "../lib/hooks";
import { backfillItemStatus, backfillStatus, rationaleStatus } from "../lib/status";
import type { BackfillDoc, BackfillItemDoc, RationaleStatus } from "../lib/types";

const ORDER: RationaleStatus[] = ["missing", "partial", "found", "self_evident"];

function DecisionSummary({ item }: { item: BackfillItemDoc }) {
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

function Caveats({ b, pending, errors }: { b: BackfillDoc; pending: number; errors: number }) {
  const notes: string[] = [];
  if (b.considered < b.sample_size)
    notes.push(
      `${b.considered} of the ${b.sample_size} PRs in the sample have a result so far; the numbers cover those ${b.considered} only.`,
    );
  if (pending) notes.push(`${pending} consequential PR${pending > 1 ? "s are" : " is"} screened but not analyzed yet, so ${pending > 1 ? "their" : "its"} decisions are not counted.`);
  if (errors) notes.push(`${errors} PR${errors > 1 ? "s" : ""} failed and ${errors > 1 ? "are" : "is"} left out.`);
  if (!notes.length) return null;
  return (
    <ul className="grid gap-1.5 text-sm text-ink-2">
      {notes.map((n) => (
        <li key={n} className="tone-muted rounded-[var(--radius)] border px-3 py-2">{n}</li>
      ))}
    </ul>
  );
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

  if (bf.loading) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-9 w-1/2" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (bf.error) return <ErrorNote error={bf.error} />;
  if (!b) {
    const cmd = `uv run python scripts/backfill.py ${repo} --limit 22 --budget-calls 40`;
    return (
      <>
        <PageTitle eyebrow={<span>Backfill · <Ext href={`https://github.com/${repo}`} className="mono">{repo}</Ext></span>}>
          No backfill for this repo yet
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
          <Caveats b={b} pending={b.pending ?? 0} errors={b.errors ?? 0} />
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
                      <div className="break-words text-ink">{it.title}</div>
                      <div className="mt-1 md:hidden">
                        <Pill size="xs" info={backfillItemStatus(it.status)} />
                      </div>
                      <div className="mt-1 text-xs text-ink-2 lg:hidden">
                        <DecisionSummary item={it} />
                      </div>
                    </td>
                    <td className="hidden px-4 py-2.5 md:table-cell">
                      <Pill size="xs" info={backfillItemStatus(it.status)} />
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
          </Card>
        )}
      </section>
    </div>
  );
}
