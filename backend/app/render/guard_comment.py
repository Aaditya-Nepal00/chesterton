"""The single, informational guard comment on a PR that touches recorded decisions."""

from __future__ import annotations

from app.github.markers import build_marker
from app.render.pr_comment import fence_for

NO_LONGER = "This PR no longer touches code covered by a recorded decision. Nothing to check here."


def _one_line(text: str, n: int = 220) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def render_guard(items: list[dict], *, pr: int) -> str:
    """items: [{match: GuardMatch dict, decision: decision doc, verdict, note, link}]"""
    marker = build_marker("guard", pr=pr)
    if not items:
        return f"{marker}\n{NO_LONGER}"
    plural = "decisions" if len(items) > 1 else "decision"
    lines = [
        marker,
        f"For context: this PR touches code covered by a recorded {plural}. "
        "Nothing here blocks the PR.",
        "",
    ]
    for it in items:
        m, d = it["match"], it["decision"]
        where = f"{m['path']}:{m['line']}" if m.get("line") else m["path"]
        verb = "changes" if it["verdict"] == "changes_decision" else "edits code next to"
        fence = fence_for(m["snippet"])
        lines += [
            f"**[{d['id']}. {d['title']}]({it['link']})** — this PR {verb} it at `{where}`:",
            "",
            f"{fence}diff",
            m["snippet"],
            fence,
            "",
            f"- Reason: “{_one_line(d['reason_quote'])}”",
            f"- Safe to change when: {_one_line(d.get('safe_to_change_when') or 'not stated')}",
            f"- Owner: @{d['owner']}" if d.get("owner") else "- Owner: unknown",
        ]
        if it.get("note"):
            lines.append(f"- Note: {_one_line(it['note'], 160)}")
        lines.append("")
    lines.append("If the reason still holds, no action needed. If it changed, a line here helps.")
    return "\n".join(lines)
