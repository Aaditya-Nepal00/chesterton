/* Status vocabulary: every status has a text label and a tone. Color never stands alone. */

export type Tone = "ok" | "missing" | "partial" | "slate" | "muted" | "fail" | "guard" | "accent";

export interface StatusInfo {
  label: string;
  tone: Tone;
  hint?: string;
}

const RUN: Record<string, StatusInfo> = {
  running: { label: "Running", tone: "accent" },
  error: { label: "Failed", tone: "fail" },
  quota_paused: {
    label: "Paused — daily AI quota",
    tone: "missing",
    hint: "Every model in the chain hit its daily free-tier quota; it resumes after 00:00 UTC",
  },
  questions_posted: { label: "Asked why", tone: "missing", hint: "Questions posted on the PR" },
  gaps_found: { label: "Missing why", tone: "missing", hint: "Decisions without a written reason" },
  no_gaps: { label: "Reasons found", tone: "ok", hint: "Every decision has a written reason" },
  skipped: { label: "Skipped", tone: "muted" },
  recorded: { label: "Recorded", tone: "ok", hint: "Decision record PR opened" },
  nothing_recorded: { label: "Nothing recorded", tone: "muted" },
  matched: { label: "Guard posted", tone: "guard", hint: "PR touches a recorded decision" },
  no_matches: { label: "No decisions touched", tone: "muted" },
  dry_run: { label: "Dry run", tone: "slate" },
  budget_stopped: {
    label: "Stopped — call budget",
    tone: "slate",
    hint: "A backfill reached its own cap on Gemini calls; it continues with --resume",
  },
  done: { label: "Done", tone: "slate" },
};

export function runStatus(status: string | undefined): StatusInfo {
  return RUN[status ?? ""] ?? { label: (status ?? "unknown").replace(/_/g, " "), tone: "slate" };
}

/** Status for a run doc: a backfill run that stopped because Gemini was briefly unavailable
    reads like the Backfill page ("Not analyzed"), not as a product failure. */
export function runStatusOf(run: { status: string; trigger?: string; summary?: string }): StatusInfo {
  if (run.status === "error" && run.trigger === "backfill") {
    const cause = transientModelError(run.summary);
    if (cause) return { label: "Not analyzed", tone: "slate", hint: `${cause}. A backfill --resume retries this PR.` };
  }
  return runStatus(run.status);
}

const RATIONALE: Record<string, StatusInfo> = {
  found: { label: "Reason found", tone: "ok" },
  partial: { label: "Partial reason", tone: "partial" },
  missing: { label: "Missing why", tone: "missing" },
  self_evident: { label: "Self-evident", tone: "slate" },
};

export function rationaleStatus(status: string | undefined): StatusInfo {
  return RATIONALE[status ?? ""] ?? { label: status ?? "unchecked", tone: "muted" };
}

export function questionStatus(status: string | undefined): StatusInfo {
  return status === "answered"
    ? { label: "Answered", tone: "ok" }
    : { label: "Open", tone: "missing" };
}

export function verdictStatus(verdict: string): StatusInfo {
  if (verdict === "changes_decision") return { label: "Changes the decision", tone: "guard" };
  if (verdict === "touches_nearby") return { label: "Edits nearby", tone: "slate" };
  return { label: "Unrelated", tone: "muted" };
}

export const KIND_LABEL: Record<string, string> = {
  analyze: "Analyze",
  record: "Record",
  guard: "Guard",
};

const BACKFILL_ITEM: Record<string, StatusInfo> = {
  skipped_deterministic: { label: "Skipped by rule", tone: "muted", hint: "Bot, release, docs-only or tests-only: no model call" },
  not_consequential: { label: "Not consequential", tone: "muted", hint: "The screen found no decision worth a written reason" },
  analyzed: { label: "Analyzed", tone: "slate" },
  pending: { label: "Waiting for budget", tone: "accent", hint: "Screened consequential; analysis continues with --resume" },
  error: { label: "Failed", tone: "fail" },
};

/** Plain-language cause for a backfill item error, when it was Gemini being briefly unavailable
    (overload, rate limit, timeout) rather than a bug. Null for any other error. */
export function transientModelError(error: string | null | undefined): string | null {
  if (!error) return null;
  if (/\b503\b|UNAVAILABLE|high demand|overloaded/i.test(error)) return "Gemini returned 503: the model was under high demand";
  if (/\b429\b|RESOURCE_EXHAUSTED/i.test(error)) return "Gemini returned 429: rate limit reached";
  if (/\b504\b|DEADLINE_EXCEEDED|timed? ?out/i.test(error)) return "Gemini timed out";
  return null;
}

export function backfillItemStatus(status: string | undefined, error?: string | null): StatusInfo {
  if (status === "error") {
    const cause = transientModelError(error);
    if (cause) return { label: "Not analyzed", tone: "slate", hint: `${cause}. A --resume run retries it.` };
  }
  return BACKFILL_ITEM[status ?? ""] ?? { label: (status ?? "unknown").replace(/_/g, " "), tone: "slate" };
}

const BACKFILL: Record<string, StatusInfo> = {
  running: { label: "Running", tone: "accent" },
  complete: { label: "Complete", tone: "ok" },
  stopped_budget: { label: "Stopped — call budget", tone: "slate", hint: "Continues with --resume" },
  paused_quota: { label: "Paused — daily AI quota", tone: "missing", hint: "Continues with --resume after 00:00 UTC" },
  incomplete: { label: "Incomplete", tone: "fail", hint: "Some PRs failed; --resume retries them" },
};

export function backfillStatus(status: string | undefined): StatusInfo {
  return BACKFILL[status ?? ""] ?? { label: status ?? "unknown", tone: "slate" };
}
