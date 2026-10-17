import pytest

from app.action import route_event
from app.analysis.answers import clean_reply, verify_answers
from app.config import Settings
from app.github.auth import AppAuth, normalize_pem
from app.models.records import ParsedAnswer

# --- verify_answer ---

REPLY = (
    "> Why WEBHOOK_MAX_RETRIES = 3 instead of 5?\n"
    "Stripe disables the endpoint after 3 consecutive failures, so more retries just "
    "get us blocked. Safe to raise once we move to the queue-based sender."
)


def _a(qid="Q1", **kw):
    base = dict(
        qid=qid,
        answered=True,
        title="Webhook retry cap",
        decision="Cap retries at 3.",
        reason="Stripe blocks after 3 failures.",
        reason_quote="Stripe disables the endpoint after 3 consecutive failures",
    )
    base.update(kw)
    return ParsedAnswer(**base)


def test_verify_answer_accepts_verbatim_quote_case_and_whitespace_insensitive():
    usable, notes = verify_answers(
        [_a(reason_quote="stripe disables the endpoint   after 3 consecutive failures")],
        ["Q1"],
        REPLY,
    )
    assert [a.qid for a in usable] == ["Q1"] and notes == []


def test_verify_answer_drops_paraphrase_unknown_dupes_and_unanswered():
    usable, notes = verify_answers(
        [
            _a("Q1", reason_quote="Stripe blocks us after three failures"),  # paraphrase
            _a("Q9"),  # not an open question
            _a("Q2", answered=False, reason_quote=""),
            _a("Q2"),  # duplicate qid
        ],
        ["Q1", "Q2"],
        REPLY,
    )
    assert usable == []
    assert any("Q1 not found verbatim" in n for n in notes)
    assert any("Q9 dropped" in n for n in notes)
    assert any("Q2 not answered" in n for n in notes)
    assert any("duplicate answer for Q2" in n for n in notes)


def test_quote_from_quoted_question_does_not_count():
    usable, notes = verify_answers(
        [_a(reason_quote="Why WEBHOOK_MAX_RETRIES = 3 instead of 5?")], ["Q1"], REPLY
    )
    assert usable == [] and "not found verbatim" in notes[0]
    assert clean_reply(REPLY).startswith("Stripe disables")


# --- dispatcher routing ---


def _pr_event(action, merged=False, user_type="User", login="dana", ref="feature/x"):
    return {
        "action": action,
        "repository": {"full_name": "acme/demo"},
        "pull_request": {
            "number": 7,
            "merged": merged,
            "user": {"login": login, "type": user_type},
            "head": {"ref": ref},
        },
    }


def _comment_event(
    on_pr=True, commenter="dana", user_type="User", body="Because X.", pr_author="dana"
):
    issue = {"number": 7, "user": {"login": pr_author}}
    if on_pr:
        issue["pull_request"] = {"url": "https://api.github.com/repos/acme/demo/pulls/7"}
    return {
        "action": "created",
        "repository": {"full_name": "acme/demo"},
        "issue": issue,
        "comment": {"id": 99, "body": body, "user": {"login": commenter, "type": user_type}},
    }


@pytest.mark.parametrize(
    "event,kind,reason_part",
    [
        (_pr_event("closed", merged=True), "analyze", "pull_request.closed"),
        (_pr_event("closed", merged=False), None, "closed without merge"),
        (_pr_event("opened"), "guard", "pull_request.opened"),
        (_pr_event("synchronize"), "guard", "pull_request.synchronize"),
        (_pr_event("reopened"), "guard", "pull_request.reopened"),
        (_pr_event("labeled"), None, "ignored action"),
        (_pr_event("closed", merged=True, user_type="Bot", login="dependabot[bot]"), None, "bot"),
        (_pr_event("opened", ref="chesterton/decision-0001"), None, "chesterton/ branch"),
    ],
)
def test_route_pull_request(event, kind, reason_part):
    r = route_event("pull_request", event)
    assert r.kind == kind and reason_part in r.reason and r.repo == "acme/demo" and r.pr == 7


@pytest.mark.parametrize(
    "event,kind,reason_part",
    [
        (_comment_event(), "record", "issue_comment.created"),
        (_comment_event(on_pr=False), None, "not a pull request"),
        (_comment_event(commenter="chesterton-aaditya[bot]", user_type="Bot"), None, "bot"),
        (_comment_event(commenter="lee"), None, "not the PR author"),
        (_comment_event(body="<!-- chesterton:questions pr=7 run_id=x -->"), None, "marker"),
    ],
)
def test_route_issue_comment(event, kind, reason_part):
    r = route_event("issue_comment", event)
    assert r.kind == kind and reason_part in r.reason
    if kind:
        assert (r.pr, r.comment_id) == (7, 99)


def test_route_workflow_dispatch():
    ok = route_event("workflow_dispatch", {"inputs": {"mode": "guard", "pr": "12"}}, "acme/demo")
    assert (ok.kind, ok.pr, ok.repo) == ("guard", 12, "acme/demo")
    assert route_event("workflow_dispatch", {"inputs": {"mode": "x", "pr": "1"}}).kind is None
    assert (
        route_event("workflow_dispatch", {"inputs": {"mode": "analyze", "pr": "abc"}}).kind is None
    )
    assert route_event("push", {}).kind is None


# --- config without .env ---


def test_config_from_env_only(monkeypatch, tmp_path):
    for key in ("GITHUB_DEMO_REPO", "GITHUB_APP_PRIVATE_KEY_PATH", "MODEL_MAIN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Acme/Demo")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "-----BEGIN KEY-----\\nabc\\n-----END KEY-----")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "sa.json"))
    monkeypatch.setenv("MODEL_SCREEN", "gemini-3.5-flash-lite")
    s = Settings(_env_file=tmp_path / "missing.env")  # no .env file at all
    assert s.github_actions and s.github_app_configured
    assert s.write_repos == {"acme/demo"} and s.can_write("acme/DEMO")
    assert not s.can_write("encode/httpx")
    assert s.actions_run_url == "https://github.com/Acme/Demo/actions/runs/42"
    assert s.model_screen == "gemini-3.5-flash-lite" and s.model_main is None
    assert s.google_application_credentials.endswith("sa.json")
    auth = AppAuth.from_settings(s)
    assert auth is not None and auth.app_id == "123"


def test_local_write_repo_is_demo_repo_only(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/else")
    s = Settings(_env_file=tmp_path / "missing.env", github_demo_repo="Me/chesterton-demo")
    assert s.write_repos == {"me/chesterton-demo"} and not s.can_write("someone/else")


def test_normalize_pem():
    assert normalize_pem("a\\nb") == "a\nb\n"
    assert normalize_pem("a\nb\n\n") == "a\nb\n"
