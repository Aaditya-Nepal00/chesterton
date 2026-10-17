"""Backfill: measure the why-gap over a repo's last N merged PRs, within a Gemini call budget.

Per PR, newest merge first:
  1. deterministic prefilter, 0 calls: bot author, release/version bump, docs-only, tests-only
  2. analyze_pr with skip_questions: screen (1 call); if consequential, extract (1) and
     up to 3 rationale checks. The questions agent never runs.

Every Gemini request is counted by a process-wide CallBudget (model_chain), so the run
can never spend more than --budget-calls. Each PR's result is written to
backfills/{owner}__{repo}/items/{pr} as soon as it is known, and the summary doc is
recomputed from the items each time, so a run stopped by the budget or the daily quota
continues with --resume (PRs already done at the same head SHA cost nothing; a PR whose
screen result was saved resumes without re-screening).

The headline numbers only count the contiguous newest-first prefix of PRs that have a
verdict, so "the last K merged PRs" is literally true even when a run stopped early.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.agents import model_chain as mc
from app.analysis.signals import compute_signals_for
from app.github.fetch import client_for, fetch_pr_context, fetch_pr_files, list_merged_prs
from app.models.analysis import Signals
from app.models.pr import ChangedFile
from app.store import firestore as store
from app.workflows import analyze_pr
from app.workflows.runner import RunOutcome

STATUSES = ("found", "partial", "missing", "self_evident")
TERMINAL = {"skipped_deterministic", "not_consequential", "analyzed"}
PREFILTER_KINDS = ("bot", "release", "docs-only", "tests-only")
MAX_RATIONALE = analyze_pr.MAX_RATIONALE_DECISIONS

# Dry-run cost model (no calls are made to calibrate it; stated in the dry-run output).
P_CONSEQUENTIAL_FLAGGED = 0.9  # signal score >= 2: deps, config, migration, tuned constants
P_CONSEQUENTIAL_OTHER = 0.35  # source changes without those signals (API, behaviour)
FLAG_SCORE = 2
SAFETY = 0.9  # suggested --limit spends at most 90% of the budget in expectation


# --- prefilter --------------------------------------------------------------


def prefilter(pr: dict, files: list[ChangedFile]) -> tuple[str | None, str | None]:
    """(kind, reason) when a PR is skipped without any model call, else (None, None)."""
    if pr.get("author_is_bot"):
        return "bot", f"bot author @{pr.get('author')}"
    s = compute_signals_for(pr.get("title") or "", False, files)
    if s.is_release_or_bot:
        return "release", "release or version bump"
    if s.is_docs_only:
        return "docs-only", "docs-only PR"
    if s.is_tests_only:
        return "tests-only", "tests-only PR"
    return None, None


# --- summary ----------------------------------------------------------------


def sample_row(pr: dict) -> dict:
    keys = ("number", "title", "url", "author", "author_is_bot", "merged_at", "head_sha")
    return {k: pr.get(k) for k in keys}


def has_verdict(item: dict | None) -> bool:
    """The PR was either prefiltered or screened (we know whether it is consequential)."""
    if not item:
        return False
    return item.get("status") in TERMINAL or item.get("screen") is not None


def _same_head(item: dict | None, pr: dict) -> bool:
    return bool(item) and item.get("head_sha") == pr.get("head_sha")


def why_gap_rate(counts: dict[str, int]) -> float | None:
    den = counts["found"] + counts["partial"] + counts["missing"]
    return (counts["missing"] + counts["partial"]) / den if den else None


def method_text(repo: str, sample_size: int, listed_at: str | None) -> str:
    when = f" as listed by GitHub on {listed_at[:10]}" if listed_at else ""
    return (
        f"Sample: the {sample_size} most recently merged pull requests in {repo}{when}, "
        "processed newest first. Bot authors, release or version bumps, docs-only and "
        "tests-only PRs were skipped by fixed rules, without a model call. Every other PR "
        "was screened by a Gemini Flash-Lite model for a consequential decision (a limit, "
        "timeout or retry value, a dependency, config or infrastructure, a migration, a "
        "public API change, a workaround, a revert). For each consequential PR a Gemini "
        "Flash model listed its decisions and checked up to 3 of them for a written reason "
        "in the PR description, commit messages, reviews and review threads, PR comments, "
        "linked issues and their comments, and code comments in the diff. A reason counts as "
        "found or partial only if the quoted sentence appears verbatim in that text (checked "
        "by code, not by the model). Self-evident decisions (a reason any maintainer knows, "
        "such as dropping an end-of-life runtime) are left out. Why-gap rate = (missing + "
        "partial) / (found + partial + missing). Reasons that live outside GitHub (chat, "
        "meetings, unlinked docs) are not seen, so a gap means no written reason on GitHub, "
        "not that nobody had one. The model can miss or misjudge a decision. These numbers "
        "describe this sample only."
    )


def headline(summary: dict[str, Any]) -> str | None:
    """The one-sentence result, or None before any decision was checked."""
    c = summary["decisions"]
    gaps, den = c["missing"] + c["partial"], c["found"] + c["partial"] + c["missing"]
    if not den:
        return None
    partial = " (or only a partial one)" if c["partial"] else ""
    return (
        f"In the last {summary['considered']} merged PRs of {summary['repo']}, {gaps} of {den} "
        f"consequential decisions had no written reason{partial} in the PR, its reviews, "
        "commits or linked issues."
    )


def summarize(repo: str, sample: list[dict], items: dict[int, dict]) -> dict[str, Any]:
    """Counts over the newest-first prefix of PRs that have a verdict (pure function)."""
    counts = dict.fromkeys(STATUSES, 0)
    by_kind = dict.fromkeys(PREFILTER_KINDS, 0)
    considered = skipped = screened = consequential = analyzed = prs_with_gaps = 0
    prefix_open = True
    for pr in sample:
        it = items.get(pr["number"])
        if not (prefix_open and _same_head(it, pr) and has_verdict(it)):
            prefix_open = False
            continue
        considered += 1
        if it["status"] == "skipped_deterministic":
            skipped += 1
            kind = it.get("skip_kind")
            if kind in by_kind:
                by_kind[kind] += 1
            continue
        screened += 1
        if (it.get("screen") or {}).get("consequential"):
            consequential += 1
        if it["status"] == "analyzed":
            analyzed += 1
            c = it.get("counts") or {}
            for s in STATUSES:
                counts[s] += int(c.get(s, 0))
            if c.get("missing", 0) + c.get("partial", 0):
                prs_with_gaps += 1

    in_sample = [items[p["number"]] for p in sample if _same_head(items.get(p["number"]), p)]
    models: list[str] = []
    for it in in_sample:
        for m in it.get("models_used") or []:
            if m not in models:
                models.append(m)
    return {
        "repo": repo,
        "sample_size": len(sample),
        "considered": considered,
        "skipped_deterministic": skipped,
        "skipped_by_kind": by_kind,
        "screened": screened,
        "consequential": consequential,
        "analyzed": analyzed,
        "pending": consequential - analyzed,
        "errors": sum(1 for it in in_sample if it.get("status") == "error"),
        "prs_with_gaps": prs_with_gaps,
        "decisions": counts,
        "decisions_total": sum(counts.values()),
        "why_gap_rate": why_gap_rate(counts),
        "calls_used": sum(int(it.get("calls") or 0) for it in in_sample),
        "models_used": models,
    }


# --- items from workflow outcomes ---------------------------------------------


def _decision_rows(result: dict) -> tuple[list[dict], dict[str, int]]:
    rationale = {r["decision_id"]: r for r in result.get("rationale") or []}
    rows, counts = [], dict.fromkeys(STATUSES, 0)
    for d in result.get("decisions") or []:
        r = rationale.get(d["id"])
        status = r["status"] if r else "unchecked"
        if status in counts:
            counts[status] += 1
        rows.append(
            {
                "id": d["id"],
                "summary": d.get("summary", ""),
                "category": d.get("category", "other"),
                "status": status,
                "reason_quote": (r or {}).get("reason_quote"),
            }
        )
    return rows, counts


def item_from_outcome(
    repo: str, pr: dict, outcome: RunOutcome, prev: dict | None, calls: int, epoch: str
) -> dict:
    prev = prev if _same_head(prev, pr) else None
    models = list((prev or {}).get("models_used") or [])
    for m in outcome.stats.get("models_used") or []:
        if m not in models:
            models.append(m)
    item: dict[str, Any] = {
        **{k: pr.get(k) for k in ("title", "url", "author", "merged_at", "head_sha")},
        "pr": pr["number"],
        "epoch": epoch,
        "run_id": outcome.run_id,
        "run_status": outcome.status,
        "calls": int((prev or {}).get("calls") or 0) + calls,
        "models_used": models,
        "screen": outcome.state.get("screen") or (prev or {}).get("screen"),
        "skip_reason": None,
        "decisions": [],
        "counts": dict.fromkeys(STATUSES, 0),
        "analysis_id": None,
        "error": None,
    }
    result = outcome.state.get("result")
    if outcome.status in ("gaps_found", "no_gaps") and result:
        item["decisions"], item["counts"] = _decision_rows(result)
        item["status"] = "analyzed"
        item["analysis_id"] = store.pr_doc_id(repo, pr["number"])
        item["rationale_capped"] = outcome.state.get("rationale_capped") or []
    elif outcome.status == "skipped" and result:
        item["skip_reason"] = result.get("skip_reason")
        if item["screen"] is not None:
            item["status"] = "not_consequential"
            item["analysis_id"] = store.pr_doc_id(repo, pr["number"])
        else:  # load_context's own skip rule (the same rules as the prefilter)
            item["status"] = "skipped_deterministic"
            reason = item["skip_reason"] or ""
            item["skip_kind"] = next(
                (k for k in ("docs-only", "tests-only") if k in reason), "release"
            )
    elif outcome.status in ("budget_stopped", "quota_paused"):
        item["status"] = "pending"  # resumes later; keeps the screen result if there is one
        item["error"] = outcome.summary[:300] if outcome.error else None
    else:
        item["status"] = "error"
        item["error"] = (outcome.error or outcome.summary or outcome.status)[:300]
    return item


# --- the run ----------------------------------------------------------------

Analyze = Callable[..., Awaitable[RunOutcome]]


@dataclass
class RunReport:
    stop: str  # complete | stopped_budget | paused_quota
    calls: int
    processed: list[int] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def execute(
    repo: str,
    sample: list[dict],
    files: dict[int, list[ChangedFile]],
    *,
    budget_calls: int,
    items: dict[int, dict],
    epoch: str,
    started_at: str,
    listed_at: str | None,
    analyze: Analyze | None = None,
    log: Callable[[str], None] = print,
) -> RunReport:
    """The budgeted loop. `items` holds what is already known (resume) and is updated."""
    analyze = analyze or analyze_pr.run
    budget = mc.CallBudget(budget_calls)
    mc.set_call_budget(budget)
    report = RunReport(stop="complete", calls=0)

    async def save_summary(status: str, finished: bool) -> dict:
        summary = summarize(repo, sample, items)
        summary.update(
            {
                "status": status,
                "epoch": epoch,
                "sample": sample,
                "sample_prs": [p["number"] for p in sample],
                "sample_listed_at": listed_at,
                "method": method_text(repo, len(sample), listed_at),
                "budget_calls": budget_calls,
                "last_run_calls": budget.used,
                "started_at": started_at,
                "finished_at": now_iso() if finished else None,
            }
        )
        await store.save_backfill(repo, summary)
        return summary

    try:
        await save_summary("running", finished=False)
        for pr in sample:
            n = pr["number"]
            prev = items.get(n)
            if _same_head(prev, pr) and prev.get("status") in TERMINAL:
                continue  # done at this head SHA (resume)
            kind, reason = prefilter(pr, files.get(n) or [])
            if kind:
                items[n] = {
                    **{k: pr.get(k) for k in ("title", "url", "author", "merged_at", "head_sha")},
                    "pr": n,
                    "epoch": epoch,
                    "status": "skipped_deterministic",
                    "skip_kind": kind,
                    "skip_reason": reason,
                    "calls": 0,
                    "screen": None,
                    "decisions": [],
                    "counts": dict.fromkeys(STATUSES, 0),
                }
                await store.save_backfill_item(repo, items[n])
                log(f"#{n:<6} skipped by rule ({reason})  0 calls")
                continue

            # A saved screen result (pending PR) is consequential: it needs extract + 1
            # rationale to make progress. Otherwise one screen call is progress.
            prescreened = (prev or {}).get("screen") if _same_head(prev, pr) else None
            need = 2 if prescreened else 1
            if budget.remaining < need:
                report.stop = "stopped_budget"
                break
            # extract (1) + this many rationale checks fit after the screen; below 1 the
            # gate stops a consequential PR before extraction and keeps its screen result.
            max_rationale = min(MAX_RATIONALE, budget.remaining - (0 if prescreened else 1) - 1)
            before = budget.used
            outcome = await analyze(
                repo,
                n,
                persist=True,
                trigger="backfill",
                skip_questions=True,
                prescreened=prescreened,
                max_rationale=max_rationale,
            )
            calls = budget.used - before
            report.calls = budget.used
            items[n] = item_from_outcome(repo, pr, outcome, prev, calls, epoch)
            await store.save_backfill_item(repo, items[n])
            report.processed.append(n)
            it = items[n]
            c = it["counts"]
            detail = (
                f"found {c['found']}, partial {c['partial']}, missing {c['missing']}, "
                f"self-evident {c['self_evident']}"
                if it["status"] == "analyzed"
                else (it.get("skip_reason") or it.get("error") or "")
            )
            log(
                f"#{n:<6} {it['status']:<18} {calls} calls  ({detail[:90]})  "
                f"total {budget.used}/{budget_calls}"
            )
            await save_summary("running", finished=False)
            if outcome.status == "quota_paused":
                report.stop = "paused_quota"
                break
            if outcome.status == "budget_stopped":
                report.stop = "stopped_budget"
                break
    finally:
        mc.set_call_budget(None)
        report.calls = budget.used

    summary = summarize(repo, sample, items)
    if summary["considered"] == len(sample) and summary["pending"] == 0:
        status = "complete"
    elif report.stop != "complete":
        status = report.stop  # stopped_budget | paused_quota: continue with --resume
    else:
        status = "incomplete"  # the loop finished but some PRs failed; --resume retries them
    report.stop = status
    report.summary = await save_summary(status, finished=True)
    return report


# --- sample loading -------------------------------------------------------------


async def load_files(
    repo: str, numbers: list[int], *, refresh: bool = False
) -> dict[int, list[ChangedFile]]:
    async with await client_for(repo, refresh=refresh) as gh:
        sem = asyncio.Semaphore(4)

        async def one(n: int):
            async with sem:
                files, _ = await fetch_pr_files(gh, repo, n)
            return n, files

        return dict(await asyncio.gather(*(one(n) for n in numbers)))


async def load_sample(repo: str, limit: int, *, refresh: bool = False) -> list[dict]:
    return [sample_row(p) for p in await list_merged_prs(repo, limit, refresh=refresh)]


async def prewarm_contexts(repo: str, numbers: list[int], *, refresh: bool = False) -> int:
    """Fetch (and disk-cache) the full PR context the workflow will read. GitHub only."""
    async with await client_for(repo, refresh=refresh) as gh:
        sem = asyncio.Semaphore(4)

        async def one(n: int):
            async with sem:
                await fetch_pr_context(repo, n, gh=gh)

        await asyncio.gather(*(one(n) for n in numbers))
        return gh.requests_made


# --- dry run ------------------------------------------------------------------


def estimate(signals: Signals) -> tuple[float, int]:
    """(probability the screen says consequential, rationale calls if it does)."""
    hits = (
        len(signals.dependency_files)
        + len(signals.config_files)
        + len(signals.migration_files)
        + len(signals.constant_changes)
        + int(signals.is_revert)
    )
    if signals.score >= FLAG_SCORE:
        return P_CONSEQUENTIAL_FLAGGED, min(MAX_RATIONALE, max(1, hits))
    return P_CONSEQUENTIAL_OTHER, 1


@dataclass
class PlanRow:
    number: int
    title: str
    kind: str | None  # prefilter kind, "done" (resume) or None (needs a model)
    p: float = 0.0
    k: int = 0
    screen_calls: int = 0
    flagged: bool = False

    @property
    def expected_main(self) -> float:
        return self.p * (1 + self.k)

    @property
    def expected(self) -> float:
        return self.screen_calls + self.expected_main

    @property
    def worst(self) -> int:
        return 0 if self.kind else self.screen_calls + 1 + MAX_RATIONALE


def plan(
    sample: list[dict], files: dict[int, list[ChangedFile]], items: dict[int, dict]
) -> list[PlanRow]:
    rows = []
    for pr in sample:
        n = pr["number"]
        prev = items.get(n)
        if _same_head(prev, pr) and prev.get("status") in TERMINAL:
            rows.append(PlanRow(n, pr["title"], "done"))
            continue
        kind, _ = prefilter(pr, files.get(n) or [])
        if kind:
            rows.append(PlanRow(n, pr["title"], kind))
            continue
        s = compute_signals_for(pr["title"], False, files.get(n) or [])
        p, k = estimate(s)
        pre = _same_head(prev, pr) and (prev or {}).get("screen")
        if pre:
            p = 1.0 if pre.get("consequential") else 0.0
        rows.append(
            PlanRow(n, pr["title"], None, p, k, screen_calls=0 if pre else 1, flagged=s.score >= 2)
        )
    return rows


def coverage(rows: list[PlanRow], budget: int) -> tuple[int, float, float]:
    """Newest-first PRs whose expected cost fits the budget: (prs, expected calls, cons)."""
    used = cons = 0.0
    covered = 0
    for r in rows:
        if used + r.expected > budget:
            break
        used += r.expected
        cons += r.p
        covered += 1
    return covered, used, cons


def dry_run_report(
    repo: str,
    sample: list[dict],
    files: dict[int, list[ChangedFile]],
    items: dict[int, dict],
    budgets: list[int],
    *,
    screen_chain: list[str],
    main_chain: list[str],
    target: int | None = None,
) -> list[str]:
    rows = plan(sample, files, items)
    kinds = {k: sum(1 for r in rows if r.kind == k) for k in PREFILTER_KINDS}
    todo = [r for r in rows if r.kind is None]
    done = sum(1 for r in rows if r.kind == "done")
    screen = sum(r.screen_calls for r in todo)
    main = sum(r.expected_main for r in todo)
    cons = sum(r.p for r in todo)
    worst = sum(r.worst for r in todo)
    newest, oldest = ((sample[i].get("merged_at") or "?")[:10] for i in (0, -1))
    out = [
        f"{repo}: last {len(sample)} merged PRs (merged {oldest} to {newest})",
        f"  prefilter skips (0 calls): {sum(kinds.values())}  "
        + ", ".join(f"{k} {v}" for k, v in kinds.items()),
        f"  already done at same head SHA: {done}",
        f"  need a model: {len(todo)}  ({sum(r.flagged for r in todo)} signal-flagged: deps, "
        f"config, migration or tuned constants)",
        f"  estimated calls: screen {screen} + extract/rationale {main:.1f} = {screen + main:.1f}"
        f"  (worst case {worst}); expected consequential PRs {cons:.1f}"
        + (f" = {cons / (screen + main):.2f} per call" if screen + main else ""),
        f"  chains: screen {','.join(screen_chain)} | main {','.join(main_chain)}",
        "  budget  PRs covered (newest first)  expected calls  expected consequential",
    ]
    for b in budgets:
        covered, used, c = coverage(rows, b)
        out.append(f"  {b:>6}  {covered:>3} of {len(rows):<22} {used:>14.1f}  {c:>22.1f}")
    out.append(
        f"  (cost model: P(consequential) {P_CONSEQUENTIAL_FLAGGED} if signal-flagged else "
        f"{P_CONSEQUENTIAL_OTHER}; rationale calls = signal hits, 1-{MAX_RATIONALE})"
    )
    if target:
        # Leave ~10% of the budget for retries; the hard cap still stops cleanly past it.
        covered, used, c = coverage(rows, SAFETY * target)
        head = [r for r in rows[:covered] if r.kind is None]
        out.append(
            f"  suggested for --budget-calls {target}: --limit {covered}  (expected {used:.1f} "
            f"calls: screen {sum(r.screen_calls for r in head)}, extract/rationale "
            f"{sum(r.expected_main for r in head):.1f}; expected consequential {c:.1f})"
        )
    return out
