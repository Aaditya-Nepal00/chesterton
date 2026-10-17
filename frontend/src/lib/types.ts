/* Shapes of the Firestore documents the dashboard reads, as written by the backend
   (backend/app/store/firestore.py + backend/app/models/*). Fields that older runs
   don't have are optional. Timestamps are Firestore Timestamps unless noted. */
import type { Timestamp } from "firebase/firestore";

export type TimeLike = Timestamp | string | null | undefined;

export type RunKind = "analyze" | "record" | "guard";

export interface LlmCallRow {
  node: string;
  path: string;
  model: string | null;
  latency_ms: number;
  ok: boolean;
  error: string | null;
}

/** runs/{run_id}  (run_id = "YYYYMMDDTHHMMSSZ-xxxxxx", so it sorts by time) */
export interface RunDoc {
  id: string;
  run_id: string;
  kind?: RunKind; // missing on Build-3 CLI runs (those are analyze runs)
  repo: string;
  pr?: number; // Build 4+
  number?: number; // Build 3
  status: string; // running | error | questions_posted | gaps_found | no_gaps | skipped | matched | no_matches | recorded | nothing_recorded | done
  summary?: string;
  trigger?: string;
  actions_run_url?: string | null;
  started_at?: TimeLike; // Timestamp (Build 4+) or ISO string (Build 3)
  finished_at?: TimeLike;
  llm_calls?: number;
  llm_failed_calls?: number;
  llm_calls_detail?: LlmCallRow[];
  node_durations_ms?: Record<string, number>;
  node_errors?: TraceEventDoc[];
  models?: { main?: string | null; screen?: string | null };
  models_used?: string[]; // Build 7a+: models that answered
  events_streamed?: number;
  previous_status?: string;
  model_chains?: { main?: string[]; screen?: string[] };
  analysis_doc?: string;
}

/** runs/{run_id}/events/{seq:04d} */
export interface TraceEventDoc {
  id?: string;
  run_id: string;
  seq: number;
  node: string;
  path: string;
  status: "started" | "finished" | "error";
  summary: string;
  ts: string; // ISO, client clock
  duration_ms?: number | null;
  model?: string | null; // newer runs only
  written_at?: TimeLike;
}

export interface Decision {
  id: string; // D1..
  summary: string;
  category: string;
  diff_evidence: string[];
  why_it_matters: string;
  confidence: number;
}

export type RationaleStatus = "found" | "partial" | "missing" | "self_evident";

export interface RationaleCheck {
  decision_id: string;
  status: RationaleStatus;
  reason_quote: string | null;
  evidence_ids: string[];
  note: string;
}

export interface RenderedQuestion {
  decision_id: string;
  text: string;
  evidence_id: string;
  snippet: string;
  path?: string | null;
  line?: number | null;
  url?: string | null;
}

/** pr_analyses/{owner}__{repo}__{number}  (latest analysis per PR) */
export interface AnalysisDoc {
  id: string;
  repo: string;
  number: number;
  title: string;
  url: string;
  author?: string | null;
  run_id: string;
  status: "skipped" | "no_gaps" | "gaps_found";
  skip_reason?: string | null;
  signals?: { score: number; dependency_files: string[]; config_files: string[]; migration_files: string[] } | null;
  screen?: { consequential: boolean; reasons: string[] } | null;
  decisions: Decision[];
  rationale: RationaleCheck[];
  verifier_notes: string[];
  questions: RenderedQuestion[];
  questions_skipped?: boolean; // backfill measurement: the questions step did not run
  comment_markdown?: string | null;
  models?: Record<string, string | null>;
  evidence_urls?: Record<string, string>; // evidence id -> html_url (Build 7a+)
  created_at?: TimeLike;
  updated_at?: TimeLike;
}

export interface QuestionItem {
  qid: string;
  decision_id: string;
  text: string;
  evidence_id: string;
  paths: string[];
  symbols: string[];
  status: "open" | "answered";
  decision_summary?: string;
  category?: string;
  path?: string | null;
  line?: number | null;
  evidence_url?: string | null;
  snippet?: string;
  decision_number?: number | null;
  record_pr_url?: string | null;
}

/** questions/{owner}__{repo}__{pr} */
export interface QuestionSetDoc {
  id: string;
  repo: string;
  pr: number;
  pr_url: string;
  pr_title?: string;
  author?: string | null;
  comment_id?: number | null;
  comment_url?: string | null;
  run_id: string;
  questions: QuestionItem[];
  processed_comment_ids?: number[];
  n_questions?: number;
  n_answered?: number;
  created_at?: TimeLike;
  updated_at?: TimeLike;
}

/** decisions/{owner}__{repo}__{NNNN} */
export interface DecisionRecordDoc {
  docId: string;
  number: number;
  id: string; // "0001"
  slug: string;
  file_path: string;
  title: string;
  repo: string;
  owner: string | null;
  paths: string[];
  symbols: string[];
  source_pr: number;
  source_pr_url: string;
  source_pr_title?: string;
  answer_url?: string | null;
  recorded: string; // YYYY-MM-DD
  question: string;
  decision: string;
  reason: string;
  reason_quote: string;
  alternatives?: string[];
  constraints?: string[];
  safe_to_change_when?: string;
  evidence_url?: string | null;
  evidence_path?: string | null;
  evidence_line?: number | null;
  record_pr_url?: string | null;
  record_pr_number?: number | null;
  reply_comment_url?: string | null;
  status: "recorded";
  created_at?: TimeLike;
}

export interface GuardMatch {
  decision_number: number;
  path: string;
  line: number | null;
  snippet: string;
  hunk_text: string;
  symbol: string | null;
}

/** guards/{owner}__{repo}__{pr} */
export interface GuardDoc {
  id: string;
  repo: string;
  pr: number;
  run_id: string;
  matches: GuardMatch[];
  judgements: { decision_id: string; verdict: string; note: string; decision_number: number }[];
  items: { decision: string; verdict: string; link: string }[];
  comment_action: string;
  comment_url?: string | null;
  matched_count?: number;
  updated_at?: TimeLike;
}

export type DecisionCounts = Record<RationaleStatus, number>;

/** backfills/{owner}__{repo}  (why-gap measurement over a repo's last N merged PRs) */
export interface BackfillDoc {
  id: string;
  repo: string;
  status: "running" | "complete" | "stopped_budget" | "paused_quota" | "incomplete";
  epoch?: string;
  sample_size: number;
  sample_prs?: number[];
  sample_listed_at?: string | null;
  considered: number; // newest-first PRs with a verdict; the headline counts only these
  skipped_deterministic: number;
  skipped_by_kind?: Record<string, number>;
  screened: number;
  consequential: number;
  analyzed: number;
  pending?: number;
  errors?: number;
  prs_with_gaps?: number;
  decisions: DecisionCounts;
  decisions_total?: number;
  why_gap_rate: number | null;
  calls_used: number;
  models_used: string[];
  method: string;
  budget_calls?: number;
  started_at?: string | null;
  finished_at?: string | null;
  updated_at?: TimeLike;
}

export interface BackfillDecision {
  id: string;
  summary: string;
  category: string;
  status: RationaleStatus | "unchecked";
  reason_quote?: string | null;
}

/** backfills/{id}/items/{pr} */
export interface BackfillItemDoc {
  id: string;
  pr: number;
  title?: string;
  url?: string;
  author?: string | null;
  merged_at?: string | null;
  head_sha?: string | null;
  epoch?: string;
  status: "skipped_deterministic" | "not_consequential" | "analyzed" | "pending" | "error";
  skip_kind?: string | null;
  skip_reason?: string | null;
  screen?: { consequential: boolean; reasons: string[] } | null;
  decisions: BackfillDecision[];
  counts?: DecisionCounts;
  calls?: number;
  run_id?: string | null;
  analysis_id?: string | null;
  error?: string | null;
}
