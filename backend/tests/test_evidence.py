from app.analysis.evidence import (
    build_evidence_index,
    default_snippet,
    locate_snippet,
    normalize,
    split_hunks,
)
from app.github.fetch import MAX_DIFF_LINES, MAX_LINES_PER_FILE, process_files
from tests.fixtures import RETRY_PATCH, make_pr


def test_split_hunks_line_numbers():
    hunks = split_hunks(RETRY_PATCH)
    assert [h.index for h in hunks] == [0, 1]
    assert hunks[0].new_start == 10
    added = [(n, t) for k, n, t in hunks[0].lines if k == "+"]
    assert added == [(13, "MAX_RETRIES = 3")]
    removed = [n for k, n, _ in hunks[0].lines if k == "-"]
    assert removed == [None]
    # a bare empty line inside a patch is context, not a marker
    assert all(k in "+- " and k for k, _, _ in hunks[0].lines)


def test_evidence_ids_and_fields():
    ev = build_evidence_index(make_pr())
    assert set(ev) == {
        "pr:body",
        "commit:abcdef1",
        "comment:9001",
        "comment:9002",
        "issue:7",
        "diff:payments/webhook.py#0",
        "diff:payments/webhook.py#1",
        "diff:pyproject.toml#0",
    }
    hunk = ev["diff:payments/webhook.py#0"]
    assert hunk["kind"] == "diff" and hunk["path"] == "payments/webhook.py"
    assert hunk["line_start"] == 10
    assert "+MAX_RETRIES = 3" in hunk["text"]
    assert hunk["url"].endswith("R10")
    assert ev["comment:9001"]["path"] == "pyproject.toml"
    assert ev["issue:7"]["text"].startswith("Webhook storms")


def test_empty_body_has_no_evidence():
    ev = build_evidence_index(make_pr(body="   "))
    assert "pr:body" not in ev


def test_locate_and_default_snippet():
    ev = build_evidence_index(make_pr())
    entry = ev["diff:payments/webhook.py#0"]
    assert locate_snippet(entry, "+MAX_RETRIES = 3") == 13
    assert locate_snippet(entry, "MAX_RETRIES   =  3") == 13
    assert locate_snippet(entry, "something else entirely") is None
    assert default_snippet(entry) == ("MAX_RETRIES = 3", 13)


def test_normalize():
    assert normalize("  We  moved\nto  HTTPX ") == "we moved to httpx"
    assert normalize("don’t") == "don't"


def test_process_files_caps_and_skips():
    big = "@@ -1,300 +1,300 @@\n" + "\n".join(f"+line {i}" for i in range(300))
    raw = [
        {
            "filename": "uv.lock",
            "status": "modified",
            "additions": 5,
            "deletions": 5,
            "patch": "@@ x",
        },
        {
            "filename": "src/a.py",
            "status": "modified",
            "additions": 300,
            "deletions": 0,
            "patch": big,
        },
        {"filename": "img.png", "status": "added", "additions": 0, "deletions": 0},
    ] + [
        {
            "filename": f"src/m{i}.py",
            "status": "added",
            "additions": 300,
            "deletions": 0,
            "patch": big,
        }
        for i in range(10)
    ]
    files, truncated = process_files(raw)
    by = {f.path: f for f in files}
    assert by["uv.lock"].patch is None and by["uv.lock"].patch_skipped == "lockfile"
    assert len(by["src/a.py"].patch.splitlines()) == MAX_LINES_PER_FILE
    assert by["src/a.py"].patch_truncated
    assert by["img.png"].patch is None and not by["img.png"].patch_truncated
    total = sum(len(f.patch.splitlines()) for f in files if f.patch)
    assert total == MAX_DIFF_LINES and truncated
    assert any(f.patch_skipped == "total diff budget exhausted" for f in files)
