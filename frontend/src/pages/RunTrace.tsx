import type { ReactNode } from "react";
import { Link, useParams } from "react-router";
import { Timeline } from "../components/Timeline";
import { Card, EmptyState, ErrorNote, Ext, KindChip, PageTitle, Pill, SectionHeading, Skeleton } from "../components/ui";
import { analysisRef, asAnalysis, asEvent, asRun, eventsQuery, modelsAnswered, runDurationMs, runPr, runRef } from "../lib/data";
import { formatDateTime, formatDuration, prDocId, prUrl, toDate } from "../lib/format";
import { useLiveDoc, useLiveQuery } from "../lib/hooks";
import { inferKind } from "../lib/nodes";
import { runStatus } from "../lib/status";

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs uppercase tracking-[0.08em] text-ink-3">{label}</dt>
      <dd className="mt-0.5 text-sm text-ink">{children}</dd>
    </div>
  );
}

export function RunTrace() {
  const { id = "" } = useParams();
  const run = useLiveDoc(() => runRef(id), asRun, [id]);
  const events = useLiveQuery(() => eventsQuery(id), asEvent, [id]);
  const r = run.data;
  const pr = r ? runPr(r) : undefined;
  const kind = r ? inferKind(r) : "analyze";
  const analysis = useLiveDoc(
    () => (r && pr != null && kind === "analyze" ? analysisRef(prDocId(r.repo, pr)) : null),
    asAnalysis,
    [r?.repo, pr, kind],
  );
  const notesForThisRun = analysis.data && analysis.data.run_id === id ? analysis.data.verifier_notes : null;
  const models = r ? modelsAnswered(r) : [];
  const live = r?.status === "running";

  if (run.loading) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-9 w-2/3" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (run.error) return <ErrorNote error={run.error} />;
  if (!r) {
    return (
      <>
        <PageTitle>Run not found</PageTitle>
        <EmptyState title={`No run with id ${id}.`} action={<Link to="/">See recent runs</Link>}>
          Runs are written by the GitHub Action as <span className="mono">runs/&lt;run_id&gt;</span>; the id looks
          like <span className="mono">20261017T113434Z-d33a58</span>.
        </EmptyState>
      </>
    );
  }

  const failedCalls = (r.llm_calls_detail ?? []).filter((c) => !c.ok);
  return (
    <div className="grid gap-10">
      <div>
        <PageTitle
          eyebrow={
            <span className="flex flex-wrap items-center gap-2">
              <KindChip kind={kind} /> <span className="mono">{r.run_id}</span>
            </span>
          }
          aside={<Pill info={runStatus(r.status)} />}
        >
          {pr != null ? (
            <>
              {r.repo}
              <span className="text-ink-3">#</span>
              {pr}
            </>
          ) : (
            r.repo
          )}
        </PageTitle>
        {r.summary && <p className="-mt-4 mb-6 max-w-[860px] text-ink-2">{r.summary}</p>}
        <Card className="p-4 sm:p-5">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-6">
            <Meta label="Started">{formatDateTime(toDate(r.started_at))}</Meta>
            <Meta label="Duration">
              <span className="mono">{live ? "running…" : formatDuration(runDurationMs(r))}</span>
            </Meta>
            <Meta label="Gemini calls">
              <span className="mono">{r.llm_calls ?? 0}</span>
              {!!r.llm_failed_calls && <span className="text-ink-3"> ({r.llm_failed_calls} failed, retried)</span>}
            </Meta>
            <Meta label="Models used">
              <span className="mono break-words">{models.length ? models.join(", ") : "none"}</span>
            </Meta>
            <Meta label="Pull request">
              {pr != null ? <Ext href={prUrl(r.repo, pr)}>on GitHub</Ext> : "—"}
              {kind === "analyze" && pr != null && (
                <>
                  {" · "}
                  <Link to={`/pr/${r.repo}/${pr}`}>analysis</Link>
                </>
              )}
            </Meta>
            <Meta label="Actions run">
              {r.actions_run_url ? <Ext href={r.actions_run_url}>logs</Ext> : <span className="text-ink-3">local run</span>}
            </Meta>
          </dl>
        </Card>
      </div>

      <section aria-labelledby="timeline">
        <SectionHeading
          id="timeline"
          aside={
            <span className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
              <span className="inline-flex items-center gap-1.5"><span className="bar-accent inline-block h-2 w-4 rounded-sm" aria-hidden="true" /> Gemini</span>
              <span className="inline-flex items-center gap-1.5"><span className="bar-code inline-block h-2 w-4 rounded-sm" aria-hidden="true" /> plain code</span>
              <span className="inline-flex items-center gap-1.5"><span className="bar-fail inline-block h-2 w-4 rounded-sm" aria-hidden="true" /> failed</span>
              {live && <span className="inline-flex items-center gap-1.5 text-accent"><span className="pulse inline-block h-2 w-2 rounded-full bg-accent" aria-hidden="true" /> live</span>}
            </span>
          }
        >
          Agent timeline
        </SectionHeading>
        {events.error && <ErrorNote error={events.error} />}
        {events.loading ? (
          <Skeleton className="h-72 w-full" />
        ) : events.data.length === 0 ? (
          <EmptyState title="No steps recorded for this run yet.">
            Steps are written to <span className="mono">runs/{id}/events</span> when the run finishes, or as it runs.
            This view updates by itself.
          </EmptyState>
        ) : (
          <Timeline events={events.data} run={r} live={live} />
        )}
        <p className="mt-2 text-xs text-ink-3">
          Bars share one time scale, so steps that ran in parallel overlap. Rows marked self-check are deterministic
          verification of the model's output.
        </p>
      </section>

      {(notesForThisRun?.length || failedCalls.length > 0) && (
        <section aria-labelledby="checks" className="grid gap-6 lg:grid-cols-2">
          {notesForThisRun && notesForThisRun.length > 0 && (
            <div>
              <SectionHeading id="checks">Verifier notes</SectionHeading>
              <ul className="grid gap-2">
                {notesForThisRun.map((n) => (
                  <li key={n} className="tone-accent rounded-[var(--radius)] border border-l-4 px-3 py-2 text-sm">
                    {n}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {failedCalls.length > 0 && (
            <div>
              <SectionHeading>Failed Gemini calls</SectionHeading>
              <ul className="grid gap-2">
                {failedCalls.map((c, i) => (
                  <li key={i} className="rounded-[var(--radius)] border border-line bg-surface px-3 py-2 text-sm">
                    <span className="mono text-ink">{c.node}</span> <span className="mono text-ink-3">{c.model}</span>
                    <div className="mono mt-0.5 text-xs text-[var(--fail)]">{(c.error ?? "").slice(0, 160)}</div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
