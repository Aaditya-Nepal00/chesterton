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
  done: { label: "Done", tone: "slate" },
};

export function runStatus(status: string | undefined): StatusInfo {
  return RUN[status ?? ""] ?? { label: (status ?? "unknown").replace(/_/g, " "), tone: "slate" };
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
