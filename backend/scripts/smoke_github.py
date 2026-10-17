"""Smoke test: GitHub access, READ-ONLY. Posts nothing; never prints token values."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
import jwt

from app.config import REPO_ROOT, get_settings

API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

MERGED_PRS_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: MERGED, first: 30, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number
        title
        author { login }
        mergedAt
        reviewThreads { totalCount }
        comments { totalCount }
      }
    }
  }
  rateLimit { limit remaining cost resetAt }
}
"""


def part_a_graphql(token: str | None) -> bool:
    print("== a. GraphQL (GITHUB_TOKEN): last 5 merged PRs of fastapi/fastapi ==")
    if not token:
        print("SKIPPED: GITHUB_TOKEN not set")
        return True
    resp = httpx.post(
        f"{API}/graphql",
        json={"query": MERGED_PRS_QUERY, "variables": {"owner": "fastapi", "name": "fastapi"}},
        headers={**HEADERS, "Authorization": f"bearer {token}"},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"FAIL HTTP {resp.status_code}: {resp.text[:300]}")
        return False
    body = resp.json()
    if body.get("errors"):
        print(f"FAIL GraphQL errors: {body['errors']}")
        return False
    nodes = body["data"]["repository"]["pullRequests"]["nodes"]
    # GraphQL can't order by mergedAt, so fetch recent-by-update and sort locally.
    latest = sorted(nodes, key=lambda n: n["mergedAt"], reverse=True)[:5]
    for pr in latest:
        author = (pr["author"] or {}).get("login", "ghost")
        print(
            f"  #{pr['number']:<6} merged {pr['mergedAt']}  @{author:<20} "
            f"threads={pr['reviewThreads']['totalCount']:<3} "
            f"comments={pr['comments']['totalCount']:<3} {pr['title'][:70]}"
        )
    rl = body["data"]["rateLimit"]
    print(
        f"  rateLimit: remaining={rl['remaining']}/{rl['limit']} "
        f"cost={rl['cost']} resetAt={rl['resetAt']}"
    )
    return True


def make_app_jwt(app_id: str, private_key: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"iat": now - 60, "exp": now + 9 * 60, "iss": str(app_id)}, private_key, algorithm="RS256"
    )


def part_b_app(app_id: str | None, key_path: str | None, demo_repo: str | None) -> bool:
    print("\n== b. GitHub App: installations + installation token ==")
    for var, value in (
        ("GITHUB_APP_ID", app_id),
        ("GITHUB_APP_PRIVATE_KEY_PATH", key_path),
        ("GITHUB_DEMO_REPO", demo_repo),
    ):
        if not value:
            print(f"SKIPPED: {var} not set")
            return True
    path = Path(key_path).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.is_file():
        print(f"FAIL: private key file not found at {path}")
        return False

    app_jwt = make_app_jwt(app_id, path.read_text())
    with httpx.Client(base_url=API, headers=HEADERS, timeout=30) as gh:
        auth = {"Authorization": f"Bearer {app_jwt}"}
        r = gh.get("/app", headers=auth)
        if r.status_code != 200:
            print(f"FAIL GET /app HTTP {r.status_code}: {r.text[:300]}")
            return False
        print(f"  App: {r.json().get('slug')} (id {r.json().get('id')})")

        r = gh.get("/app/installations", headers=auth)
        if r.status_code != 200:
            print(f"FAIL GET /app/installations HTTP {r.status_code}: {r.text[:300]}")
            return False
        installs = r.json()
        print(f"  Installations: {len(installs)}")
        for inst in installs:
            print(
                f"    id={inst['id']} account={inst['account']['login']} "
                f"repository_selection={inst.get('repository_selection')}"
            )

        r = gh.get(f"/repos/{demo_repo}/installation", headers=auth)
        if r.status_code != 200:
            print(f"FAIL app not installed on {demo_repo}: HTTP {r.status_code}: {r.text[:300]}")
            return False
        installation_id = r.json()["id"]

        # Creating an installation token is a credential exchange; nothing is posted to repos.
        r = gh.post(f"/app/installations/{installation_id}/access_tokens", headers=auth)
        if r.status_code != 201:
            print(f"FAIL create installation token HTTP {r.status_code}: {r.text[:300]}")
            return False
        tok = r.json()
        print(f"  Installation token for {demo_repo}: obtained (expires {tok['expires_at']})")
        print(f"  Permissions granted: {tok.get('permissions')}")

        inst_auth = {"Authorization": f"Bearer {tok['token']}"}
        r = gh.get(f"/repos/{demo_repo}", headers=inst_auth)
        if r.status_code != 200:
            print(f"FAIL GET /repos/{demo_repo} HTTP {r.status_code}: {r.text[:300]}")
            return False
        repo = r.json()
        print(
            f"  Repo: {repo['full_name']} private={repo['private']} "
            f"default_branch={repo['default_branch']}"
        )
        r = gh.get(
            f"/repos/{demo_repo}/pulls", params={"state": "all", "per_page": 10}, headers=inst_auth
        )
        if r.status_code != 200:
            print(f"FAIL list PRs HTTP {r.status_code}: {r.text[:300]}")
            return False
        pulls = r.json()
        print(f"  PRs (state=all, up to 10): {len(pulls)}")
        for pr in pulls:
            print(f"    #{pr['number']} [{pr['state']}] {pr['title'][:70]}")
    return True


def main() -> int:
    s = get_settings()
    ok_a = part_a_graphql(s.github_token)
    ok_b = part_b_app(s.github_app_id, s.github_app_private_key_path, s.github_demo_repo)
    return 0 if ok_a and ok_b else 1


if __name__ == "__main__":
    sys.exit(main())
