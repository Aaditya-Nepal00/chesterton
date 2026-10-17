"""Deterministic path/symbol extraction and the guard match logic.

Symbols are identifiers on changed diff lines that a later PR would have to
touch to change the decision: constant names, config keys, dependency names.
"""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass

from app.analysis.evidence import split_hunks
from app.analysis.signals import (
    CONSTANT_KEYWORDS,
    is_config_or_infra,
    is_dependency_manifest,
)
from app.models.pr import ChangedFile

MAX_SYMBOLS = 10
GUARD_WINDOW = 3

_DECL_PREFIX = r"(?:(?:export|const|let|var|final|static|public|private|readonly|val)\s+)*"
_ASSIGN_OR_KEY = re.compile(
    r"""^\s*-?\s*""" + _DECL_PREFIX + r"""["']?(?P<name>[A-Za-z_][\w.\-]*)["']?"""
    r"(?:\s*:\s*[A-Za-z_][\w\[\]., |]*?(?=\s*=))?"  # optional type hint
    r"\s*(?::(?!:)|=(?!=)|:=)"
)
_KWARG = re.compile(r"\b(?P<name>[a-z_][a-z0-9_]*)\s*=(?!=)")
_DEPENDENCY = re.compile(
    r"""^\s*["']?(?P<name>@?[A-Za-z0-9][\w.\-]*(?:/[\w.\-]+)?)(?:\[[^\]]*\])?["']?\s*"""
    r"""(?:==|>=|<=|~=|!=|>|<|\^|~|@|:\s*["']?[\^~>=<\d])"""
)
_GENERIC_KEYS = {
    "name",
    "version",
    "description",
    "run",
    "uses",
    "with",
    "steps",
    "on",
    "jobs",
    "image",
    "env",
    "value",
    "type",
    "id",
    "key",
    "default",
    "args",
    "kwargs",
    "self",
    "return",
    "if",
    "else",
    "elif",
    "for",
    "while",
    "def",
    "class",
    "import",
    "from",
}


def _is_constant_like(name: str) -> bool:
    bare = name.rsplit(".", 1)[-1]
    return bare.isupper() and len(bare) > 1


def _has_keyword(name: str) -> bool:
    upper = name.upper()
    return any(k in upper for k in CONSTANT_KEYWORDS)


def symbols_from_line(path: str, text: str) -> list[str]:
    out: list[str] = []
    stripped = text.strip()
    if not stripped or stripped.startswith(("#", "//", "/*", "*")):
        return out
    dep = is_dependency_manifest(path)
    config = is_config_or_infra(path)
    if dep:
        m = _DEPENDENCY.match(text)
        if m and m.group("name").lower() not in _GENERIC_KEYS:
            out.append(m.group("name"))
    m = _ASSIGN_OR_KEY.match(text)
    if m:
        name = m.group("name")
        if name.lower() not in _GENERIC_KEYS and not keyword.iskeyword(name):
            if _is_constant_like(name) or _has_keyword(name) or config or dep or "_" in name:
                out.append(name)
    for km in _KWARG.finditer(text):
        if _has_keyword(km.group("name")):
            out.append(km.group("name"))
    return out


def extract_symbols(path: str, hunk_text: str) -> list[str]:
    """Identifiers on the changed (+/-) lines of one hunk, in order, de-duplicated."""
    seen: dict[str, None] = {}
    for hunk in split_hunks(hunk_text):
        for kind, _line, text in hunk.lines:
            if kind in "+-":
                for s in symbols_from_line(path, text):
                    seen.setdefault(s, None)
    return list(seen)[:MAX_SYMBOLS]


def symbols_for_snippet(path: str | None, snippet: str, fallback: list[str]) -> list[str]:
    """Symbols on the question's own snippet lines; a whole-file hunk would otherwise
    tie every constant in the file to every decision. Falls back to `fallback`."""
    seen: dict[str, None] = {}
    for line in (snippet or "").splitlines():
        for s in symbols_from_line(path or "", line):
            seen.setdefault(s, None)
    return list(seen)[:MAX_SYMBOLS] or fallback


def paths_and_symbols(
    evidence_ids: list[str], evidence: dict[str, dict]
) -> tuple[list[str], list[str]]:
    paths: dict[str, None] = {}
    symbols: dict[str, None] = {}
    for eid in evidence_ids:
        entry = evidence.get(eid)
        if not entry or not entry.get("path"):
            continue
        paths.setdefault(entry["path"], None)
        for s in extract_symbols(entry["path"], entry["text"]):
            symbols.setdefault(s, None)
    return list(paths), list(symbols)[:MAX_SYMBOLS]


# --- guard matching -----------------------------------------------------------


def _contains_symbol(text: str, symbol: str) -> bool:
    return re.search(rf"(?<![\w.\-]){re.escape(symbol)}(?![\w\-])", text) is not None


@dataclass
class GuardMatch:
    decision_number: int
    path: str
    line: int | None
    snippet: str  # changed lines with +/- markers
    hunk_text: str
    symbol: str | None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _match_in_file(f: ChangedFile, symbols: list[str], number: int) -> GuardMatch | None:
    for hunk in split_hunks(f.patch):
        lines = hunk.lines
        changed = [i for i, (k, _, _) in enumerate(lines) if k in "+-"]
        if not changed:
            continue
        if not symbols:
            i = changed[0]
            return _make_match(number, f.path, hunk, lines, i, None)
        for i in changed:
            window = lines[max(0, i - GUARD_WINDOW) : i + GUARD_WINDOW + 1]
            for sym in symbols:
                if any(_contains_symbol(t, sym) for _, _, t in window):
                    return _make_match(number, f.path, hunk, lines, i, sym)
    return None


def _make_match(number, path, hunk, lines, i, symbol) -> GuardMatch:
    # snippet: the run of changed lines around index i (max 4), with markers
    start = i
    while start > 0 and lines[start - 1][0] in "+-" and i - start < 3:
        start -= 1
    end = i
    while end + 1 < len(lines) and lines[end + 1][0] in "+-" and end - start < 3:
        end += 1
    run = lines[start : end + 1]
    snippet = "\n".join(k + t for k, _, t in run)
    line = next((n for k, n, _ in run if k == "+" and n), None)
    if line is None:  # pure deletion: the next surviving line
        line = next((n for _k, n, _ in lines[end + 1 :] if n), hunk.new_start)
    return GuardMatch(number, path, line, snippet, hunk.text, symbol)


def match_decisions(decisions: list[dict], files: list[ChangedFile]) -> list[GuardMatch]:
    """A decision matches if the PR changes one of its paths and (when it has symbols)
    a changed line, or a line within ±3 lines of one, contains one of its symbols.
    One match per decision (the first hunk that matches)."""
    by_path = {f.path: f for f in files if f.patch}
    matches: list[GuardMatch] = []
    for d in decisions:
        for path in d.get("paths") or []:
            f = by_path.get(path)
            if f is None:
                continue
            m = _match_in_file(f, list(d.get("symbols") or []), int(d["number"]))
            if m:
                matches.append(m)
                break
    return matches
