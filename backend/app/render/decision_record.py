"""Decision record files (decisions/NNNN-slug.md), numbering, and the related PR texts."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable

from app.github.markers import build_marker
from app.models.records import DecisionRecord, QuestionItem

DECISIONS_DIR = "decisions"
_NUMBERED = re.compile(r"^(\d{4})-")
_MAX_SLUG = 50


def slugify(text: str, max_len: int = _MAX_SLUG) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[`'\"]", "", text.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0] or slug[:max_len]
    return slug or "decision"


def numbers_in_dir(names: Iterable[str]) -> set[int]:
    out = set()
    for n in names:
        m = _NUMBERED.match(n)
        if m:
            out.add(int(m.group(1)))
    return out


def allocate_numbers(used: Iterable[int], count: int) -> list[int]:
    """The next `count` numbers after the highest one in use (gaps are not reused)."""
    start = max(used, default=0) + 1
    return list(range(start, start + count))


def record_id(number: int) -> str:
    return f"{number:04d}"


def _yaml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # JSON strings are valid YAML scalars


def _yaml_list(key: str, values: list[str]) -> list[str]:
    if not values:
        return [f"{key}: []"]
    return [f"{key}:"] + [f"  - {_yaml_str(v)}" for v in values]


def _blockquote(text: str) -> list[str]:
    return [f"> {ln}" if ln.strip() else ">" for ln in text.strip().splitlines()]


def render_record(rec: DecisionRecord) -> str:
    front = [
        "---",
        f"id: {_yaml_str(rec.id)}",
        f"title: {_yaml_str(rec.title)}",
        f"status: {rec.status}",
        f"owner: {_yaml_str('@' + rec.owner if rec.owner else '')}",
        *_yaml_list("paths", rec.paths),
        *_yaml_list("symbols", rec.symbols),
        f"source_pr: {_yaml_str(rec.source_pr_url)}",
        f"answer_url: {_yaml_str(rec.answer_url or '')}",
        f"recorded: {rec.recorded}",
        "---",
    ]
    owner = f"@{rec.owner}" if rec.owner else "the author"
    body = [
        "",
        f"# {rec.id}. {rec.title}",
        "",
        "## Decision",
        "",
        rec.decision.strip(),
        "",
        "## Why",
        "",
        *_blockquote(rec.reason_quote),
        "",
        f"— {owner}, [answer on #{rec.source_pr}]({rec.answer_url})"
        if rec.answer_url
        else f"— {owner}",
    ]
    # The quote is the reason; add the paraphrase only when the quote is too short to
    # stand on its own.
    if rec.reason and len(rec.reason_quote.split()) < 8:
        body += ["", rec.reason.strip()]
    if rec.constraints:
        body += ["", "Constraints:", *[f"- {c}" for c in rec.constraints]]
    if rec.alternatives:
        body += ["", "## Alternatives considered", "", *[f"- {a}" for a in rec.alternatives]]
    body += [
        "",
        "## Safe to change when",
        "",
        rec.safe_to_change_when.strip() or "Not stated. Ask the owner before changing it.",
        "",
        "## Evidence",
        "",
        f"- Source PR: [#{rec.source_pr} {rec.source_pr_title}]({rec.source_pr_url})".rstrip(),
    ]
    if rec.evidence_url:
        where = rec.evidence_path or "change"
        if rec.evidence_line:
            where += f":{rec.evidence_line}"
        body.append(f"- Change: [`{where}`]({rec.evidence_url})")
    if rec.answer_url:
        body.append(f"- Answer: [comment by {owner}]({rec.answer_url})")
    body.append(f"- Question asked: {rec.question}")
    return "\n".join(front + body) + "\n"


def record_pr_title(records: list[DecisionRecord]) -> str:
    if len(records) == 1:
        return f"Decision record {records[0].id}: {records[0].title}"
    return f"Decision records {records[0].id}–{records[-1].id}"


def record_pr_body(records: list[DecisionRecord]) -> str:
    rec = records[0]
    files = "\n".join(f"- `{r.file_path}` — {r.title}" for r in records)
    return (
        f"Records the reasoning @{rec.owner} gave on #{rec.source_pr} "
        f"([answer]({rec.answer_url})).\n\n{files}\n\n"
        "Merging this keeps the reason next to the code; Chesterton will point to it "
        "when a later PR touches the same lines."
    )


def reply_text(
    records: list[DecisionRecord],
    record_pr_number: int | None,
    still_open: list[QuestionItem],
    *,
    pr: int,
    answer_comment_id: int,
) -> str:
    files = ", ".join(f"`{r.file_path}`" for r in records)
    where = f" in #{record_pr_number}" if record_pr_number else ""
    lines = [
        build_marker("recorded", pr=pr, comment=answer_comment_id),
        f"Recorded as {files}{where}. Thanks!",
    ]
    if still_open:
        lines.append("")
        lines.append("Still open:")
        lines += [f"- {q.qid}: {q.text}" for q in still_open]
    return "\n".join(lines)
