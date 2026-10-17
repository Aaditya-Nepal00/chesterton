"""Markdown for the PR comment that a later build will post."""

from __future__ import annotations

from app.models.analysis import RenderedQuestion

MARKER = "<!-- chesterton:questions run_id={run_id} -->"
FOOTER = (
    "Reply in this thread — one line is enough. "
    "Your answer becomes a decision record in decisions/."
)

_LANGS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".toml": "toml",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".sh": "bash",
    ".tf": "hcl",
    ".ini": "ini",
    ".cfg": "ini",
}


def _lang(path: str | None) -> str:
    if not path:
        return ""
    if path.rsplit("/", 1)[-1].startswith("Dockerfile"):
        return "dockerfile"
    dot = path.rfind(".")
    return _LANGS.get(path[dot:].lower(), "") if dot != -1 else ""


def _fence(snippet: str) -> str:
    fence = "```"
    while fence in snippet:
        fence += "`"
    return fence


def render_comment(
    questions: list[RenderedQuestion], *, author: str | None, run_id: str
) -> str | None:
    if not questions:
        return None
    greeting = f"Hi @{author} — " if author else "Hi — "
    what = (
        "one change in this PR looks like a deliberate choice"
        if len(questions) == 1
        else "a few changes in this PR look like deliberate choices"
    )
    lines = [
        MARKER.format(run_id=run_id),
        f"{greeting}{what}, but I couldn't find the reasoning written down. "
        "A quick note would help whoever touches this code next.",
        "",
    ]
    for i, q in enumerate(questions, 1):
        location = f"{q.path}:{q.line}" if q.path and q.line else (q.path or q.evidence_id)
        loc_md = f"[`{location}`]({q.url})" if q.url else f"`{location}`"
        fence = _fence(q.snippet)
        lines.append(f"{i}. {q.text}")
        lines.append("")
        lines.append(f"   {loc_md}")
        lines.append(f"   {fence}{_lang(q.path)}")
        lines.extend(f"   {ln}" for ln in q.snippet.splitlines())
        lines.append(f"   {fence}")
        lines.append("")
    lines.append("---")
    lines.append(FOOTER)
    return "\n".join(lines)
