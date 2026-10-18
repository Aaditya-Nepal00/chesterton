import type { ReactNode } from "react";
import { Link } from "react-router";
import { recordFileUrl } from "../lib/github";
import { prUrl } from "../lib/format";
import { verdictStatus } from "../lib/status";
import type { DecisionRecordDoc, GuardDoc } from "../lib/types";
import { Card, Ext, InlineMd, Pill } from "./ui";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="border-t border-line pt-4">
      <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-[0.08em] text-ink-3">{label}</h3>
      <div className="text-ink">{children}</div>
    </section>
  );
}

export function DecisionMeta({ d }: { d: DecisionRecordDoc }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-ink-3">
      <Pill info={{ label: "Recorded", tone: "ok" }} size="xs" />
      {d.owner && <span>Owner <span className="text-ink-2">@{d.owner}</span></span>}
      <span className="mono">{d.recorded}</span>
      <span className="mono">{d.repo}</span>
    </div>
  );
}

/** The full record: Decision / Why / Safe to change when / Evidence (+ later guards). */
export function DecisionRecordView({
  d,
  guards,
  compact = false,
}: {
  d: DecisionRecordDoc;
  guards?: GuardDoc[];
  compact?: boolean;
}) {
  const sourcePr = d.source_pr_url || prUrl(d.repo, d.source_pr);
  return (
    <Card as="article" className="p-5 sm:p-6">
      <div className="mono text-sm text-ink-3">decisions/{d.file_path?.split("/").pop() ?? `${d.id}.md`}</div>
      <h2 className="mt-1 font-display text-2xl font-medium tracking-[-0.01em] text-ink">
        <span className="mono mr-2 text-xl text-ink-3">{d.id}</span>
        {d.title}
      </h2>
      <div className="mt-2">
        <DecisionMeta d={d} />
      </div>

      <div className="mt-5 grid gap-4">
        <Field label="Decision">
          <p>{d.decision}</p>
        </Field>
        <Field label="Why">
          <blockquote className="border-l-2 border-accent pl-4 font-display text-lg leading-7 text-ink">
            “{d.reason_quote}”
          </blockquote>
          <p className="mt-2 text-sm text-ink-3">
            — @{d.owner ?? "author"},{" "}
            <Ext href={d.answer_url}>answer on #{d.source_pr}</Ext>
          </p>
        </Field>
        {!!d.alternatives?.length && (
          <Field label="Alternatives considered">
            <ul className="list-disc pl-5">
              {d.alternatives.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </Field>
        )}
        <Field label="Safe to change when">
          <p>{d.safe_to_change_when || <span className="text-ink-3">Not stated. Ask the owner before changing it.</span>}</p>
        </Field>
        {!compact && (
          <Field label="Covers">
            <div className="flex flex-wrap gap-1.5">
              {d.paths.map((p) => (
                <span key={p} className="mono rounded border border-line bg-surface-2 px-1.5 py-px text-xs text-ink-2">{p}</span>
              ))}
              {d.symbols.map((s) => (
                <span key={s} className="mono rounded border border-accent/40 bg-accent-soft px-1.5 py-px text-xs text-accent">{s}</span>
              ))}
            </div>
          </Field>
        )}
        <Field label="Evidence">
          <ul className="grid gap-1 text-sm">
            <li>
              Source PR: <Ext href={sourcePr}>#{d.source_pr} <InlineMd text={d.source_pr_title ?? ""} /></Ext>{" "}
              <Link to={`/pr/${d.repo}/${d.source_pr}`} className="ml-1 text-ink-3">analysis</Link>
            </li>
            {d.evidence_url && (
              <li>
                Change:{" "}
                <Ext href={d.evidence_url} className="mono">
                  {d.evidence_path}
                  {d.evidence_line ? `:${d.evidence_line}` : ""}
                </Ext>
              </li>
            )}
            {d.answer_url && (
              <li>
                Answer: <Ext href={d.answer_url}>comment by @{d.owner}</Ext>
              </li>
            )}
            {d.record_pr_url && (
              <li>
                Record PR: <Ext href={d.record_pr_url}>#{d.record_pr_number}</Ext> ·{" "}
                <Ext href={recordFileUrl(d.repo, d.file_path)} className="mono">{d.file_path}</Ext>
              </li>
            )}
            <li className="text-ink-3">Question asked: <InlineMd text={d.question} /></li>
          </ul>
        </Field>
        {guards && (
          <Field label="Later PRs that touched it">
            {guards.length === 0 ? (
              <p className="text-sm text-ink-3">
                No PR has touched this decision yet. When one does, the guard comment shows up here.
              </p>
            ) : (
              <ul className="grid gap-2">
                {guards.map((g) => {
                  const j = g.judgements.find((x) => x.decision_number === d.number);
                  return (
                    <li key={g.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                      <Ext href={prUrl(g.repo, g.pr)} className="mono">{g.repo}#{g.pr}</Ext>
                      {j && <Pill info={verdictStatus(j.verdict)} size="xs" />}
                      {j?.note && <span className="text-ink-2">{j.note}</span>}
                      {g.comment_url && <Ext href={g.comment_url}>guard comment</Ext>}
                      <Link to={`/runs/${g.run_id}`} className="text-ink-3">trace</Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </Field>
        )}
      </div>
    </Card>
  );
}
