import { useMemo } from "react";
import { Link, useParams } from "react-router";
import { EvidenceLink } from "../components/EvidenceLink";
import { Card, EmptyState, ErrorNote, Ext, InlineMd, PageTitle, Pill, SectionHeading, Skeleton } from "../components/ui";
import { analysisRef, asAnalysis, asDecision, asQuestionSet, decisionsForRepoQuery, questionSetRef } from "../lib/data";
import { prDocId, prUrl } from "../lib/format";
import { useLiveDoc, useLiveQuery } from "../lib/hooks";
import { questionStatus, rationaleStatus, runStatus } from "../lib/status";

export function PrAnalysis() {
  const { owner = "", repo: name = "", number = "" } = useParams();
  const repo = `${owner}/${name}`;
  const id = prDocId(repo, number);
  const analysis = useLiveDoc(() => analysisRef(id), asAnalysis, [id]);
  const qset = useLiveDoc(() => questionSetRef(id), asQuestionSet, [id]);
  const records = useLiveQuery(() => decisionsForRepoQuery(repo), asDecision, [repo]);
  const a = analysis.data;
  const myRecords = useMemo(
    () => records.data.filter((d) => String(d.source_pr) === String(number)).sort((x, y) => x.number - y.number),
    [records.data, number],
  );

  if (analysis.loading) {
    return (
      <div className="grid gap-4">
        <Skeleton className="h-9 w-1/2" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (analysis.error) return <ErrorNote error={analysis.error} />;
  if (!a) {
    return (
      <>
        <PageTitle eyebrow={<span className="mono">{repo}#{number}</span>}>No analysis for this PR</PageTitle>
        <EmptyState title="Chesterton hasn't analyzed this pull request." action={<Link to="/">Recent activity</Link>}>
          Analyses are written when a PR is merged in a repo that has the Chesterton workflow, or when someone runs it by
          hand with <span className="mono">workflow_dispatch</span>.
        </EmptyState>
      </>
    );
  }

  const url = a.url || prUrl(repo, number);
  const rationale = new Map(a.rationale.map((r) => [r.decision_id, r]));
  const qByDecision = new Map((qset.data?.questions ?? []).map((q) => [q.decision_id, q]));

  return (
    <div className="grid gap-10">
      <div>
        <PageTitle
          eyebrow={
            <span className="flex flex-wrap items-center gap-x-3">
              <Ext href={url} className="mono">{repo}#{a.number}</Ext>
              {a.author && <span>by @{a.author}</span>}
              <Link to={`/runs/${a.run_id}`}>trace of this analysis</Link>
            </span>
          }
          aside={<Pill info={runStatus(a.status)} />}
        >
          {a.title}
        </PageTitle>
        <div className="-mt-4 flex flex-wrap gap-x-6 gap-y-1 text-sm text-ink-2">
          {a.skip_reason && <span>Skipped: {a.skip_reason}</span>}
          {a.signals && (
            <span>
              Signal score <span className="mono">{a.signals.score}</span>/10
            </span>
          )}
          {a.screen && <span>Screen: {a.screen.reasons.join("; ")}</span>}
        </div>
      </div>

      <section aria-labelledby="decisions">
        <SectionHeading id="decisions" aside={`${a.decisions.length} found`}>Decisions in this PR</SectionHeading>
        {a.decisions.length === 0 ? (
          <EmptyState title="No decisions were extracted.">
            {a.status === "skipped"
              ? "This PR was skipped before extraction (see the reason above)."
              : "The model found nothing in this diff that a future maintainer might undo without knowing why."}
          </EmptyState>
        ) : (
          <div className="grid gap-4">
            {a.decisions.map((d) => {
              const r = rationale.get(d.id);
              const q = qByDecision.get(d.id);
              return (
                <Card as="article" key={d.id} className="p-4 sm:p-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="mono text-xs text-ink-3">
                        {d.id} · {d.category.replace(/_/g, " ")} · confidence {Math.round(d.confidence * 100)}%
                      </div>
                      <p className="mt-1 text-ink">{d.summary}</p>
                    </div>
                    {r ? <Pill info={rationaleStatus(r.status)} /> : <Pill info={{ label: "Not checked", tone: "muted" }} />}
                  </div>
                  {r?.reason_quote && (
                    <blockquote className="mt-3 border-l-2 border-[var(--ok-line)] pl-3 text-ink-2">
                      “{r.reason_quote}”
                      <div className="mt-1 flex flex-wrap gap-2">
                        {r.evidence_ids.map((e) => (
                          <EvidenceLink key={e} id={e} prUrl={url} repo={repo} url={a.evidence_urls?.[e]} />
                        ))}
                      </div>
                    </blockquote>
                  )}
                  {r?.note && <p className="mt-2 text-sm text-ink-3">{r.note}</p>}
                  <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-3 text-xs text-ink-3">
                    <span>Diff:</span>
                    {d.diff_evidence.map((e) => (
                      <EvidenceLink key={e} id={e} prUrl={url} repo={repo} url={a.evidence_urls?.[e]} />
                    ))}
                    {q && (
                      <span className="ml-auto flex items-center gap-2">
                        <span>Question {q.qid}</span>
                        <Pill info={questionStatus(q.status)} size="xs" />
                      </span>
                    )}
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </section>

      <div className="grid gap-10 lg:grid-cols-2">
        <section aria-labelledby="notes">
          <SectionHeading id="notes">Verifier notes</SectionHeading>
          {a.verifier_notes.length === 0 ? (
            <EmptyState title="The verifier didn't change anything.">
              Every cited evidence id existed and every quoted reason matched the text it came from.
            </EmptyState>
          ) : (
            <ul className="grid gap-2">
              {a.verifier_notes.map((n) => (
                <li key={n} className="tone-accent rounded-[var(--radius)] border border-l-4 px-3 py-2 text-sm">{n}</li>
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby="questions">
          <SectionHeading
            id="questions"
            aside={qset.data?.comment_url ? <Ext href={qset.data.comment_url}>comment on GitHub</Ext> : undefined}
          >
            Questions
          </SectionHeading>
          {qset.loading ? (
            <Skeleton className="h-24 w-full" />
          ) : qset.data?.questions.length ? (
            <ol className="grid gap-3">
              {qset.data.questions.map((q) => (
                <li key={q.qid} className="rounded-[var(--radius)] border border-line bg-surface px-3 py-2.5">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm text-ink">
                      <span className="mono mr-1.5 text-ink-3">{q.qid}</span>
                      <InlineMd text={q.text} />
                    </p>
                    <Pill info={questionStatus(q.status)} size="xs" />
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-ink-3">
                    {q.evidence_url && <Ext href={q.evidence_url} className="mono">{q.path}:{q.line}</Ext>}
                    {q.record_pr_url && <Ext href={q.record_pr_url}>record PR</Ext>}
                  </div>
                </li>
              ))}
            </ol>
          ) : a.questions.length ? (
            <EmptyState title="Questions were rendered but not posted.">
              This analysis ran without posting (CLI or dry run), so there's no open question set for it.
            </EmptyState>
          ) : (
            <EmptyState title="No questions for this PR.">
              Chesterton only asks when a decision has no written reason. This one didn't need any.
            </EmptyState>
          )}
        </section>
      </div>

      <section aria-labelledby="records">
        <SectionHeading id="records">Decision records from this PR</SectionHeading>
        {myRecords.length === 0 ? (
          <EmptyState title="None yet.">
            When the author answers a question above, the answer becomes a record in{" "}
            <span className="mono">decisions/</span> and shows up here.
          </EmptyState>
        ) : (
          <ul className="grid gap-2">
            {myRecords.map((d) => (
              <li key={d.docId}>
                <Link to={`/decisions/${d.docId}`} className="font-medium">
                  <span className="mono mr-2">{d.id}</span>
                  {d.title}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
