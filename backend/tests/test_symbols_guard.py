from app.analysis.evidence import build_evidence_index
from app.analysis.symbols import extract_symbols, match_decisions, paths_and_symbols
from app.models.pr import ChangedFile
from tests.fixtures import make_pr

WEBHOOK_PATCH = """\
@@ -1,8 +1,9 @@
 import httpx

-WEBHOOK_MAX_RETRIES = 5
+WEBHOOK_MAX_RETRIES = 3
 WEBHOOK_TIMEOUT_SECONDS = 15

 def send(event):
-    return httpx.post(URL, json=event)
+    return httpx.post(URL, json=event, timeout=WEBHOOK_TIMEOUT_SECONDS)
"""


def test_extract_symbols_constants_kwargs_config_and_deps():
    assert extract_symbols("app/payments/webhook.py", WEBHOOK_PATCH) == [
        "WEBHOOK_MAX_RETRIES",
        "timeout",
    ]
    yaml_patch = "@@ -1,3 +1,3 @@\n jobs:\n-  max-parallel: 2\n+  max-parallel: 4\n"
    assert extract_symbols("deploy/app.yaml", yaml_patch) == ["max-parallel"]
    req = "@@ -1 +1 @@\n-requests>=2.31\n+httpx[http2]>=0.27\n"
    assert extract_symbols("requirements.txt", req) == ["requests", "httpx"]
    pyproject = '@@ -1,2 +1,2 @@\n dependencies = [\n-    "requests>=2.31",\n+    "httpx>=0.27",\n'
    assert extract_symbols("pyproject.toml", pyproject) == ["requests", "httpx"]


def test_extract_symbols_ignores_comments_and_plain_locals():
    patch = "@@ -1,3 +1,3 @@\n-# MAX_RETRIES was 5\n+    i = 2\n+    return value\n"
    assert extract_symbols("svc/x.py", patch) == []


def test_symbols_for_snippet_narrows_to_the_question_line():
    from app.analysis.symbols import symbols_for_snippet

    hunk = ["WEBHOOK_MAX_RETRIES", "WEBHOOK_TIMEOUT_SECONDS", "timeout"]
    assert symbols_for_snippet("app/w.py", "WEBHOOK_MAX_RETRIES = 3", hunk) == [
        "WEBHOOK_MAX_RETRIES"
    ]
    assert symbols_for_snippet("app/w.py", "return x", hunk) == hunk  # falls back


def test_paths_and_symbols_from_evidence():
    ev = build_evidence_index(make_pr())
    paths, symbols = paths_and_symbols(["diff:payments/webhook.py#0", "diff:pyproject.toml#0"], ev)
    assert paths == ["payments/webhook.py", "pyproject.toml"]
    assert symbols[:1] == ["MAX_RETRIES"] and "httpx" in symbols and "requests" in symbols


DECISION = {"number": 1, "paths": ["app/payments/webhook.py"], "symbols": ["WEBHOOK_MAX_RETRIES"]}


def _file(patch: str, path: str = "app/payments/webhook.py") -> ChangedFile:
    return ChangedFile(path=path, status="modified", patch=patch)


def test_guard_matches_changed_symbol_line():
    patch = (
        "@@ -1,4 +1,4 @@\n import httpx\n \n-WEBHOOK_MAX_RETRIES = 3\n+WEBHOOK_MAX_RETRIES = 5\n"
    )
    [m] = match_decisions([DECISION], [_file(patch)])
    assert (m.decision_number, m.path, m.line, m.symbol) == (
        1,
        "app/payments/webhook.py",
        3,
        "WEBHOOK_MAX_RETRIES",
    )
    assert m.snippet == "-WEBHOOK_MAX_RETRIES = 3\n+WEBHOOK_MAX_RETRIES = 5"


def test_guard_matches_within_three_lines_of_a_change():
    patch = (
        "@@ -10,6 +10,6 @@\n WEBHOOK_MAX_RETRIES = 3\n a = 1\n b = 2\n"
        "-WEBHOOK_TIMEOUT_SECONDS = 15\n+WEBHOOK_TIMEOUT_SECONDS = 20\n c = 3\n"
    )
    [m] = match_decisions([DECISION], [_file(patch)])
    assert m.line == 13 and m.symbol == "WEBHOOK_MAX_RETRIES"


def test_guard_no_match_outside_window_other_file_or_substring():
    far = "@@ -1,9 +1,9 @@\n WEBHOOK_MAX_RETRIES = 3\n a\n b\n c\n d\n-x = 1\n+x = 2\n"
    assert match_decisions([DECISION], [_file(far)]) == []
    same_line_other_file = "@@ -1 +1 @@\n-WEBHOOK_MAX_RETRIES = 3\n+WEBHOOK_MAX_RETRIES = 5\n"
    assert match_decisions([DECISION], [_file(same_line_other_file, "app/other.py")]) == []
    substring = "@@ -1 +1 @@\n-OLD_WEBHOOK_MAX_RETRIES_X = 1\n+OLD_WEBHOOK_MAX_RETRIES_X = 2\n"
    assert match_decisions([DECISION], [_file(substring)]) == []


def test_guard_decision_without_symbols_matches_any_change_in_path():
    d = {"number": 2, "paths": ["Dockerfile"], "symbols": []}
    patch = "@@ -1,2 +1,2 @@\n-FROM python:3.11\n+FROM python:3.12\n"
    [m] = match_decisions([d], [_file(patch, "Dockerfile")])
    assert m.decision_number == 2 and m.line == 1 and m.symbol is None


def test_guard_pure_deletion_gets_next_line():
    patch = "@@ -1,3 +1,2 @@\n a = 1\n-WEBHOOK_MAX_RETRIES = 3\n b = 2\n"
    [m] = match_decisions([DECISION], [_file(patch)])
    assert m.line == 2 and m.snippet == "-WEBHOOK_MAX_RETRIES = 3"
