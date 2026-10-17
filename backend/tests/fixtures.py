"""Small hand-written PR fixtures shared by the analysis tests."""

from datetime import UTC, datetime

from app.models.pr import ChangedFile, Comment, Commit, LinkedIssue, PRContext

RETRY_PATCH = """\
@@ -10,7 +10,7 @@ import httpx

 log = logging.getLogger(__name__)

-MAX_RETRIES = 5
+MAX_RETRIES = 3
 BACKOFF_SECONDS = 2

 def send(event):
@@ -40,3 +40,4 @@ def send(event):
     client = httpx.Client(timeout=10)
-    return client.post(URL, json=event)
+    # Keep the timeout short: the provider drops connections after 15s.
+    return client.post(URL, json=event, timeout=5)
"""

PYPROJECT_PATCH = """\
@@ -5,6 +5,6 @@ dependencies = [
-    "requests>=2.31",
+    "httpx>=0.27",
 ]
"""


def make_pr(**overrides) -> PRContext:
    base = dict(
        repo="acme/payments",
        number=42,
        title="Tune webhook retries",
        url="https://github.com/acme/payments/pull/42",
        author="dana",
        body="Tweak retries for the payment webhook.\n\nAlso swaps requests for httpx.",
        files=[
            ChangedFile(
                path="payments/webhook.py",
                status="modified",
                additions=3,
                deletions=2,
                patch=RETRY_PATCH,
            ),
            ChangedFile(
                path="pyproject.toml",
                status="modified",
                additions=1,
                deletions=1,
                patch=PYPROJECT_PATCH,
            ),
            ChangedFile(
                path="uv.lock",
                status="modified",
                additions=200,
                deletions=180,
                patch_skipped="lockfile",
            ),
        ],
        commits=[Commit(sha="abcdef1234567", message="tweak retries", author="dana")],
        comments=[
            Comment(
                id="9001",
                kind="review_comment",
                author="lee",
                body="We moved to httpx because requests has no async client and we need one.",
                path="pyproject.toml",
                line=6,
            ),
            Comment(id="9002", kind="issue_comment", author="dana", body="LGTM, merging."),
        ],
        linked_issues=[LinkedIssue(number=7, title="Webhook storms", body="Retries pile up.")],
        fetched_at=datetime(2026, 10, 17, tzinfo=UTC),
    )
    base.update(overrides)
    return PRContext(**base)
