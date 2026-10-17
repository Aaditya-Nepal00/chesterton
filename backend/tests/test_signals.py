import pytest

from app.analysis import signals as sig
from app.models.pr import ChangedFile
from tests.fixtures import make_pr


def test_retry_pr_signals():
    s = sig.compute_signals(make_pr())
    assert s.dependency_files == ["pyproject.toml"]
    assert "uv.lock" not in s.dependency_files + s.config_files
    names = {(c.name, c.old, c.new) for c in s.constant_changes}
    assert ("MAX_RETRIES", "5", "3") in names
    assert ("timeout", "10", "5") not in names  # timeout=10 is a context line, not a removal
    assert not s.is_docs_only and not s.is_release_or_bot and not s.is_revert
    assert s.score >= 5


def test_constant_change_line_number_and_type_hints():
    patch = "@@ -1,2 +1,2 @@\n-    max_retries: int = 5\n+    max_retries: int = 3\n"
    changes = sig.find_constant_changes(
        [ChangedFile(path="svc/client.py", status="modified", patch=patch)]
    )
    assert [(c.name, c.old, c.new, c.line) for c in changes] == [("max_retries", "5", "3", 1)]


def test_durations_and_kwargs():
    patch = "@@ -3,2 +3,2 @@\n-  timeout: 30s\n+  timeout: 90s\n"
    changes = sig.find_constant_changes(
        [ChangedFile(path="deploy/app.yaml", status="modified", patch=patch)]
    )
    assert [(c.old, c.new) for c in changes] == [("30s", "90s")]


def test_ignores_versions_lowercase_locals_tests_and_docs():
    patch = '@@ -1,4 +1,4 @@\n-version = "1.2.3"\n+version = "1.2.4"\n-    i = 1\n+    i = 2\n'
    files = [
        ChangedFile(path="pkg/mod.py", status="modified", patch=patch),
        ChangedFile(
            path="tests/test_x.py",
            status="modified",
            patch="@@ -1 +1 @@\n-MAX_RETRIES = 5\n+MAX_RETRIES = 3\n",
        ),
        ChangedFile(
            path="README.md", status="modified", patch="@@ -1 +1 @@\n-TIMEOUT = 5\n+TIMEOUT = 3\n"
        ),
    ]
    assert sig.find_constant_changes(files) == []


@pytest.mark.parametrize(
    "title,bot,expected",
    [
        ("🔖 Release version 0.143.3", False, True),
        ("Version 0.28.1", False, True),
        ("Bump actions/checkout from 3 to 4", False, True),
        ("Fix pool timeout handling", True, True),
        ("Fix pool timeout handling", False, False),
        ("Release the connection on error", False, False),
        ("v2.34.2", False, True),
        ("1.0.0rc1", False, True),
        ("3.11", False, False),
        ("3.11+", False, False),  # a Python version support change, not a release
    ],
)
def test_release_or_bot(title, bot, expected):
    assert sig.release_or_bot(title, bot) is expected


def test_docs_only_and_revert():
    docs = make_pr(
        title='Revert "Add retries"',
        files=[
            ChangedFile(path="docs/index.md", status="modified"),
            ChangedFile(path="CHANGELOG.md", status="modified"),
        ],
    )
    s = sig.compute_signals(docs)
    assert s.is_docs_only and s.is_revert and s.score == 0
    assert sig.skip_reason(s) == "docs-only PR"


@pytest.mark.parametrize(
    "path,dep,config,migration",
    [
        ("requirements-dev.txt", True, False, False),
        ("frontend/package.json", True, False, False),
        ("Dockerfile.prod", False, True, False),
        (".github/workflows/ci.yml", False, True, False),
        (".github/ISSUE_TEMPLATE/bug.yml", False, False, False),
        ("infra/main.tf", False, True, False),
        ("app/alembic/versions/001_init.py", False, False, True),
        ("db/migrations/0002_add_index.sql", False, False, True),
        ("package-lock.json", False, False, False),
        ("mkdocs.yml", False, True, False),
    ],
)
def test_path_classifiers(path, dep, config, migration):
    assert sig.is_dependency_manifest(path) is dep
    assert sig.is_config_or_infra(path) is config
    assert sig.is_migration(path) is migration


def test_requirements_txt_is_a_dependency_not_a_doc():
    pr = make_pr(
        files=[ChangedFile(path="requirements.txt", status="modified", additions=3, deletions=3)]
    )
    s = sig.compute_signals(pr)
    assert s.dependency_files == ["requirements.txt"] and not s.is_docs_only
    assert not sig.is_doc("requirements-dev.txt")


def test_python_version_pins_are_not_constants():
    patch = '@@ -1 +1 @@\n-python = "3.10"\n+python = "3.11"\n'
    files = [ChangedFile(path="tox.ini", status="modified", patch=patch)]
    assert sig.find_constant_changes(files) == []


def test_lockfile_and_generated():
    assert sig.is_lockfile("web/pnpm-lock.yaml")
    assert sig.is_generated("static/app.min.js")
    assert sig.is_generated("vendor/github.com/x/y.go")
    assert not sig.is_generated("src/build_info.py")
