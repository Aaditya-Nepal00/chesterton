from datetime import date

import yaml

from app.github.markers import build_marker, has_marker, marker_prefix, parse_markers
from app.models.records import DecisionRecord, QuestionItem
from app.render.decision_record import (
    allocate_numbers,
    numbers_in_dir,
    record_pr_title,
    render_record,
    reply_text,
    slugify,
)

# --- markers ---


def test_build_and_parse_markers():
    m = build_marker("questions", pr=12, run_id="20261017T090240Z-6e9741")
    assert m == "<!-- chesterton:questions pr=12 run_id=20261017T090240Z-6e9741 -->"
    assert parse_markers(f"{m}\nQuick question") == [
        {"kind": "questions", "pr": "12", "run_id": "20261017T090240Z-6e9741"}
    ]
    assert build_marker("guard", pr=3) == "<!-- chesterton:guard pr=3 -->"
    assert m.startswith(marker_prefix("questions", 12))


def test_has_marker_checks_kind_and_pr():
    body = "hello\n<!-- chesterton:guard pr=7 -->\n"
    assert has_marker(body, "guard", 7)
    assert not has_marker(body, "guard", 8)
    assert not has_marker(body, "questions", 7)
    assert has_marker(body, "guard")
    assert parse_markers("<!-- not ours -->") == [] and parse_markers(None) == []


# --- numbering + slugs ---


def test_slugify():
    assert slugify("Webhook retry cap of 3") == "webhook-retry-cap-of-3"
    assert slugify("Use `httpx` instead of requests!") == "use-httpx-instead-of-requests"
    assert slugify("Café — naïve ümlaut") == "cafe-naive-umlaut"
    assert slugify("!!!") == "decision"
    long = slugify("a very long title " * 10)
    assert len(long) <= 50 and not long.endswith("-")


def test_numbers_and_allocation():
    names = ["0001-retry-cap.md", "0003-pool.md", "README.md", "12-x.md", "template.md"]
    assert numbers_in_dir(names) == {1, 3}
    assert allocate_numbers({1, 3} | {4}, 2) == [5, 6]
    assert allocate_numbers(set(), 1) == [1]


def _rec(**kw) -> DecisionRecord:
    base = dict(
        number=1,
        id="0001",
        slug="webhook-retry-cap",
        file_path="decisions/0001-webhook-retry-cap.md",
        title="Webhook retry cap of 3",
        repo="acme/demo",
        owner="dana",
        paths=["app/payments/webhook.py"],
        symbols=["WEBHOOK_MAX_RETRIES"],
        source_pr=1,
        source_pr_url="https://github.com/acme/demo/pull/1",
        source_pr_title="Add webhook",
        answer_url="https://github.com/acme/demo/pull/1#issuecomment-9",
        answer_comment_id=9,
        recorded=date(2026, 10, 17).isoformat(),
        qid="Q1",
        question="Why 3?",
        decision="Cap WEBHOOK_MAX_RETRIES at 3.",
        reason="The PSP blocks us after 3 retries.",
        reason_quote="Stripe blocks the endpoint after 3 failed retries",
        safe_to_change_when="when we move off Stripe",
        evidence_url="https://github.com/acme/demo/pull/1/files#diff-abcR4",
        evidence_path="app/payments/webhook.py",
        evidence_line=4,
    )
    base.update(kw)
    return DecisionRecord(**base)


def test_render_record_front_matter_and_sections():
    md = render_record(_rec(title='Cap: "retries" at 3', alternatives=["5 retries"]))
    front = yaml.safe_load(md.split("---\n")[1])
    assert front["id"] == "0001" and front["status"] == "recorded"
    assert front["title"] == 'Cap: "retries" at 3'
    assert front["owner"] == "@dana"
    assert front["paths"] == ["app/payments/webhook.py"]
    assert front["symbols"] == ["WEBHOOK_MAX_RETRIES"]
    assert front["recorded"] == date(2026, 10, 17)
    for section in (
        "## Decision",
        "## Why",
        "## Alternatives considered",
        "## Safe to change when",
        "## Evidence",
    ):
        assert section in md
    assert "> Stripe blocks the endpoint after 3 failed retries" in md
    assert "[`app/payments/webhook.py:4`]" in md


def test_record_without_alternatives_or_condition():
    md = render_record(_rec(safe_to_change_when="", alternatives=[]))
    assert "## Alternatives considered" not in md
    assert "Not stated. Ask the owner before changing it." in md


def test_pr_title_and_reply():
    r1, r2 = _rec(), _rec(number=2, id="0002", file_path="decisions/0002-x.md", qid="Q2")
    assert record_pr_title([r1]) == "Decision record 0001: Webhook retry cap of 3"
    assert record_pr_title([r1, r2]) == "Decision records 0001–0002"
    still = [QuestionItem(qid="Q3", decision_id="D3", text="Why 15s?", evidence_id="diff:x#0")]
    text = reply_text([r1], 5, still, pr=1, answer_comment_id=9)
    assert text.splitlines()[0] == "<!-- chesterton:recorded pr=1 comment=9 -->"
    assert "Recorded as `decisions/0001-webhook-retry-cap.md` in #5. Thanks!" in text
    assert "Q3: Why 15s?" in text
