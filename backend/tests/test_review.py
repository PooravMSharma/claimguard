"""Step 11: risk score and band, review deadlines, assignment, the decision panel and query letters."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.claim import Claim
from app.models.decision import Decision
from app.models.enums import ClaimStage, ClaimStatus, RoleName, Severity
from app.models.rules import Finding
from app.models.user import AuditLog, User
from app.review import sla
from app.review.scoring import Item, compute
from tests.test_rules_engine import defaults, headers, process, store, synthetic  # noqa: F401  (fixtures)

# ---------------------------------------------------------------- scoring (no database)


def test_band_follows_the_worst_finding_and_the_score():
    assert compute([], {}).band == "green" and compute([], {}).score == 0
    r = compute([Item("B-05", "info", "open")], {})
    assert (r.band, r.score) == ("green", 5)
    r = compute([Item("C-02", "amber", "open")], {})
    assert (r.band, r.score) == ("amber", 15) and "C-02" in r.reason
    r = compute([Item("B-10", "red", "open"), Item("C-02", "amber", "open")], {})
    assert (r.band, r.score) == ("red", 45) and [p.rule_id for p in r.parts] == ["B-10", "C-02"]


def test_score_counts_each_rule_once_and_caps_at_100():
    two_lines = [Item("B-10", "red", "open", amount_at_stake=Decimal(100)),
                 Item("B-10", "red", "open", amount_at_stake=Decimal(50))]
    r = compute(two_lines, {})
    assert r.score == 30 and r.parts[0].findings == 2 and r.amount_at_stake == Decimal(150)
    many = [Item(rule, "amber", "open") for rule in ("C-01", "C-02", "C-05", "X-01", "X-02", "X-09", "P-03", "F-01")]
    assert compute(many, {}).score == 100


def test_info_findings_alone_can_reach_amber_by_score():
    infos = [Item(rule, "info", "open") for rule in ("B-05", "B-06", "P-05", "B-07", "B-09", "B-08")]
    r = compute(infos, {rule: 5 for rule in ("B-05", "B-06", "P-05", "B-07", "B-09", "B-08")})
    assert r.score == 30 and r.band == "amber" and "Score 30" in r.reason


def test_medical_findings_alone_cannot_make_a_claim_red():
    medical = [Item(rule, "amber", "open", medical=True) for rule in ("M-01", "M-02", "M-03", "M-04", "M-06")]
    r = compute(medical, {})
    assert r.score == 75 and r.band == "amber" and "medical-necessity" in r.reason
    r = compute(medical + [Item("C-01", "amber", "open"), Item("X-09", "amber", "open"),
                           Item("P-03", "amber", "open"), Item("F-01", "amber", "open"),
                           Item("C-05", "amber", "open")], {})
    assert r.band == "red"                                       # non-medical weights alone reach 70


def test_dismissed_findings_no_longer_count():
    r = compute([Item("C-02", "amber", "dismissed"), Item("B-05", "info", "open",
                                                          suggested_deduction=Decimal("250"))], {})
    assert (r.band, r.score, r.dismissed) == ("green", 5, 1) and r.suggested_deduction == Decimal(250)


def test_rule_weights_come_from_the_rules_table():
    assert compute([Item("C-02", "amber", "open")], {"C-02": 40}).score == 40


def test_review_deadlines():
    received = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    assert sla.due_at(ClaimStage.FINAL_CASHLESS, received) == received + timedelta(hours=3)
    assert sla.due_at(ClaimStage.PREAUTH, received) == received + timedelta(hours=1)
    assert sla.due_at(ClaimStage.REIMBURSEMENT, received) == received + timedelta(days=15)


# ---------------------------------------------------------------- end to end (database)


def user_of(db, hdrs) -> User:
    from app.core.security import decode_access_token

    payload = decode_access_token(hdrs["Authorization"].split()[1])
    assert payload is not None
    user = db.get(User, uuid.UUID(payload["sub"]))
    assert user is not None
    return user


def test_pipeline_scores_the_claim_and_sets_a_deadline(client, db, defaults):  # noqa: F811
    hdrs = headers(db)
    pdf, case = synthetic(["B-10"], seed=41)
    claim_id = process(client, db, hdrs, pdf, case.stage)
    claim = db.get(Claim, claim_id)
    assert claim.risk_band is not None and claim.risk_band.value == "red" and claim.risk_score >= 30
    assert claim.sla_due_at is not None                        # uploaded claim: the IRDAI clock runs
    res = client.get(f"/api/claims/{claim_id}/review", headers=hdrs).json()
    assert res["band"] == "red" and any(p["rule_id"] == "B-10" for p in res["score_parts"])
    assert res["open_blocking"] >= 1 and res["approve_blockers"] and res["can_decide"]
    assert res["claimed_amount"] and res["suggested_payable"] is not None
    queue = client.get("/api/claims?band=red", headers=hdrs).json()
    assert [c["id"] for c in queue["items"]] == [str(claim_id)] and queue["band_counts"]["red"] == 1


def test_review_moves_the_claim_into_review_and_rescores(client, db, defaults):  # noqa: F811
    hdrs = headers(db)
    pdf, case = synthetic(["B-10"], seed=42)
    claim_id = process(client, db, hdrs, pdf, case.stage)
    finding = db.scalars(select(Finding).where(Finding.claim_id == claim_id, Finding.rule_id == "B-10")).first()
    note = client.post(f"/api/findings/{finding.id}/review", headers=hdrs, json={"action": "noted", "reason": "Ask"})
    assert note.status_code == 200 and note.json()["status"] == "open"           # a note does not settle it
    assert client.post(f"/api/findings/{finding.id}/review", headers=hdrs, json={"action": "noted"}).status_code == 422
    res = client.post(f"/api/findings/{finding.id}/review", headers=hdrs,
                      json={"action": "dismissed", "reason": "MRP on the strip is the old one"})
    assert res.status_code == 200 and res.json()["status"] == "dismissed"
    db.expire_all()
    claim = db.get(Claim, claim_id)
    assert claim.status == ClaimStatus.IN_REVIEW
    summary = client.get(f"/api/claims/{claim_id}/review", headers=hdrs).json()
    assert summary["dismissed_findings"] >= 1 and all(p["rule_id"] != "B-10" for p in summary["score_parts"])


def test_medical_findings_are_settled_by_a_senior_reviewer(client, db, defaults):  # noqa: F811
    hdrs = headers(db)
    pdf, case = synthetic([], seed=43)
    claim_id = process(client, db, hdrs, pdf, case.stage)
    finding = Finding(claim_id=claim_id, rule_id="M-05", rule_version=1, severity=Severity.AMBER, title="Long stay",
                      explanation="Stay longer than the norm.", evidence=[], needs_medical_review=True,
                      fingerprint="test-m05", is_current=True)
    db.add(finding)
    db.commit()
    url = f"/api/findings/{finding.id}/review"
    assert client.post(url, headers=hdrs, json={"action": "confirmed"}).status_code == 403
    assert client.post(url, headers=hdrs, json={"action": "noted", "reason": "Needs a doctor"}).status_code == 200
    senior = headers(db, RoleName.SENIOR_REVIEWER)
    assert client.post(url, headers=senior, json={"action": "confirmed"}).status_code == 200


def test_approval_waits_for_findings_then_closes_the_claim(client, db, defaults):  # noqa: F811
    hdrs = headers(db)
    pdf, case = synthetic(["B-10"], seed=44)
    claim_id = process(client, db, hdrs, pdf, case.stage)
    url = f"/api/claims/{claim_id}/decisions"
    res = client.post(url, headers=hdrs, json={"recommendation": "approve", "reason": "All fine here"})
    assert res.status_code == 409                                                  # B-10 still open
    findings = db.scalars(select(Finding).where(Finding.claim_id == claim_id, Finding.is_current.is_(True))).all()
    for f in findings:
        if f.severity.value in ("red", "amber"):
            assert client.post(f"/api/findings/{f.id}/review", headers=hdrs,
                               json={"action": "confirmed"}).status_code == 200
    b10 = next(f for f in findings if f.rule_id == "B-10")
    amount = str(b10.amount_at_stake or Decimal("10.00"))
    assert client.post(url, headers=hdrs, json={"recommendation": "approve_with_deductions", "reason": "Too short",
                                                "deductions": []}).status_code == 422
    res = client.post(url, headers=hdrs, json={
        "recommendation": "approve_with_deductions", "reason": "Pharmacy charged above MRP; deducted.",
        "deductions": [{"description": "Charged above MRP", "amount": amount, "finding_id": str(b10.id)}]})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "decided" and len(body["decisions"]) == 1
    decision = db.scalar(select(Decision).where(Decision.claim_id == claim_id))
    claim = db.get(Claim, claim_id)
    assert decision.approved_amount == claim.claimed_amount - Decimal(amount)
    assert db.scalar(select(AuditLog).where(AuditLog.action == "claim.approve_with_deductions")) is not None
    assert client.post(url, headers=hdrs, json={"recommendation": "approve", "reason": "Again please"}
                       ).status_code == 409                                        # already decided
    assert client.post(f"/api/findings/{b10.id}/review", headers=hdrs,
                       json={"action": "dismissed", "reason": "late"}).status_code == 409


def test_escalated_claims_are_decided_by_a_senior_reviewer(client, db, defaults):  # noqa: F811
    hdrs = headers(db)
    pdf, case = synthetic([], seed=45)
    claim_id = process(client, db, hdrs, pdf, case.stage)
    url = f"/api/claims/{claim_id}/decisions"
    res = client.post(url, headers=hdrs, json={"recommendation": "escalate", "reason": "High value, needs a doctor"})
    assert res.status_code == 201 and res.json()["escalated"] and res.json()["status"] == "in_review"
    assert client.post(url, headers=hdrs, json={"recommendation": "approve", "reason": "Looks fine now"}
                       ).status_code == 403
    senior = headers(db, RoleName.SENIOR_REVIEWER)
    summary = client.get(f"/api/claims/{claim_id}/review", headers=senior).json()
    assert summary["can_decide"]
    lead = headers(db, RoleName.TEAM_LEAD)
    assert client.post(url, headers=lead, json={"recommendation": "approve", "reason": "Lead says ok"}
                       ).status_code == 403
    res = client.post(url, headers=senior, json={"recommendation": "query_hospital", "reason": "Need the ICU notes"})
    assert res.status_code == 201 and not res.json()["escalated"] and res.json()["status"] == "in_review"


def test_deductions_are_checked(client, db, defaults):  # noqa: F811
    hdrs = headers(db)
    pdf, case = synthetic([], seed=46)
    claim_id = process(client, db, hdrs, pdf, case.stage)
    url = f"/api/claims/{claim_id}/decisions"
    big = {"recommendation": "approve_with_deductions", "reason": "Too much taken off",
           "deductions": [{"description": "Everything", "amount": "99999999.00"}]}
    assert client.post(url, headers=hdrs, json=big).status_code == 422
    other = {"recommendation": "approve_with_deductions", "reason": "Wrong finding",
             "deductions": [{"description": "x", "amount": "10.00", "finding_id": str(uuid.uuid4())}]}
    assert client.post(url, headers=hdrs, json=other).status_code == 422
    query = {"recommendation": "query_hospital", "reason": "Need the bill break-up",
             "deductions": [{"description": "x", "amount": "10.00"}]}
    assert client.post(url, headers=hdrs, json=query).status_code == 422


def test_assignment(client, db, defaults):  # noqa: F811
    hdrs = headers(db)
    me = user_of(db, hdrs)
    pdf, case = synthetic([], seed=47)
    claim_id = process(client, db, hdrs, pdf, case.stage)
    url = f"/api/claims/{claim_id}/assign"
    res = client.post(url, headers=hdrs, json={"user_id": str(me.id)})
    assert res.status_code == 200 and res.json()["assignee"]["id"] == str(me.id)
    mine = client.get("/api/claims?assignee=me", headers=hdrs).json()
    assert [c["id"] for c in mine["items"]] == [str(claim_id)] and mine["items"][0]["assignee"] == me.full_name
    other_hdrs = headers(db)
    other = user_of(db, other_hdrs)
    assert client.post(url, headers=other_hdrs, json={"user_id": str(other.id)}).status_code == 403  # not theirs
    lead = headers(db, RoleName.TEAM_LEAD)
    assert client.post(url, headers=lead, json={"user_id": str(other.id)}).status_code == 200
    assert client.post(url, headers=lead, json={"user_id": str(user_of(db, lead).id)}).status_code == 422
    people = client.get("/api/review/assignees", headers=lead).json()
    assert {p["id"] for p in people} >= {str(me.id), str(other.id)}
    assert db.scalar(select(AuditLog).where(AuditLog.action == "claim.assigned")) is not None


def test_query_letter_lists_missing_documents(client, db, defaults):  # noqa: F811
    hdrs = headers(db)
    pdf, case = synthetic(["C-01"], seed=48)
    claim_id = process(client, db, hdrs, pdf, case.stage)
    letter = client.get(f"/api/claims/{claim_id}/query-letter", headers=hdrs).json()
    assert letter["documents"] and "Documents required" in letter["body"]
    assert db.get(Claim, claim_id).claim_number in letter["subject"]


def test_queue_puts_claims_waiting_for_review_first_by_band(client, db, defaults):  # noqa: F811
    hdrs = headers(db)
    green = process(client, db, hdrs, synthetic([], seed=49)[0])
    red = process(client, db, hdrs, synthetic(["B-10"], seed=50)[0])
    items = client.get("/api/claims", headers=hdrs).json()["items"]
    order = [c["id"] for c in items]
    assert order.index(str(red)) < order.index(str(green))
    assert {c["risk_band"] for c in items} >= {"red"}
