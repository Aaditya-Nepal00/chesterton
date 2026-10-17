"""Which runs to delete when cleaning up demo data (pure; the script does the I/O).

Never selects a run that another document points to (pr_analyses / questions /
guards .run_id), so no "trace of this analysis" link breaks.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

FAILED = {"error", "quota_paused", "running"}
TEST_KINDS = {"selftest", "test"}
TEST_REPOS = {"chesterton/selftest", "acme/demo"}


@dataclass
class Candidate:
    run_id: str
    category: str  # a | b | c | d
    reason: str
    repo: str = ""
    pr: int | None = None
    kind: str = ""
    status: str = ""


def _kind(run: dict) -> str:
    return run.get("kind") or "analyze"


def _pr(run: dict) -> int | None:
    return run.get("pr") if run.get("pr") is not None else run.get("number")


def select_runs_to_delete(
    runs: list[dict], referenced: set[str], orphan_run_ids: list[str] = ()
) -> tuple[list[Candidate], list[tuple[str, str]]]:
    """Returns (to_delete, kept_notes). `runs` are run docs (with run_id)."""
    picked: dict[str, Candidate] = {}
    kept: list[tuple[str, str]] = []

    def pick(run: dict, cat: str, reason: str) -> None:
        rid = run["run_id"]
        if rid in referenced:
            kept.append(
                (
                    rid,
                    f"would be ({cat}) {reason}, but kept: "
                    "referenced by an analysis/question/guard doc",
                )
            )
            return
        picked.setdefault(
            rid,
            Candidate(
                rid, cat, reason, run.get("repo", ""), _pr(run), _kind(run), run.get("status", "")
            ),
        )

    for run in runs:
        if (
            run.get("kind") in TEST_KINDS
            or run.get("trigger") in TEST_KINDS
            or run.get("repo") in TEST_REPOS
        ):
            pick(run, "b", "smoke/dev script run")
        elif not run.get("kind"):
            pick(run, "a", "dev run without kind (Build 3 CLI)")

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for run in runs:
        if run["run_id"] in picked or run.get("kind") in TEST_KINDS:
            continue
        groups[(run.get("repo"), _pr(run), _kind(run))].append(run)
    for (repo, pr, kind), members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: r["run_id"])  # run_id is time-ordered
        good = [r for r in members if r.get("status") not in FAILED]
        keep = (good or members)[-1]
        for r in members:
            if r is not keep:
                pick(
                    r,
                    "c",
                    f"duplicate {kind} of {repo}#{pr}; "
                    f"keeping {keep['run_id']} ({keep.get('status')})",
                )

    for rid in orphan_run_ids:
        picked.setdefault(
            rid, Candidate(rid, "d", "events subcollection without a run doc (test write)")
        )
    return sorted(picked.values(), key=lambda c: (c.category, c.run_id)), kept
