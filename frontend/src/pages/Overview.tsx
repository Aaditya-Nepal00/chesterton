import type { ReactNode } from "react";
import { Link } from "react-router";
import { MethodPopover } from "../components/Backfill";
import { DecisionRecordView } from "../components/DecisionRecordView";
import { RunRow, RunRowSkeleton } from "../components/RunRow";
import { Card, EmptyState, ErrorNote, Ext, InlineMd, SectionHeading, Skeleton, Stat } from "../components/ui";
import {
  allQuestionSetsQuery,
  asAnalysis,
  asBackfill,
  asDecision,
  asGuard,
  asQuestionSet,
  asRun,
  backfillsQuery,
  countAll,
  guardWarningsQuery,
  latestOf,
  latestRunsQuery,
} from "../lib/data";
import { backfillPath, gapCounts, ratePercent } from "../lib/backfill";
import { REPO_URL } from "../lib/firebase";
import { percent, prUrl } from "../lib/format";
import { useCount, useLiveQuery, useNow, useSums } from "../lib/hooks";
import type { AnalysisDoc, BackfillDoc, DecisionRecordDoc, GuardDoc, QuestionSetDoc } from "../lib/types";

function LoopStep({
  n,
  title,
  verb,
  children,
  loading,
}: {
  n: number;
  title: string;
  verb: string;
  children: ReactNode;
  loading: boolean;
}) {
  return (
    <li className="relative min-w-0 flex-1 border-t-2 border-accent pt-3">
      <div className="flex items-baseline gap-2">
        <span className="mono text-xs text-accent">{String(n).padStart(2, "0")}</span>
        <span className="font-semibold text-ink">{title}</span>
      </div>
      <div className="mt-0.5 text-sm text-ink-3">{verb}</div>
      <div className="mt-2 text-sm text-ink-2">{loading ? <Skeleton className="h-4 w-4/5" /> : children}</div>
    </li>
  );
}

function LoopStrip() {
  const analyses = useLiveQuery(() => latestOf("pr_analyses"), asAnalysis, []);
  const qsets = useLiveQuery(() => latestOf("questions"), asQuestionSet, []);
  const decisions = useLiveQuery(() => latestOf("decisions", "created_at"), asDecision, []);
  const guards = useLiveQuery(() => latestOf("guards"), asGuard, []);
  const a: AnalysisDoc | undefined = analyses.data[0];
  const q: QuestionSetDoc | undefined = qsets.data[0];
  const d: DecisionRecordDoc | undefined = decisions.data[0];
  const g: GuardDoc | undefined = guards.data[0];
  const firstQ = q?.questions.find((x) => x.status === "open") ?? q?.questions[0];
  const gj = g?.judgements[0];
  return (
    <section aria-labelledby="loop">
      <h2 id="loop" className="sr-only">How the loop works, with the latest real example of each step</h2>
      <ol className="flex flex-col gap-6 sm:flex-row sm:gap-5">
        <LoopStep n={1} title="Merge" verb="A PR is merged; Chesterton reads it." loading={analyses.loading}>
          {a ? (
            <span>
              <Link to={`/pr/${a.repo}/${a.number}`} className="mono">{a.repo}#{a.number}</Link>{" "}
              <span className="text-ink">{a.title}</span>
            </span>
          ) : (
            <span className="text-ink-3">No PR analyzed yet.</span>
          )}
        </LoopStep>
        <LoopStep n={2} title="Ask why" verb="Decisions with no written reason get a question." loading={qsets.loading}>
          {firstQ && q ? (
            <span>
              “<InlineMd text={firstQ.text} />”{" "}
              <Ext href={q.comment_url} className="whitespace-nowrap text-xs">on #{q.pr}</Ext>
            </span>
          ) : (
            <span className="text-ink-3">No questions asked yet.</span>
          )}
        </LoopStep>
        <LoopStep n={3} title="Record" verb="The author's answer becomes decisions/NNNN.md." loading={decisions.loading}>
          {d ? (
            <span>
              <Link to={`/decisions/${d.docId}`} className="mono">{d.id}</Link>{" "}
              <span className="text-ink">{d.title}</span>
            </span>
          ) : (
            <span className="text-ink-3">No decision recorded yet.</span>
          )}
        </LoopStep>
        <LoopStep n={4} title="Guard" verb="A later PR touching it gets the reason and owner." loading={guards.loading}>
          {g ? (
            <span>
              <Ext href={g.comment_url ?? prUrl(g.repo, g.pr)} className="mono">{g.repo}#{g.pr}</Ext>{" "}
              {gj ? <span className="text-ink">{gj.note || "touches a recorded decision"}</span> : "no recorded decision touched"}
            </span>
          ) : (
            <span className="text-ink-3">No guard comments yet.</span>
          )}
        </LoopStep>
      </ol>
    </section>
  );
}

function WhyGapSub({ bf }: { bf: BackfillDoc }) {
  const { gaps, checked } = gapCounts(bf.decisions);
  return (
    <span className="grid gap-0.5">
      <span>
        {checked ? `${gaps} of ${checked} decisions, ` : "no decisions yet, "}
        <Link to={backfillPath(bf.repo)}>
          last {bf.considered} PRs of <span className="mono">{bf.repo}</span>
        </Link>
      </span>
      <span>
        In this sample. <MethodPopover b={bf} />
      </span>
    </span>
  );
}

function Kpis() {
  const analyzed = useCount(() => countAll("pr_analyses"), 0);
  const decisions = useCount(() => countAll("decisions"), 0);
  const guardWarnings = useCount(guardWarningsQuery, 0);
  const backfills = useLiveQuery(backfillsQuery, asBackfill, []);
  // Server-side sum() over the per-PR counters (n_questions / n_answered).
  const qsums = useSums(allQuestionSetsQuery, { asked: "n_questions", answered: "n_answered" }, 0);
  const asked = qsums.data?.asked ?? 0;
  const answered = qsums.data?.answered ?? 0;
  const qsets = { loading: qsums.loading };
  const bf = backfills.data[0];
  return (
    <section aria-labelledby="kpis">
      <h2 id="kpis" className="sr-only">Totals</h2>
      <div className="grid grid-cols-2 gap-y-6 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="PRs analyzed" value={analyzed.data ?? "—"} loading={analyzed.loading} />
        <Stat label="Questions asked" value={asked} loading={qsets.loading} />
        <Stat
          label="Answer rate"
          value={percent(answered, asked)}
          sub={asked ? `${answered} of ${asked} answered` : "no questions yet"}
          loading={qsets.loading}
        />
        <Stat label="Decisions recorded" value={decisions.data ?? "—"} loading={decisions.loading} />
        <Stat label="Guard warnings" value={guardWarnings.data ?? "—"} loading={guardWarnings.loading} />
        <Stat
          label="Why-gap rate"
          value={bf ? ratePercent(bf) : "—"}
          sub={bf ? <WhyGapSub bf={bf} /> : "Run a backfill to measure"}
          loading={backfills.loading}
        />
      </div>
    </section>
  );
}

function Activity() {
  const runs = useLiveQuery(() => latestRunsQuery(15), asRun, []);
  const now = useNow();
  return (
    <section aria-labelledby="activity">
      <SectionHeading
        id="activity"
        aside={
          <span className="inline-flex items-center gap-1.5">
            <span className="pulse inline-block h-2 w-2 rounded-full bg-accent" aria-hidden="true" /> live
          </span>
        }
      >
        Live activity
      </SectionHeading>
      {runs.error && <ErrorNote error={runs.error} />}
      <Card className="overflow-hidden">
        {runs.loading ? (
          <ul>
            {Array.from({ length: 5 }, (_, i) => (
              <RunRowSkeleton key={i} />
            ))}
          </ul>
        ) : runs.data.length === 0 ? (
          <div className="p-5">
            <EmptyState title="No runs yet.">
              Every Action run (analyze on merge, record on reply, guard on new PRs) appears here as it happens,
              with the model that answered and a step-by-step trace.
            </EmptyState>
          </div>
        ) : (
          <ul>
            {runs.data.map((r) => (
              <RunRow key={r.id} run={r} now={now} />
            ))}
          </ul>
        )}
      </Card>
    </section>
  );
}

function LatestDecision() {
  const latest = useLiveQuery(() => latestOf("decisions", "created_at"), asDecision, []);
  return (
    <section aria-labelledby="latest-decision">
      <SectionHeading id="latest-decision" aside={<Link to="/decisions">All decisions</Link>}>
        Latest decision record
      </SectionHeading>
      {latest.loading ? (
        <Card className="p-6">
          <Skeleton className="h-6 w-2/3" />
          <Skeleton className="mt-4 h-4 w-full" />
          <Skeleton className="mt-2 h-4 w-5/6" />
        </Card>
      ) : latest.data[0] ? (
        <DecisionRecordView d={latest.data[0]} compact />
      ) : (
        <EmptyState title="No decision records yet.">
          When an author answers a question on their PR, Chesterton turns the answer into{" "}
          <span className="mono">decisions/NNNN-slug.md</span> and opens a PR. The newest one is shown here.
        </EmptyState>
      )}
    </section>
  );
}

export function Overview() {
  return (
    <div className="grid gap-12">
      <header className="grid gap-4 border-b border-line pb-8 lg:grid-cols-[1fr_auto] lg:items-end">
        <div className="max-w-[760px]">
          <h1 className="font-display text-3xl font-medium leading-[44px] tracking-[-0.01em] text-ink">
            Chesterton asks why at merge time and keeps the answer next to the code.
          </h1>
          <p className="mt-3 text-lg text-ink-2">
            For teams whose limits, dependencies and config changes outlive the people who chose them.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link
            to="/install"
            className="rounded bg-accent px-3.5 py-2 text-sm font-medium text-accent-ink no-underline hover:bg-accent-hover hover:text-accent-ink"
          >
            Install
          </Link>
          <Ext
            href={REPO_URL}
            className="rounded border border-line-strong bg-surface px-3.5 py-2 text-sm font-medium text-ink no-underline hover:border-accent hover:text-accent"
          >
            GitHub repo
          </Ext>
        </div>
      </header>
      <LoopStrip />
      <Kpis />
      <Activity />
      <LatestDecision />
    </div>
  );
}
