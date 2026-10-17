"""Deterministic PR signals: which files and lines look like decisions. No LLM."""

from __future__ import annotations

import re
from collections.abc import Iterable
from fnmatch import fnmatch
from pathlib import PurePosixPath

from app.analysis.evidence import split_hunks
from app.models.analysis import ConstantChange, Signals
from app.models.pr import ChangedFile, PRContext

DEPENDENCY_PATTERNS = [
    "package.json",
    "requirements*.txt",
    "requirements/*.txt",
    "pyproject.toml",
    "setup.cfg",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle*",
    "Gemfile",
    "composer.json",
]
CONFIG_NAME_PATTERNS = [
    "Dockerfile*",
    "*.dockerfile",
    "docker-compose*",
    "compose.y*ml",
    "*.tf",
    "*.tfvars",
    ".env.example",
    "*.ini",
    "*.toml",
    "*.yaml",
    "*.yml",
    "*.cfg",
]
LOCKFILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.lock",
    "go.sum",
    "Pipfile.lock",
}
GENERATED_DIRS = {"dist", "build", "vendor", "vendored", "third_party", "node_modules", "_vendor"}
DOC_SUFFIXES = {".md", ".rst", ".txt", ".adoc"}
DOC_DIRS = {"docs", "doc", "docs_src"}
CONSTANT_KEYWORDS = (
    "RETRY",
    "RETRIES",
    "TIMEOUT",
    "LIMIT",
    "MAX",
    "MIN",
    "TTL",
    "INTERVAL",
    "BATCH",
    "SIZE",
    "THRESHOLD",
    "DELAY",
    "POOL",
    "WORKERS",
    "BACKOFF",
    "CONCURRENCY",
)

_RELEASE_TITLE = re.compile(
    r"(^|\W)(release|version)\s+v?\d+\.\d+|^\W*bump\s+\S+\s+from\b|^\W*chore\(release\)"
    r"|^\W*v?\d+\.\d+\.\d+([.-]?\w+)?\s*$",  # the whole title is a version: "v2.34.2"
    re.IGNORECASE,
)
_REVERT_TITLE = re.compile(r"^\W*revert\b", re.IGNORECASE)
_DURATION = r"(?:ms|s|sec|secs|seconds|m|min|mins|minutes|h|hr|hours|d|days|kb|mb|gb|k)?"
_ASSIGN = re.compile(
    r"""(?P<name>[A-Za-z_][\w.\-]*)["']?"""
    r"(?:\s*:\s*[A-Za-z_][\w\[\]., |]*?(?=\s*=))?"  # optional type annotation: `x: int = 3`
    r"""\s*(?:=|:=|:)\s*["']?"""
    r"(?P<val>-?\d+(?:_\d+)*(?:\.\d+)?" + _DURATION + r""")["']?\s*(?=[,;)\]}#\s]|$)""",
    re.IGNORECASE,
)


def _name(path: str) -> str:
    return PurePosixPath(path).name


def _parts(path: str) -> list[str]:
    return [p.lower() for p in PurePosixPath(path).parts]


def is_lockfile(path: str) -> bool:
    return _name(path) in LOCKFILES


def is_generated(path: str) -> bool:
    name = _name(path)
    if ".min." in name or name.endswith((".map", "_pb2.py", ".pb.go", ".snap")):
        return True
    return any(p in GENERATED_DIRS for p in _parts(path)[:-1])


def is_doc(path: str) -> bool:
    if is_dependency_manifest(path):  # requirements*.txt is not documentation
        return False
    parts = _parts(path)
    return PurePosixPath(path).suffix.lower() in DOC_SUFFIXES or (
        len(parts) > 1 and parts[0] in DOC_DIRS
    )


def is_test(path: str) -> bool:
    parts = _parts(path)
    name = parts[-1] if parts else ""
    return (
        any(p in ("tests", "test", "__tests__", "spec") for p in parts[:-1])
        or name.startswith("test_")
        or name.endswith(("_test.py", "_test.go", ".test.ts", ".test.js", ".spec.ts"))
    )


def is_dependency_manifest(path: str) -> bool:
    name = _name(path)
    return any(fnmatch(name, p) or fnmatch(path, p) for p in DEPENDENCY_PATTERNS)


def is_config_or_infra(path: str) -> bool:
    if is_dependency_manifest(path) or is_lockfile(path) or is_doc(path):
        return False
    parts = _parts(path)
    if path.startswith(".github/workflows/"):
        return True
    if any(p in ("k8s", "kubernetes", "helm", "charts", "deploy", "infra") for p in parts[:-1]):
        if _name(path).endswith((".yaml", ".yml", ".json", ".tpl")):
            return True
    if path.startswith(".github/"):  # issue templates, funding, etc. are not decisions
        return False
    return any(fnmatch(_name(path), p) for p in CONFIG_NAME_PATTERNS)


def is_migration(path: str) -> bool:
    parts = _parts(path)
    return any(p in ("migrations", "alembic") for p in parts[:-1])


def file_priority(path: str) -> int:
    """Lower is more important; used to spend the diff-line budget wisely."""
    if is_migration(path) or is_dependency_manifest(path) or is_config_or_infra(path):
        return 0
    if is_doc(path):
        return 3
    if is_test(path):
        return 2
    return 1


def release_or_bot(title: str, author_is_bot: bool) -> bool:
    return author_is_bot or bool(_RELEASE_TITLE.search(title or ""))


def _looks_like_version(val: str) -> bool:
    return val.count(".") >= 2


def _is_interesting_name(name: str, path: str) -> bool:
    upper = name.upper()
    if "VERSION" in upper or "PYTHON" in upper:  # runtime/version pins are not tuning constants
        return False
    if any(k in upper for k in CONSTANT_KEYWORDS):
        return True
    bare = name.rsplit(".", 1)[-1]
    return bare.isupper() or is_config_or_infra(path)


def find_constant_changes(files: Iterable[ChangedFile]) -> list[ConstantChange]:
    """Same identifier, different numeric/duration literal, within one diff hunk."""
    changes: list[ConstantChange] = []
    for f in files:
        if not f.patch or is_doc(f.path) or is_test(f.path) or is_lockfile(f.path):
            continue
        for hunk in split_hunks(f.patch):
            removed: dict[str, str] = {}
            added: dict[str, tuple[str, int | None]] = {}
            for kind, new_line, text in hunk.lines:
                if kind not in "+-":
                    continue
                for m in _ASSIGN.finditer(text):
                    name, val = m.group("name"), m.group("val").strip()
                    if _looks_like_version(val) or not _is_interesting_name(name, f.path):
                        continue
                    if kind == "-":
                        removed.setdefault(name, val)
                    else:
                        added.setdefault(name, (val, new_line))
            for name, (new, line) in added.items():
                old = removed.get(name)
                if old is not None and old.replace(" ", "") != new.replace(" ", ""):
                    changes.append(
                        ConstantChange(path=f.path, line=line, old=old, new=new, name=name)
                    )
    return changes


def compute_signals_for(title: str, author_is_bot: bool, files: list[ChangedFile]) -> Signals:
    paths = [f.path for f in files]
    deps = [p for p in paths if is_dependency_manifest(p)]
    config = [p for p in paths if is_config_or_infra(p)]
    migrations = [p for p in paths if is_migration(p)]
    constants = find_constant_changes(files)
    is_revert = bool(_REVERT_TITLE.search(title or ""))
    docs_only = bool(paths) and all(is_doc(p) for p in paths)
    tests_only = not docs_only and bool(paths) and all(is_test(p) or is_doc(p) for p in paths)
    rel_bot = release_or_bot(title, author_is_bot)

    score = 0
    if deps:
        score += 3
    if config:
        score += 2
    if migrations:
        score += 3
    keyword_hits = sum(1 for c in constants if any(k in c.name.upper() for k in CONSTANT_KEYWORDS))
    score += min(4, 2 * keyword_hits + (len(constants) - keyword_hits))
    if is_revert:
        score += 2
    if sum(f.additions + f.deletions for f in files if not is_doc(f.path)) > 300:
        score += 1
    if docs_only or tests_only or rel_bot:
        score = 0

    return Signals(
        score=min(10, score),
        dependency_files=deps,
        config_files=config,
        migration_files=migrations,
        constant_changes=constants,
        is_revert=is_revert,
        is_docs_only=docs_only,
        is_tests_only=tests_only,
        is_release_or_bot=rel_bot,
    )


def compute_signals(pr: PRContext) -> Signals:
    return compute_signals_for(pr.title, pr.author_is_bot, pr.files)


def skip_reason(signals: Signals) -> str | None:
    if signals.is_release_or_bot:
        return "release or bot PR"
    if signals.is_docs_only:
        return "docs-only PR"
    if signals.is_tests_only:
        return "tests-only PR"
    return None
