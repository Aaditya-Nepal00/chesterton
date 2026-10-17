from app.analysis.evidence import build_evidence_index
from app.analysis.verify import verify_analysis, verify_decisions, verify_questions
from app.models.analysis import Decision, Question, RationaleCheck
from app.render.pr_comment import FOOTER, render_comment
from tests.fixtures import make_pr

EV = build_evidence_index(make_pr())
PR_URL = make_pr().url


def _d(id_, *evidence, category="config_constant"):
    return Decision(
        id=id_,
        summary=f"decision {id_}",
        category=category,
        diff_evidence=list(evidence),
        why_it_matters="x",
        confidence=0.8,
    )


D1 = _d("D1", "diff:payments/webhook.py#0")
D2 = _d("D2", "diff:pyproject.toml#0", category="dependency")


def test_decisions_with_unknown_evidence_are_dropped_or_trimmed():
    kept, notes = verify_decisions(
        [
            D1,
            _d("D3", "diff:nope.py#0"),
            _d("D4", "diff:pyproject.toml#0", "diff:ghost#9", "pr:body"),
        ],
        EV,
    )
    assert [d.id for d in kept] == ["D1", "D4"]
    assert kept[1].diff_evidence == ["diff:pyproject.toml#0"]
    assert any("D3 dropped" in n for n in notes)
    assert any("D4: removed unknown evidence ids" in n for n in notes)


def test_found_quote_must_be_verbatim_in_cited_evidence():
    checks = [
        RationaleCheck(  # D1: claims found, but the quote is a paraphrase -> downgraded
            decision_id="D1",
            status="found",
            reason_quote="Retries were causing webhook storms",
            evidence_ids=["issue:7"],
        ),
        RationaleCheck(  # D2: verbatim modulo whitespace/case -> kept
            decision_id="D2",
            status="found",
            reason_quote="we moved to httpx because requests has  no async client",
            evidence_ids=["comment:9001"],
        ),
    ]
    out = verify_analysis([D1, D2], checks, EV)
    status = {r.decision_id: r.status for r in out.rationale}
    assert status == {"D1": "missing", "D2": "found"}
    assert out.gaps == ["D1"]
    assert any("rationale for D1 not found in cited evidence" in n for n in out.notes)


def test_quote_in_uncited_evidence_or_too_short_is_downgraded():
    checks = [
        RationaleCheck(
            decision_id="D1",
            status="partial",
            reason_quote="Webhook storms\n\nRetries pile up.",
            evidence_ids=["pr:body"],
        ),  # text is in issue:7, not pr:body
        RationaleCheck(
            decision_id="D2",
            status="found",
            reason_quote="tweak retries",
            evidence_ids=["commit:abcdef1"],
        ),  # verbatim but too short to be a reason
    ]
    out = verify_analysis([D1, D2], checks, EV)
    assert [r.status for r in out.rationale] == ["missing", "missing"]
    assert all(r.reason_quote is None for r in out.rationale)


def test_partial_is_a_gap_after_missing_and_missing_checks_are_filled():
    checks = [
        RationaleCheck(
            decision_id="D2",
            status="partial",
            reason_quote="storms Retries pile up.",
            evidence_ids=["issue:7"],
        ),
        RationaleCheck(decision_id="D9", status="missing"),
    ]
    out = verify_analysis([D2, D1], checks, EV)
    assert out.gaps == ["D1", "D2"]  # missing first, then partial
    assert any("no rationale check returned for D1" in n for n in out.notes)
    assert any("unknown decision D9" in n for n in out.notes)


def test_questions_dropped_unless_verified_gap_and_snippet_resolved():
    qs = [
        Question(
            decision_id="D1",
            text="Why 3 retries?",
            evidence_id="diff:payments/webhook.py#0",
            snippet="+MAX_RETRIES = 3",
        ),
        Question(
            decision_id="D2",
            text="Why httpx?",
            evidence_id="diff:pyproject.toml#0",
            snippet='"httpx>=0.27",',
        ),
        Question(
            decision_id="D1", text="dup", evidence_id="diff:payments/webhook.py#0", snippet="x"
        ),
    ]
    rendered, notes = verify_questions(qs, ["D1"], [D1, D2], EV, PR_URL)
    assert [q.decision_id for q in rendered] == ["D1"]
    q = rendered[0]
    assert (q.path, q.line, q.snippet) == ("payments/webhook.py", 13, "MAX_RETRIES = 3")
    assert q.url.endswith("R13")
    assert any("D2 dropped: not a verified gap" in n for n in notes)


def test_question_with_bad_evidence_and_snippet_falls_back_to_hunk():
    qs = [Question(decision_id="D1", text="Why?", evidence_id="diff:made/up#3", snippet="nope")]
    rendered, notes = verify_questions(qs, ["D1"], [D1], EV, PR_URL)
    assert rendered[0].evidence_id == "diff:payments/webhook.py#0"
    assert rendered[0].snippet == "MAX_RETRIES = 3"
    assert len(notes) == 2


def test_render_comment_marker_and_footer():
    qs = [
        Question(
            decision_id="D1",
            text="Why 3 retries in `payments/webhook.py`?",
            evidence_id="diff:payments/webhook.py#0",
            snippet="MAX_RETRIES = 3",
        )
    ]
    rendered, _ = verify_questions(qs, ["D1"], [D1], EV, PR_URL)
    md = render_comment(rendered, pr=42, run_id="r123")
    assert md.splitlines()[0] == "<!-- chesterton:questions pr=42 run_id=r123 -->"
    assert md.splitlines()[1] == "Quick question before this context fades:"
    assert "1. Why 3 retries" in md
    assert "payments/webhook.py:13" in md and "```python" in md
    assert md.rstrip().endswith(FOOTER)
    assert render_comment([], pr=42, run_id="r") is None


def test_self_evident_needs_a_note_but_no_quote_and_is_not_a_gap():
    checks = [
        RationaleCheck(
            decision_id="D1",
            status="self_evident",
            note="Python 3.8 reached end of life in October 2024.",
        ),
        RationaleCheck(decision_id="D2", status="self_evident", note="obvious"),
    ]
    out = verify_analysis([D1, D2], checks, EV)
    assert [r.status for r in out.rationale] == ["self_evident", "missing"]
    assert out.gaps == ["D2"]
    assert any("self_evident for D2 has no explanatory note" in n for n in out.notes)


def test_self_evident_with_bogus_quote_keeps_status_but_drops_quote():
    checks = [
        RationaleCheck(
            decision_id="D1",
            status="self_evident",
            reason_quote="not in any text here",
            evidence_ids=["pr:body"],
            note="Security fix for CVE-2026-1234 linked.",
        ),
    ]
    out = verify_analysis([D1], checks, EV)
    assert out.rationale[0].status == "self_evident"
    assert out.rationale[0].reason_quote is None and out.gaps == []
