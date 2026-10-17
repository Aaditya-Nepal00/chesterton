/* Backfill wording, shared by the Overview KPI and the backfill page. Mirrors
   backend/app/backfill.py headline(): numbers describe this sample only. */
import type { BackfillDoc, DecisionCounts } from "./types";

export function gapCounts(d: DecisionCounts) {
  const gaps = d.missing + d.partial;
  const checked = d.found + d.partial + d.missing; // self-evident is left out of the rate
  return { gaps, checked };
}

export function ratePercent(b: Pick<BackfillDoc, "why_gap_rate">): string {
  return b.why_gap_rate == null ? "—" : `${Math.round(b.why_gap_rate * 100)}%`;
}

/** "In the last 22 merged PRs of pallets/flask, 9 of 14 consequential decisions had no written reason …" */
export function headline(b: BackfillDoc): string | null {
  const { gaps, checked } = gapCounts(b.decisions);
  if (!checked) return null;
  const partial = b.decisions.partial ? " (or only a partial one)" : "";
  return (
    `In the last ${b.considered} merged PRs of ${b.repo}, ${gaps} of ${checked} consequential decisions ` +
    `had no written reason${partial} in the PR, its reviews, commits or linked issues.`
  );
}

export function backfillPath(repo: string): string {
  return `/backfill/${repo}`;
}
