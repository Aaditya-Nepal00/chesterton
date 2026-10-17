"""Fetch a merged PR's full context from GitHub (read-only)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.analysis.signals import file_priority, is_generated, is_lockfile
from app.config import get_settings
from app.github.auth import AppAuth, TokenSelector
from app.github.client import CACHE_DIR, GitHubClient
from app.models.pr import ChangedFile, Comment, Commit, LinkedIssue, PRContext

MAX_LINES_PER_FILE = 200
MAX_DIFF_LINES = 1500

_ACTOR = "author { login __typename }"
_COMMENT_FIELDS = f"databaseId body url createdAt {_ACTOR}"

PR_QUERY = f"""
query($owner: String!, $name: String!, $number: Int!) {{
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $number) {{
      number title body url merged mergedAt baseRefName headRefName state
      {_ACTOR}
      labels(first: 20) {{ nodes {{ name }} }}
      commits(first: 100) {{
        totalCount
        nodes {{ commit {{ oid message author {{ name user {{ login }} }} }} }}
      }}
      reviews(first: 50) {{ nodes {{ {_COMMENT_FIELDS} state }} }}
      reviewThreads(first: 50) {{
        nodes {{ comments(first: 30) {{ nodes {{ {_COMMENT_FIELDS} path line originalLine }} }} }}
      }}
      comments(first: 100) {{ nodes {{ {_COMMENT_FIELDS} }} }}
      closingIssuesReferences(first: 10) {{
        nodes {{
          number title body url
          comments(first: 20) {{ nodes {{ {_COMMENT_FIELDS} }} }}
        }}
      }}
    }}
  }}
}}
"""

MERGED_PRS_QUERY = """
query($owner: String!, $name: String!, $first: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: MERGED, first: $first, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number title url mergedAt changedFiles additions deletions headRefOid
        author { login __typename }
        comments { totalCount }
        reviewThreads { totalCount }
      }
    }
  }
}
"""


def _split_repo(repo: str) -> tuple[str, str]:
    owner, _, name = repo.partition("/")
    if not owner or not name:
        raise ValueError(f"repo must be owner/name, got {repo!r}")
    return owner, name


def _login(actor: dict | None) -> str | None:
    return (actor or {}).get("login")


def _is_bot(actor: dict | None) -> bool:
    actor = actor or {}
    return actor.get("__typename") == "Bot" or (actor.get("login") or "").endswith("[bot]")


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def process_files(raw_files: list[dict]) -> tuple[list[ChangedFile], bool]:
    """Apply lockfile/generated skipping and the per-file and total line caps.

    The total budget is spent on the most decision-relevant files first
    (manifests/config/migrations, then source, then tests, then docs).
    """
    files = [
        ChangedFile(
            path=f["filename"],
            status=f.get("status", "modified"),
            additions=f.get("additions", 0),
            deletions=f.get("deletions", 0),
        )
        for f in raw_files
    ]
    patches = {f["filename"]: f.get("patch") for f in raw_files}
    budget = MAX_DIFF_LINES
    any_truncated = False
    order = sorted(range(len(files)), key=lambda i: (file_priority(files[i].path), i))
    for i in order:
        cf = files[i]
        patch = patches.get(cf.path)
        if is_lockfile(cf.path):
            cf.patch_skipped = "lockfile"
            continue
        if is_generated(cf.path):
            cf.patch_skipped = "generated or vendored"
            continue
        if patch is None:
            if cf.additions or cf.deletions:
                cf.patch_skipped = "no patch from GitHub (binary or too large)"
                cf.patch_truncated = True
                any_truncated = True
            continue
        if budget <= 0:
            cf.patch_skipped = "total diff budget exhausted"
            cf.patch_truncated = True
            any_truncated = True
            continue
        lines = patch.splitlines()
        cap = min(MAX_LINES_PER_FILE, budget)
        if len(lines) > cap:
            lines = lines[:cap]
            cf.patch_truncated = True
            any_truncated = True
        cf.patch = "\n".join(lines)
        budget -= len(lines)
    return files, any_truncated


def _make_selector() -> TokenSelector:
    settings = get_settings()
    return TokenSelector(settings, AppAuth.from_settings(settings))


async def client_for(repo: str, *, refresh: bool = False) -> GitHubClient:
    """Read client for
    epo. The disk cache is only used for read-only public repos
        outside Actions; the writable repo changes under us (new pushes, new comments)."""
    settings = get_settings()
    selector = _make_selector()
    token = await selector.token_for(repo)
    cache = not (selector.uses_app(repo) or settings.github_actions)
    return GitHubClient(token, refresh=refresh, cache_dir=CACHE_DIR if cache else None)


async def fetch_pr_files(
    gh: GitHubClient, repo: str, number: int
) -> tuple[list[ChangedFile], bool]:
    raw = await gh.rest_get_paginated(f"/repos/{repo}/pulls/{number}/files")
    return process_files(raw)


async def fetch_pr_context(
    repo: str,
    number: int,
    *,
    refresh: bool = False,
    gh: GitHubClient | None = None,
    require_merged: bool = True,
) -> PRContext:
    owner, name = _split_repo(repo)
    own = gh is None
    gh = gh or await client_for(repo, refresh=refresh)
    try:
        data = await gh.graphql(PR_QUERY, {"owner": owner, "name": name, "number": number})
        pr = (data.get("repository") or {}).get("pullRequest")
        if not pr:
            raise ValueError(f"{repo}#{number} not found")
        if require_merged and not pr.get("merged"):
            raise ValueError(f"{repo}#{number} is not merged")
        files, truncated = await fetch_pr_files(gh, repo, number)
    finally:
        if own:
            await gh.aclose()

    comments: list[Comment] = []

    def add_comment(node: dict, kind: str, **extra) -> None:
        body = (node.get("body") or "").strip()
        if not body or node.get("databaseId") is None:
            return
        comments.append(
            Comment(
                id=str(node["databaseId"]),
                kind=kind,
                author=_login(node.get("author")),
                body=body,
                url=node.get("url"),
                created_at=_dt(node.get("createdAt")),
                **extra,
            )
        )

    for review in pr["reviews"]["nodes"]:
        add_comment(review, "review")
    for thread in pr["reviewThreads"]["nodes"]:
        for c in thread["comments"]["nodes"]:
            add_comment(
                c, "review_comment", path=c.get("path"), line=c.get("line") or c.get("originalLine")
            )
    for c in pr["comments"]["nodes"]:
        add_comment(c, "issue_comment")

    issues: list[LinkedIssue] = []
    for issue in pr["closingIssuesReferences"]["nodes"]:
        issues.append(
            LinkedIssue(
                number=issue["number"],
                title=issue["title"],
                body=issue.get("body") or "",
                url=issue.get("url"),
            )
        )
        for c in issue["comments"]["nodes"]:
            add_comment(c, "linked_issue_comment")

    commits = [
        Commit(
            sha=n["commit"]["oid"],
            message=n["commit"]["message"],
            author=((n["commit"].get("author") or {}).get("user") or {}).get("login")
            or (n["commit"].get("author") or {}).get("name"),
        )
        for n in pr["commits"]["nodes"]
    ]

    return PRContext(
        repo=repo,
        number=pr["number"],
        title=pr["title"],
        url=pr["url"],
        author=_login(pr.get("author")),
        author_is_bot=_is_bot(pr.get("author")),
        merged_at=_dt(pr.get("mergedAt")),
        base_branch=pr.get("baseRefName"),
        head_ref=pr.get("headRefName"),
        state=pr.get("state"),
        body=pr.get("body") or "",
        labels=[label["name"] for label in pr["labels"]["nodes"]],
        files=files,
        commits=commits,
        comments=comments,
        linked_issues=issues,
        diff_truncated=truncated,
        fetched_at=datetime.now(UTC),
    )


async def list_merged_prs(
    repo: str, limit: int = 30, *, refresh: bool = False, gh: GitHubClient | None = None
) -> list[dict]:
    """Recent merged PRs, newest merge first.

    GraphQL can't order by mergedAt, so this over-fetches by updatedAt and sorts.
    """
    owner, name = _split_repo(repo)
    own = gh is None
    gh = gh or await client_for(repo, refresh=refresh)
    try:
        data = await gh.graphql(
            MERGED_PRS_QUERY, {"owner": owner, "name": name, "first": min(100, limit * 2)}
        )
    finally:
        if own:
            await gh.aclose()
    nodes = data["repository"]["pullRequests"]["nodes"]
    nodes.sort(key=lambda n: n["mergedAt"], reverse=True)
    return [
        {
            "number": n["number"],
            "title": n["title"],
            "url": n["url"],
            "author": _login(n.get("author")),
            "author_is_bot": _is_bot(n.get("author")),
            "merged_at": n["mergedAt"],
            "head_sha": n.get("headRefOid"),
            "changed_files": n["changedFiles"],
            "additions": n["additions"],
            "deletions": n["deletions"],
            "comment_count": n["comments"]["totalCount"] + n["reviewThreads"]["totalCount"],
        }
        for n in nodes[:limit]
    ]
