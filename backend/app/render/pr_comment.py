"""Markdown for the questions comment Chesterton posts on a merged PR."""

from __future__ import annotations

from app.analysis.evidence import normalize
from app.github.markers import build_marker
from app.models.analysis import RenderedQuestion

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


def lang_for(path: str | None) -> str:
    if not path:
        return ""
    if path.rsplit("/", 1)[-1].startswith("Dockerfile"):
        return "dockerfile"
    dot = path.rfind(".")
    return _LANGS.get(path[dot:].lower(), "") if dot != -1 else ""


def fence_for(snippet: str) -> str:
    fence = "```"
    while fence in snippet:
        fence += "`"
    return fence


def code_block(snippet: str, path: str | None, indent: str = "") -> list[str]:
    fence = fence_for(snippet)
    return (
        [f"{indent}{fence}{lang_for(path)}"]
        + [f"{indent}{ln}" for ln in snippet.splitlines()]
        + [f"{indent}{fence}"]
    )


def question_quotes_snippet(text: str, snippet: str) -> bool:
    """True when the snippet is one line and the question already quotes all of it."""
    lines = [ln for ln in (snippet or "").splitlines() if ln.strip()]
    if len(lines) != 1:
        return False
    return normalize(lines[0]) in normalize(text.replace("`", ""))


def render_comment(questions: list[RenderedQuestion], *, pr: int, run_id: str) -> str | None:
    if not questions:
        return None
    intro = (
        "Quick question before this context fades:"
        if len(questions) == 1
        else "A few quick questions before this context fades:"
    )
    lines = [build_marker("questions", pr=pr, run_id=run_id), intro, ""]
    for i, q in enumerate(questions, 1):
        location = f"{q.path}:{q.line}" if q.path and q.line else (q.path or q.evidence_id)
        loc_md = f"[`{location}`]({q.url})" if q.url else f"`{location}`"
        if question_quotes_snippet(q.text, q.snippet):
            lines.append(f"{i}. {q.text} {loc_md}")
        else:
            lines.append(f"{i}. {q.text}")
            lines.append("")
            lines.append(f"   {loc_md}")
            lines.extend(code_block(q.snippet, q.path, indent="   "))
        lines.append("")
    lines.append("---")
    lines.append(FOOTER)
    return "\n".join(lines)
