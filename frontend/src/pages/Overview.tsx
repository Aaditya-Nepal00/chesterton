import { useMemo, type ReactNode } from "react";
import { Link } from "react-router";
import { MethodPopover } from "../components/Backfill";
import { DecisionRecordView } from "../components/DecisionRecordView";
import { BackfillRow, RunRow, RunRowSkeleton } from "../components/RunRow";
import { Card, cx, EmptyState, ErrorNote, Ext, InlineMd, SectionHeading, Skeleton, Stat } from "../components/ui";
import {
  allBackfillsQuery,
  asAnalysis,
  asBackfill,
  asDecision,
  asGuard,
  asQuestionSet,
  asRun,
  backfillsQuery,
  inReposQuery,
  isBackfillRun,
  isLiveRun,
  latestRunsQuery,
  newestFirst,
} from "../lib/data";
import { backfillPath, gapCounts, ratePercent } from "../lib/backfill";
import { REPO_URL } from "../lib/firebase";
import { percent, prUrl, shortRepo, toDate } from "../lib/format";
import { useLiveQuery, useNow } from "../lib/hooks";
import type { AnalysisDoc, BackfillDoc, DecisionRecordDoc, GuardDoc, QuestionSetDoc, RunDoc } from "../lib/types";

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

/** What Overview reads: live Action runs and the repos they touched, kept apart from backfill
    runs (a read-only measurement) and Build-3 CLI runs. Everything below is scoped from here. */
function useLiveScope() {
  const runs = useLiveQuery(() => latestRunsQuery(100), asRun, []);
  const repos = useMemo(() => [...new Set(runs.data.filter(isLiveRun).map((r) => r.repo))].sort(), [runs.data]);
  const key = repos.join(",");
  const analyses = useLiveQuery(() => inReposQuery("pr_analyses", repos), asAnalysis, [key]);
  const qsets = useLiveQuery(() => inReposQuery("questions", repos), asQuestionSet, [key]);
  const decisions = useLiveQuery(() => inReposQuery("decisions", repos), asDecision, [key]);
  const guards = useLiveQuery(() => inReposQuery("guards", repos), asGuard, [key]);
  return useMemo(() => {
    // An analysis written by a backfill or CLI run is not live, even on a live repo.
    const notLive = new Set(runs.data.filter((r) => !isLiveRun(r)).map((r) => r.run_id));
    return {
      runsLoading: runs.loading,
      runsError: runs.error,
      repos,
      liveRuns: runs.data.filter(isLiveRun),
      backfillRuns: runs.data.filter(isBackfillRun),
      analyses: {
        loading: runs.loading || analyses.loading,
        data: newestFirst(analyses.data.filter((x) => !notLive.has(x.run_id)), (x) => x.updated_at),
      },
      qsets: { loading: runs.loading || qsets.loading, data: newestFirst(qsets.data, (x) => x.updated_at) },
      // One reply records several at the same instant: highest number first among equals.
      decisions: {
        loading: runs.loading || decisions.loading,
        data: newestFirst([...decisions.data].sort((x, y) => y.id.localeCompare(x.id)), (x) => x.created_at),
      },
      guards: { loading: runs.loading || guards.loading, data: newestFirst(guards.data, (x) => x.updated_at) },
    };
  }, [runs, repos, analyses, qsets, decisions, guards]);
}
type Scope = ReturnType<typeof useLiveScope>;

/** The newest batch of records (one reply can record several), lowest number first. */
function latestBatch(decisions: DecisionRecordDoc[]): DecisionRecordDoc[] {
  const d = decisions[0];
  if (!d) return [];
  return decisions
    .filter((x) => x.repo === d.repo && x.source_pr === d.source_pr)
    .sort((x, y) => x.id.localeCompare(y.id));
}

function LoopStrip({ scope }: { scope: Scope }) {
  const a: AnalysisDoc | undefined = scope.analyses.data[0];
  const q: QuestionSetDoc | undefined = scope.qsets.data[0];
  const batch = latestBatch(scope.decisions.data);
  // The guard step shows a guard that actually surfaced a decision when there is one.
  const g: GuardDoc | undefined = scope.guards.data.find((x) => (x.matched_count ?? 0) > 0) ?? scope.guards.data[0];
  const firstQ = q?.questions.find((x) => x.status === "open") ?? q?.questions[0];
  const gj = g?.judgements[0];
  const range = batch.length > 1 ? `${batch[0].id}–${batch[batch.length - 1].id}` : batch[0]?.id;
  return (
    <section aria-labelledby="loop">
      <h2 id="loop" className="sr-only">How the loop works, with the latest live example of each step</h2>
      <ol className="flex flex-col gap-6 sm:flex-row sm:gap-5">
        <LoopStep n={1} title="Merge" verb="A PR is merged; Chesterton reads it." loading={scope.analyses.loading}>
          {a ? (
            <span>
              <Link to={`/pr/${a.repo}/${a.number}`} className="mono" title={`${a.repo}#${a.number}`}>
                {shortRepo(a.repo)}#{a.number}
              </Link>{" "}
              <span className="text-ink">
                <InlineMd text={a.title} />
              </span>
            </span>
          ) : (
            <span className="text-ink-3">No PR analyzed yet.</span>
          )}
        </LoopStep>
        <LoopStep n={2} title="Ask why" verb="Decisions with no written reason get a question." loading={scope.qsets.loading}>
          {firstQ && q ? (
            <span>
              “<InlineMd text={firstQ.text} />”{" "}
              <Ext href={q.comment_url} className="whitespace-nowrap text-xs">
                {q.questions.length > 1 ? `${q.questions.length} questions ` : ""}on #{q.pr}
              </Ext>
            </span>
          ) : (
            <span className="text-ink-3">No questions asked yet.</span>
          )}
        </LoopStep>
        <LoopStep n={3} title="Record" verb="The author's answer becomes decisions/NNNN.md." loading={scope.decisions.loading}>
          {batch.length ? (
            <span className="grid gap-1">
              <span className="text-ink-3">
                <span className="mono text-ink">{range}</span> from the answer on #{batch[0].source_pr}
              </span>
              <ul className="grid gap-0.5">
                {batch.slice(-3).map((d) => (
                  <li key={d.docId} className="flex gap-1.5">
                    <Link to={`/decisions/${d.docId}`} className="mono shrink-0">
                      {d.id}
                    </Link>
                    <span className="text-ink">{d.title}</span>
                  </li>
                ))}
              </ul>
            </span>
          ) : (
            <span className="text-ink-3">No decision recorded yet.</span>
          )}
        </LoopStep>
        <LoopStep n={4} title="Guard" verb="A later PR touching it gets the reason and owner." loading={scope.guards.loading}>
          {g ? (
            <span>
              <Ext href={g.comment_url ?? prUrl(g.repo, g.pr)} className="mono">
                <span title={`${g.repo}#${g.pr}`}>
                  {shortRepo(g.repo)}#{g.pr}
                </span>
              </Ext>{" "}
              {gj ? (
                <span className="text-ink">
                  {gj.decision_number ? (
                    <span className="mono text-ink-3">{String(gj.decision_number).padStart(4, "0")} · </span>
                  ) : null}
                  {gj.note || "touches a recorded decision"}
                </span>
              ) : (
                "no recorded decision touched"
              )}
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
      <span>{checked ? `${gaps} of ${checked} decisions, last ${bf.considered} PRs` : "no decisions yet"}</span>
      <span>
        <MethodPopover b={bf} />
      </span>
    </span>
  );
}

/** A labelled run of KPI tiles: the label says where the numbers come from. */
function KpiGroup({ label, children, className }: { label: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cx("min-w-0", className)}>
      <div className="mb-3 border-b border-line pb-1.5 text-xs uppercase tracking-[0.08em] text-ink-3">{label}</div>
      {children}
    </div>
  );
}

function Kpis({ scope }: { scope: Scope }) {
  const backfills = useLiveQuery(backfillsQuery, asBackfill, []);
  const asked = scope.qsets.data.reduce((n, q) => n + (q.n_questions ?? q.questions.length), 0);
  const answered = scope.qsets.data.reduce(
    (n, q) => n + (q.n_answered ?? q.questions.filter((x) => x.status === "answered").length),
    0,
  );
  const warnings = scope.guards.data.filter((g) => (g.matched_count ?? 0) > 0).length;
  const bf = backfills.data[0];
  const repos = scope.repos;
  const liveLabel = scope.runsLoading ? (
    "Live installs"
  ) : repos.length === 1 ? (
    <>
      Live install ·{" "}
      <span className="mono normal-case tracking-normal" title={repos[0]}>
        {shortRepo(repos[0])}
      </span>
    </>
  ) : (
    <span title={repos.join(", ")}>Live installs · {repos.length} repos</span>
  );
  return (
    <section aria-labelledby="kpis">
      <h2 id="kpis" className="sr-only">Totals</h2>
      <div className="grid gap-x-8 gap-y-8 lg:grid-cols-[5fr_1fr]">
        <KpiGroup label={liveLabel}>
          <div className="grid grid-cols-2 gap-y-6 sm:grid-cols-3 lg:grid-cols-5">
            <Stat label="PRs analyzed" value={scope.analyses.data.length} loading={scope.analyses.loading} />
            <Stat label="Questions asked" value={asked} loading={scope.qsets.loading} />
            <Stat
              label="Answer rate"
              value={percent(answered, asked)}
              sub={asked ? `${answered} of ${asked} answered` : "no questions yet"}
              loading={scope.qsets.loading}
            />
            <Stat label="Decisions recorded" value={scope.decisions.data.length} loading={scope.decisions.loading} />
            <Stat label="Guard warnings" value={warnings} sub="PRs shown a decision" loading={scope.guards.loading} />
          </div>
        </KpiGroup>
        <KpiGroup
          label={
            bf ? (
              <Link to={backfillPath(bf.repo)} className="text-ink-3 no-underline hover:text-accent">
                Backfill ·{" "}
                <span className="mono normal-case tracking-normal" title={bf.repo}>
                  {shortRepo(bf.repo)}
                </span>
              </Link>
            ) : (
              "Backfill"
            )
          }
        >
          <Stat
            label="Why-gap rate"
            value={bf ? ratePercent(bf) : "—"}
            sub={bf ? <WhyGapSub bf={bf} /> : "Run a backfill to measure"}
            loading={backfills.loading}
          />
        </KpiGroup>
      </div>
    </section>
  );
}

type Entry = { at: number; node: ReactNode };

function Activity({ scope }: { scope: Scope }) {
  const backfills = useLiveQuery(allBackfillsQuery, asBackfill, []);
  const now = useNow();
  const entries = useMemo(() => {
    const out: Entry[] = scope.liveRuns.slice(0, 15).map((r) => ({
      at: toDate(r.started_at)?.getTime() ?? 0,
      node: <RunRow key={r.id} run={r} now={now} />,
    }));
    // One row per backfilled repo, placed at its newest run.
    const byRepo = new Map<string, RunDoc[]>();
    for (const r of scope.backfillRuns) byRepo.set(r.repo, [...(byRepo.get(r.repo) ?? []), r]);
    for (const [repo, runs] of byRepo) {
      const b = backfills.data.find((x) => x.repo === repo);
      out.push({
        at: toDate(runs[0].started_at)?.getTime() ?? 0,
        node: <BackfillRow key={`backfill:${repo}`} repo={repo} runs={runs} b={b} now={now} />,
      });
    }
    return out.sort((x, y) => y.at - x.at);
  }, [scope.liveRuns, scope.backfillRuns, backfills.data, now]);
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
      {scope.runsError && <ErrorNote error={scope.runsError} />}
      <Card className="overflow-hidden">
        {scope.runsLoading ? (
          <ul>
            {Array.from({ length: 5 }, (_, i) => (
              <RunRowSkeleton key={i} />
            ))}
          </ul>
        ) : entries.length === 0 ? (
          <div className="p-5">
            <EmptyState title="No runs yet.">
              Every Action run (analyze on merge, record on reply, guard on new PRs) appears here as it happens,
              with the model that answered and a step-by-step trace.
            </EmptyState>
          </div>
        ) : (
          <ul>{entries.map((e) => e.node)}</ul>
        )}
      </Card>
      <p className="mt-2 text-xs text-ink-3">
        Action runs on live repos. A backfill is read-only and posts nothing, so it shows as one row; its PRs are on
        the Backfill page.
      </p>
    </section>
  );
}

function LatestDecision({ scope }: { scope: Scope }) {
  const latest = scope.decisions;
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
  const scope = useLiveScope();
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
      <LoopStrip scope={scope} />
      <Kpis scope={scope} />
      <Activity scope={scope} />
      <LatestDecision scope={scope} />
    </div>
  );
}
