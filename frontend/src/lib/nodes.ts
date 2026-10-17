/* Human names for workflow nodes, and which ones are Gemini calls. */

export const NODE_NAMES: Record<string, string> = {
  // analyze_pr
  load_context: "Load PR context",
  compute_signals: "Score signals",
  build_evidence: "Build evidence index",
  join_context: "Join context",
  screen_input: "Prepare screen input",
  screen: "Screen PR",
  gate: "Gate: anything consequential?",
  extract_input: "Prepare decision input",
  extract: "Find decisions",
  plan_rationale: "Plan rationale checks",
  rationale: "Look for a written reason",
  verify: "Verify against evidence",
  questions_input: "Prepare questions input",
  questions: "Write questions",
  verify_questions: "Verify questions",
  render_comment: "Render comment",
  finish_skipped: "Finish (skipped)",
  persist: "Save to Firestore",
  post_questions: "Post questions on the PR",
  // record_answer
  load_question_set: "Load open questions",
  answer_input: "Prepare answer input",
  parse_answer: "Read the author's answer",
  verify_answer: "Verify quote against reply",
  allocate_numbers: "Allocate record number",
  render_records: "Render decision record",
  open_record_pr: "Open record PR",
  reply_on_source_pr: "Reply on the source PR",
  finish_none: "Finish (nothing to do)",
  // guard_pr
  load_pr_diff: "Load PR diff",
  load_decisions: "Load recorded decisions",
  match: "Match paths and symbols",
  judge: "Judge each match",
  render_guard_comment: "Render guard comment",
  upsert_comment: "Post or update comment",
};

/** Nodes that call Gemini (agents, or parallel workers of an agent). */
export const GEMINI_NODES = new Set(["screen", "extract", "rationale", "questions", "parse_answer", "judge"]);

/** Nodes whose job is checking the model's work deterministically. */
export const VERIFIER_NODES = new Set(["verify", "verify_questions", "verify_answer", "match"]);

export function nodeName(node: string): string {
  return NODE_NAMES[node] ?? node.replace(/_/g, " ");
}

/** "analyze_pr@1/rationale@1/rationale@2" -> ["analyze_pr", "rationale", "rationale"] */
export function pathParts(path: string): string[] {
  return path.split("/").map((p) => p.split("@")[0]);
}

/** Depth below the workflow root: 1 for top-level nodes, 2 for parallel workers. */
export function pathDepth(path: string): number {
  return Math.max(1, path.split("/").length - 1);
}

export function workerIndex(path: string): string | null {
  const last = path.split("/").pop() ?? "";
  const m = last.match(/@(\w+)$/);
  return pathDepth(path) > 1 && m ? m[1] : null;
}

export function inferKind(run: { kind?: string; analysis_doc?: string; run_id: string }): string {
  if (run.kind) return run.kind;
  return "analyze";
}
