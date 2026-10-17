/* Query builders and mappers. Only single-field orderBy queries (no composite indexes). */
import {
  collection,
  doc,
  limit,
  orderBy,
  query,
  where,
  type Query,
} from "firebase/firestore";
import { db } from "./firebase";
import { toDate } from "./format";
import type {
  AnalysisDoc,
  BackfillDoc,
  BackfillItemDoc,
  DecisionRecordDoc,
  GuardDoc,
  QuestionSetDoc,
  RunDoc,
  TraceEventDoc,
} from "./types";

type Raw = Record<string, unknown>;

// Firestore data is untyped at the boundary; arrays get defaults so the UI never
// crashes on an older document that lacks them.
export const asRun = (id: string, d: Raw) => ({ id, ...d }) as unknown as RunDoc;
export const asEvent = (id: string, d: Raw) => ({ id, ...d }) as unknown as TraceEventDoc;
export const asAnalysis = (id: string, d: Raw) =>
  ({ decisions: [], rationale: [], verifier_notes: [], questions: [], ...d, id }) as unknown as AnalysisDoc;
export const asQuestionSet = (id: string, d: Raw) =>
  ({ questions: [], ...d, id }) as unknown as QuestionSetDoc;
export const asDecision = (id: string, d: Raw) =>
  ({ paths: [], symbols: [], ...d, docId: id }) as unknown as DecisionRecordDoc;
export const asGuard = (id: string, d: Raw) =>
  ({ matches: [], judgements: [], items: [], ...d, id }) as unknown as GuardDoc;
const NO_COUNTS = { found: 0, partial: 0, missing: 0, self_evident: 0 };
export const asBackfill = (id: string, d: Raw) =>
  ({ models_used: [], why_gap_rate: null, method: "", ...d, decisions: { ...NO_COUNTS, ...(d.decisions as object) }, id }) as unknown as BackfillDoc;
export const asBackfillItem = (id: string, d: Raw) =>
  ({ decisions: [], ...d, id }) as unknown as BackfillItemDoc;

const col = (name: string) => (db ? collection(db, name) : null);

// run_id is "YYYYMMDDTHHMMSSZ-xxxxxx" on every run, so ordering by it is chronological
// (started_at is a string on older runs and a Timestamp on newer ones, so it can't be used).
export const latestRunsQuery = (n: number): Query | null => {
  const c = col("runs");
  return c ? query(c, orderBy("run_id", "desc"), limit(n)) : null;
};
export const runRef = (id: string) => (db ? doc(db, "runs", id) : null);
export const eventsQuery = (id: string): Query | null =>
  db ? query(collection(db, "runs", id, "events"), orderBy("seq")) : null;

export const analysisRef = (id: string) => (db ? doc(db, "pr_analyses", id) : null);
export const questionSetRef = (id: string) => (db ? doc(db, "questions", id) : null);
export const decisionRef = (docId: string) => (db ? doc(db, "decisions", docId) : null);

export const allQuestionSetsQuery = (): Query | null => col("questions");
export const decisionsQuery = (): Query | null => {
  const c = col("decisions");
  return c ? query(c, orderBy("created_at", "desc")) : null;
};
export const decisionsForRepoQuery = (repo: string): Query | null => {
  const c = col("decisions");
  return c ? query(c, where("repo", "==", repo)) : null;
};
export const guardsForRepoQuery = (repo: string): Query | null => {
  const c = col("guards");
  return c ? query(c, where("repo", "==", repo)) : null;
};
export const latestOf = (name: string, field = "updated_at"): Query | null => {
  const c = col(name);
  return c ? query(c, orderBy(field, "desc"), limit(1)) : null;
};

// --- counts (count() aggregations) ---
export const countAll = (name: string): Query | null => col(name);
/** Guards that showed a PR at least one recorded decision. */
export const guardWarningsQuery = (): Query | null => {
  const c = col("guards");
  return c ? query(c, where("matched_count", ">", 0)) : null;
};
/** The most recently updated backfill (the Overview KPI shows its rate). */
export const backfillsQuery = (): Query | null => {
  const c = col("backfills");
  return c ? query(c, orderBy("updated_at", "desc"), limit(1)) : null;
};
/** backfills/{owner}__{repo}: one doc per repo (no PR number, unlike prDocId). */
export const repoDocId = (repo: string) => repo.replace("/", "__");
export const backfillRef = (id: string) => (db ? doc(db, "backfills", id) : null);
export const backfillItemsQuery = (id: string): Query | null =>
  db ? query(collection(db, "backfills", id, "items"), orderBy("pr", "desc")) : null;

// --- derived run facts ---
export function runPr(run: RunDoc): number | undefined {
  return run.pr ?? run.number;
}

export function runDurationMs(run: RunDoc): number | null {
  const a = toDate(run.started_at);
  const b = toDate(run.finished_at);
  if (!a || !b) return null;
  return Math.max(0, b.getTime() - a.getTime());
}

/** Models that actually answered, from the per-call rows (ok calls only). */
export function modelsAnswered(run: RunDoc): string[] {
  if (run.models_used?.length) return run.models_used;
  const seen = new Set<string>();
  for (const c of run.llm_calls_detail ?? []) if (c.ok && c.model) seen.add(c.model);
  return [...seen];
}

/** Model that answered for a node path (ok calls), if any. */
export function modelForPath(run: RunDoc | null, path: string): string | null {
  const rows = (run?.llm_calls_detail ?? []).filter((c) => c.path === path && c.ok && c.model);
  return rows.length ? rows[rows.length - 1].model : null;
}

export function failedCallsForPath(run: RunDoc | null, path: string) {
  return (run?.llm_calls_detail ?? []).filter((c) => c.path === path && !c.ok);
}
