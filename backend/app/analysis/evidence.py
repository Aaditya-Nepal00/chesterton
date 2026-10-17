"""Evidence index: every piece of PR context gets a stable id the LLM may cite.

Ids: pr:body, commit:<sha7>, comment:<id>, issue:<number>, diff:<path>#<n>
(n = 0-based hunk index within that file's patch).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.models.pr import PRContext

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    index: int
    header: str
    new_start: int
    # (kind, new_line_number, text) where kind is "+", "-" or " "; new line is None for "-"
    lines: list[tuple[str, int | None, str]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join([self.header] + [k + t for k, _, t in self.lines])


def split_hunks(patch: str | None) -> list[Hunk]:
    hunks: list[Hunk] = []
    if not patch:
        return hunks
    current: Hunk | None = None
    new_line = 0
    for raw in patch.splitlines():
        m = _HUNK_HEADER.match(raw)
        if m:
            new_line = int(m.group(3))
            current = Hunk(index=len(hunks), header=raw, new_start=new_line)
            hunks.append(current)
            continue
        if current is None or raw.startswith("\\"):  # "\ No newline at end of file"
            continue
        kind, text = (raw[0], raw[1:]) if raw and raw[0] in "+- " else (" ", raw)
        if kind == "-":
            current.lines.append(("-", None, text))
        else:
            current.lines.append((kind, new_line, text))
            new_line += 1
    return hunks


def diff_id(path: str, index: int) -> str:
    return f"diff:{path}#{index}"


def diff_url(pr_url: str, path: str, line: int | None) -> str:
    anchor = hashlib.sha256(path.encode()).hexdigest()
    return f"{pr_url}/files#diff-{anchor}" + (f"R{line}" if line else "")


def build_evidence_index(pr: PRContext) -> dict[str, dict]:
    """{id: {kind, text, url, path, line_start}}"""
    index: dict[str, dict] = {}

    def add(eid, kind, text, url=None, path=None, line_start=None):
        if text and text.strip():
            index[eid] = {
                "kind": kind,
                "text": text,
                "url": url,
                "path": path,
                "line_start": line_start,
            }

    add("pr:body", "pr_body", pr.body, pr.url)
    for c in pr.commits:
        add(f"commit:{c.sha[:7]}", "commit", c.message, f"{pr.url}/commits/{c.sha}")
    for c in pr.comments:
        add(f"comment:{c.id}", c.kind, c.body, c.url, c.path, c.line)
    for issue in pr.linked_issues:
        add(
            f"issue:{issue.number}",
            "linked_issue",
            f"{issue.title}\n\n{issue.body or ''}".strip(),
            issue.url,
        )
    for f in pr.files:
        for h in split_hunks(f.patch):
            add(
                diff_id(f.path, h.index),
                "diff",
                h.text,
                diff_url(pr.url, f.path, h.new_start),
                f.path,
                h.new_start,
            )
    return index


def is_diff_id(eid: str) -> bool:
    return eid.startswith("diff:")


def normalize(text: str) -> str:
    """Whitespace-collapsed, case-folded, with curly quotes straightened."""
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(text.split()).casefold()


def strip_diff_markers(snippet: str) -> str:
    lines = snippet.splitlines()
    if lines and all(ln[:1] in "+- " for ln in lines if ln):
        lines = [ln[1:] if ln[:1] in "+- " else ln for ln in lines]
    return "\n".join(lines)


def locate_snippet(entry: dict, snippet: str) -> int | None:
    """New-file line number of the first snippet line inside a diff hunk, if found."""
    hunks = split_hunks(entry.get("text"))
    if not hunks:
        return None
    first = next((ln for ln in strip_diff_markers(snippet).splitlines() if ln.strip()), "")
    target = normalize(first)
    if not target:
        return None
    for kind, new_line, text in hunks[0].lines:
        if kind != "-" and target in normalize(text):
            return new_line
    for _kind, _new_line, text in hunks[0].lines:  # snippet is a removed line
        if target in normalize(text):
            return entry.get("line_start")
    return None


def default_snippet(entry: dict, max_lines: int = 3) -> tuple[str, int | None]:
    """First changed lines of a hunk, preferring additions."""
    hunks = split_hunks(entry.get("text"))
    if not hunks:
        return "", None
    lines = hunks[0].lines
    picked = [(n, t) for k, n, t in lines if k == "+" and t.strip()][:max_lines]
    if not picked:
        picked = [(entry.get("line_start"), t) for k, _n, t in lines if k == "-" and t.strip()]
        picked = picked[:max_lines]
    if not picked:
        return "", entry.get("line_start")
    return "\n".join(t for _, t in picked), picked[0][0]
