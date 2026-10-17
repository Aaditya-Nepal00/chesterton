import { useMemo } from "react";
import { Link, useParams } from "react-router";
import { DecisionRecordView } from "../components/DecisionRecordView";
import { Card, cx, EmptyState, ErrorNote, PageTitle, Skeleton } from "../components/ui";
import { asDecision, asGuard, decisionRef, decisionsQuery, guardsForRepoQuery } from "../lib/data";
import { useLiveDoc, useLiveQuery } from "../lib/hooks";
import type { DecisionRecordDoc } from "../lib/types";

function DecisionDetail({ docId }: { docId: string }) {
  const d = useLiveDoc(() => decisionRef(docId), asDecision, [docId]);
  const repo = d.data?.repo;
  const guards = useLiveQuery(() => (repo ? guardsForRepoQuery(repo) : null), asGuard, [repo]);
  const referencing = useMemo(
    () =>
      d.data
        ? guards.data.filter((g) => g.matches.some((m) => m.decision_number === d.data!.number))
        : [],
    [guards.data, d.data],
  );
  if (d.loading) return <Skeleton className="h-96 w-full" />;
  if (d.error) return <ErrorNote error={d.error} />;
  if (!d.data)
    return (
      <EmptyState title="This decision record doesn't exist." action={<Link to="/decisions">All decisions</Link>}>
        Record ids look like <span className="mono">owner__repo__0001</span>.
      </EmptyState>
    );
  return <DecisionRecordView d={d.data} guards={guards.loading ? undefined : referencing} />;
}

function DecisionList({ items, active }: { items: DecisionRecordDoc[]; active?: string }) {
  return (
    <Card className="overflow-hidden">
      <ul>
        {items.map((d) => (
          <li key={d.docId} className="border-b border-line last:border-b-0">
            <Link
              to={`/decisions/${d.docId}`}
              aria-current={active === d.docId ? "page" : undefined}
              className={cx(
                "block px-4 py-3 no-underline hover:bg-surface-2",
                active === d.docId && "bg-accent-soft",
              )}
            >
              <div className="flex items-baseline gap-2">
                <span className="mono text-sm text-ink-3">{d.id}</span>
                <span className="font-display text-lg font-medium leading-6 text-ink">{d.title}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-ink-3">
                {d.owner && <span>@{d.owner}</span>}
                <span className="mono">{d.repo}</span>
                <span className="mono">{d.recorded}</span>
              </div>
              <div className="mono mt-1 truncate text-xs text-ink-2">{d.paths.join(", ")}</div>
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function Decisions() {
  const { docId } = useParams();
  const list = useLiveQuery(decisionsQuery, asDecision, []);
  const selected = docId ?? list.data[0]?.docId;

  return (
    <>
      <PageTitle eyebrow="decisions/">Decision records</PageTitle>
      {list.error && <ErrorNote error={list.error} />}
      {list.loading ? (
        <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      ) : list.data.length === 0 ? (
        <EmptyState title="No decisions recorded yet.">
          Chesterton asks why when a PR merges with an unexplained choice. When the author replies, the answer becomes{" "}
          <span className="mono">decisions/NNNN-slug.md</span> (quoted verbatim, with a link to the reply), and it's
          listed here.
        </EmptyState>
      ) : (
        <div className="grid items-start gap-6 lg:grid-cols-[360px_1fr]">
          {/* On small screens the detail replaces the list when one is open. */}
          <div className={cx(docId && "hidden lg:block")}>
            <DecisionList items={list.data} active={selected} />
          </div>
          <div className={cx(!docId && "hidden lg:block")}>
            {docId && (
              <Link to="/decisions" className="mb-3 inline-block text-sm lg:hidden">
                ← All decisions
              </Link>
            )}
            {selected && <DecisionDetail docId={selected} />}
          </div>
        </div>
      )}
    </>
  );
}
