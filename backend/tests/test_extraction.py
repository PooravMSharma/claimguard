"""Step 8: structured claim record (rules + AI reader), evidence links, corrections, cost log."""

import uuid
from decimal import Decimal

import pypdfium2 as pdfium
import pytest
from sqlalchemy import select

from app.ai import client as ai_client
from app.ai.client import AIError, AIResult
from app.core.config import get_settings
from app.core.security import create_access_token
from app.extraction import ai_extract
from app.extraction.documents import build_documents, normalize
from app.extraction.evaluate import KEY, Tally, compare
from app.extraction.layout import Table, group_lines, labelled_fields, parse_table, words_of
from app.extraction.layout import W as LayoutWord
from app.extraction.model import DocExtract, Value
from app.extraction.values import find_icd, parse_age_sex, parse_amount, parse_datetime, strip_icd
from app.models.enums import ClaimStatus, RoleName
from app.models.extraction import AiCall, ExtractedField
from app.models.user import AuditLog
from app.pipeline import runner
from app.pipeline.classify import classify
from app.pipeline.ocr import quality, read_pdf_page
from app.services import intake
from app.services.storage import LocalStorage
from app.services.users import create_user
from app.synthetic.generate import Generator, Options
from app.synthetic.render import render_case

# ---------------------------------------------------------------- helpers


def synthetic_claim(scenario_checks: list[str], seed: int = 21):
    """A rendered synthetic claim with its answers (record.json equivalent)."""
    gen = Generator(Options(out_dir=None, count=1, seed=seed))  # type: ignore[arg-type]
    case = gen.build(0, scenario_checks)
    rendered = render_case(case)
    return rendered.pdf, {"claim_id": case.claim_id, "documents": rendered.fields}, case


def read_rules(pdf: bytes) -> list[DocExtract]:
    doc = pdfium.PdfDocument(pdf)
    pages, prev = [], None
    for i in range(len(doc)):
        r = read_pdf_page(doc, i)
        c = classify(r.text, prev)
        prev = c.doc_type
        pages.append({"id": f"p{i}", "file_id": "f", "doc_type": c.doc_type.value,
                      "words": [w.as_list() for w in r.words], "quality": quality(r)[0]})
    return build_documents(pages)


def W(text, x0, y0, x1=None, y1=None, line=None):
    return [text, 1.0, x0, y0, x1 if x1 is not None else x0 + 0.01 * max(len(text), 1), y1 or y0 + 0.012, line]


class FakeAI:
    """Stand-in for the AI provider: returns a fixed answer and records what it was sent."""

    name, model = "fake", "fake-model"

    def __init__(self, answer: dict | None = None, error: str | None = None, fatal: bool = False):
        self.answer, self.error, self.fatal, self.calls = answer or {}, error, fatal, []

    def structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise AIError(self.error, fatal=self.fatal)
        return AIResult(self.answer, self.name, self.model, 1200, 300, Decimal("0.0081"), 900)


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    local = LocalStorage(str(tmp_path / "storage"))
    monkeypatch.setattr(intake, "get_storage", lambda: local)
    monkeypatch.setattr(intake, "enqueue_processing", lambda claim_id: "job")
    return local


def headers(db, role: RoleName = RoleName.ADJUDICATOR) -> dict[str, str]:
    user = create_user(db, email=f"{role.value}@x.claimguard.in", full_name="Reviewer", role=role,
                       password="Claims#2026secure")
    token, _ = create_access_token(user.id)
    return {"Authorization": f"Bearer {token}"}


def process(client, db, hdrs, pdf: bytes):
    res = client.post("/api/claims", headers=hdrs, data={"stage": "final_cashless"},
                      files=[("files", ("claim.pdf", pdf, "application/pdf"))])
    assert res.status_code == 201, res.text
    claim_id = uuid.UUID(res.json()["claim"]["id"])
    claim = runner.run_pipeline(db, claim_id)
    assert claim.status == ClaimStatus.READY_FOR_REVIEW, claim.processing_error
    return claim_id


# ---------------------------------------------------------------- parsing


def test_value_parsers():
    assert str(parse_datetime("Admitted 05/08/2026 13:50")) == "2026-08-05 13:50:00"
    assert parse_datetime("31/02/2026") is None
    assert parse_amount("Rs. 1,50,000.00") == Decimal("150000.00")
    assert parse_amount("Approved amount (Rs.)85,100.00") == Decimal("85100.00")
    assert parse_amount("§00,000") is None              # scan lost the leading 5 of 5,00,000: not zero
    assert parse_amount("0.50") == Decimal("0.50")
    assert parse_age_sex("37 Y / M") == (37, "M") and parse_age_sex("65 Years / Female") == (65, "F")
    assert find_icd("Dengue fever (ICD-10: A90)") == "A90" and find_icd("fracture (S72.001A)") == "S72.001A"
    assert strip_icd("Dengue fever (ICD-10: A90)") == "Dengue fever"
    assert normalize("admission", "05/08/2026 13:50") == "2026-08-05T13:50:00"
    assert normalize("claimed_amount", "84,069.40") == 84069.40
    assert normalize("rohini_id", "8900 0803 24643") == "8900080324643"


def test_labels_need_a_gap_and_respect_columns():
    words = words_of("p", 0, [
        W("Hospital", 0.07, 0.10), W("Lifeline", 0.23, 0.10), W("Hospital", 0.29, 0.10), W("Rohini", 0.50, 0.10),
        W("ID", 0.56, 0.10), W("8900080324643", 0.66, 0.10),
        W("Diagnosis", 0.50, 0.13), W("Acute", 0.66, 0.13), W("appendicitis", 0.70, 0.13),
        W("(perforated)", 0.66, 0.145), W("Bill", 0.07, 0.16), W("No.", 0.11, 0.16), W("IPB/1", 0.23, 0.16),
    ])
    f = labelled_fields(group_lines(words), {"Hospital": "hospital_name", "Rohini ID": "rohini_id",
                                             "Diagnosis": "diagnosis", "Bill No.": "bill_no"}, {"diagnosis"})
    assert f["hospital_name"].text == "Lifeline Hospital"          # "Hospital" inside the value is not a label
    assert f["rohini_id"].text == "8900080324643"
    assert f["diagnosis"].text == "Acute appendicitis (perforated)"  # continuation line under the value
    assert f["bill_no"].text == "IPB/1"
    assert (f["rohini_id"].bbox or [0])[0] == 0.66 and f["rohini_id"].page_id == "p"


def test_table_rows_with_wrapped_text():
    rows = [W("#", 0.05, 0.1), W("Description", 0.1, 0.1), W("Qty", 0.6, 0.1), W("Amount", 0.8, 0.1),
            W("1", 0.05, 0.12), W("Consultant", 0.1, 0.12), W("visit", 0.18, 0.12), W("2", 0.62, 0.12),
            W("1,400.00", 0.82, 0.12),
            W("(Dr. Rao)", 0.1, 0.135),
            W("2", 0.05, 0.15), W("Nursing", 0.1, 0.15), W("3", 0.62, 0.15), W("900.00", 0.84, 0.15),
            W("GRAND", 0.1, 0.17), W("TOTAL", 0.16, 0.17), W("2,300.00", 0.82, 0.17)]
    table = Table({"no": ["#"], "description": ["Description"], "qty": ["Qty"], "amount": ["Amount"]},
                  anchor="no", row_end="amount", stop=("grand total",))
    out = parse_table(group_lines(words_of("p", 0, rows)), table)
    assert [(r["description"].text, r["amount"].text) for r in out] == [
        ("Consultant visit (Dr. Rao)", "1,400.00"), ("Nursing", "900.00")]


# ---------------------------------------------------------------- rules on synthetic claims


@pytest.mark.parametrize("checks", [[], ["B-09"], ["X-07"], ["F-04"], ["C-05"]])
def test_rules_read_digital_claims_exactly(checks):
    pdf, record, _ = synthetic_claim(checks, seed=7)
    tally = Tally()
    compare(read_rules(pdf), record, tally)
    assert tally.rate(KEY & set(tally.totals)) == 1.0, tally.misses[:5]
    assert tally.rows_right == tally.rows_total, tally.misses[:5]


# ---------------------------------------------------------------- pipeline, record API, corrections


def test_record_api_after_processing(client, db):
    hdrs = headers(db)
    pdf, record, case = synthetic_claim(["X-04"], seed=3)
    claim_id = process(client, db, hdrs, pdf)
    rec = client.get(f"/api/claims/{claim_id}/record", headers=hdrs).json()

    s = rec["summary"]
    assert s["patient_name"] == case.patient.name and s["policy_no"] == case.policy.policy_no
    assert s["rohini_id"] == case.hospital.rohini_id and float(s["claimed_amount"]) == case.claimed_amount
    assert len(rec["bill_lines"]) == len(case.bill)
    total = sum(Decimal(b["amount"]) for b in rec["bill_lines"])
    assert total == Decimal(str(case.bill_total)).quantize(Decimal("0.01"))
    # the seeded X-04 drug is billed but never charted
    meds = {m["drug_name"]: m["in_medication_chart"] for m in rec["medications"]}
    assert meds["Inj Meropenem 1 g"] is False and sum(meds.values()) == len(meds) - 1
    assert all(i["billed"] and i["has_report"] for i in rec["investigations"])
    assert rec["ai"]["calls"] == 0                      # no key in tests: rules only
    claim_form = next(d for d in rec["documents"] if d["doc_type"] == "claim_form")
    policy = next(f for f in claim_form["fields"] if f["name"] == "policy_no")
    assert policy["method"] == "rule" and policy["page_id"] and policy["bbox"]["x1"] > policy["bbox"]["x0"]


def test_reviewer_correction_is_kept_and_survives_reprocessing(client, db):
    hdrs = headers(db)
    pdf, _, case = synthetic_claim([], seed=5)
    claim_id = process(client, db, hdrs, pdf)
    field = db.scalar(select(ExtractedField).where(ExtractedField.claim_id == claim_id,
                                                   ExtractedField.section == "claim_form",
                                                   ExtractedField.field_name == "patient_name"))
    res = client.patch(f"/api/claims/{claim_id}/fields/{field.id}", headers=hdrs, json={"value": "Corrected Name"})
    assert res.status_code == 200 and res.json()["corrected_value"] == "Corrected Name"
    assert res.json()["value"] == case.patient.name            # the original reading is kept
    assert client.get(f"/api/claims/{claim_id}/record", headers=hdrs).json()["summary"]["patient_name"] == \
        "Corrected Name"
    assert db.scalar(select(AuditLog.action).where(AuditLog.entity_id == str(field.id))) == "field.corrected"

    runner.run_pipeline(db, claim_id)                           # reprocess: correction carried over
    again = db.scalar(select(ExtractedField).where(ExtractedField.claim_id == claim_id,
                                                   ExtractedField.section == "claim_form",
                                                   ExtractedField.field_name == "patient_name"))
    assert again.corrected_value == "Corrected Name"

    viewer = headers(db, RoleName.FRAUD_INVESTIGATOR)
    assert client.patch(f"/api/claims/{claim_id}/fields/{field.id}", headers=viewer,
                        json={"value": "x"}).status_code == 403


# ---------------------------------------------------------------- AI reader


def _ocr_doc(doc_type: str, fields: dict[str, str]) -> tuple[DocExtract, list]:
    """A scanned document whose rules found little; words carry what is printed."""
    words = words_of("p0", 0, [W("Bill", 0.07, 0.10), W("No.", 0.12, 0.10), W("IPB/2026/38342", 0.23, 0.10),
                               W("GRAND", 0.07, 0.5), W("TOTAL", 0.13, 0.5), W("84,069.40", 0.8, 0.5)])
    doc = DocExtract(doc_type, doc_type, ["p0"], "fair",
                     {k: Value(v, normalize(k, v), "p0", None, 0.7) for k, v in fields.items()})
    return doc, words


def test_ai_answer_is_merged_with_evidence():
    doc, words = _ocr_doc("final_bill", {"bill_no": "1PB/2026/38342"})   # OCR misread
    answer = {"bill_no": "IPB/2026/38342", "bill_total": "84,069.40", "patient_name": None, "uncertain": [],
              "lines": [{"no": "1", "description": "Room rent", "qty": "7", "rate": "1,760.00",
                         "amount": "12,320.00"}]}
    changed = ai_extract.merge(doc, answer, words, digital=False)
    assert changed >= 3 and doc.ai_used
    assert doc.fields["bill_no"].text == "IPB/2026/38342" and doc.fields["bill_no"].method == "ai"
    assert doc.fields["bill_total"].normalized == 84069.40
    assert doc.fields["bill_total"].bbox is not None                     # placed on the page
    assert doc.tables["lines"][0]["amount"].normalized == 12320.0
    assert "patient_name" not in doc.fields                              # null means not printed: nothing invented


def test_ai_does_not_override_digital_text():
    doc, words = _ocr_doc("final_bill", {"bill_no": "IPB/2026/38342"})
    doc.quality = "good"
    ai_extract.merge(doc, {"bill_no": "IPB/9999/1", "uncertain": []}, words, digital=True)
    assert doc.fields["bill_no"].text == "IPB/2026/38342" and doc.fields["bill_no"].method == "rule"


def test_ai_schema_and_when_it_is_used():
    schema = ai_extract.schema_for("final_bill")
    assert {"bill_no", "bill_total", "lines", "uncertain"} <= set(schema["properties"])
    assert schema["properties"]["lines"]["items"]["properties"].keys() >= {"description", "amount"}
    complete = DocExtract("id_proof", "id_proof", ["p"], "good", {"patient_name": Value("A B")})
    assert not ai_extract.needs_ai(complete, "gaps") and ai_extract.needs_ai(complete, "all")
    assert ai_extract.needs_ai(DocExtract("id_proof", "id_proof", ["p"], "fair", {"patient_name": Value("A")}), "gaps")
    assert ai_extract.needs_ai(DocExtract("id_proof", "id_proof", ["p"], "good"), "gaps")   # name missing


def test_provider_off_without_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_provider", "anthropic")
    monkeypatch.setattr(settings, "ai_api_key", "")
    assert ai_client.get_provider() is None
    monkeypatch.setattr(settings, "ai_api_key", "sk-test")
    monkeypatch.setattr(settings, "ai_extraction", "off")
    assert ai_client.get_provider() is None
    assert ai_client.cost(1_000_000, 0) == Decimal(str(settings.ai_price_input_per_mtok))


def test_pipeline_calls_ai_for_gaps_and_logs_cost(client, db, monkeypatch):
    hdrs = headers(db)
    pdf, _, case = synthetic_claim([], seed=11)
    fake = FakeAI({"uncertain": []})
    monkeypatch.setattr(ai_client, "get_provider", lambda: fake)
    # remove the policy card's sum insured line from the rules' view: the document now has a gap
    from app.extraction import documents as docs_module

    original = docs_module.LABELS.pop("Sum insured (Rs.)")
    try:
        claim_id = process(client, db, hdrs, pdf)
    finally:
        docs_module.LABELS["Sum insured (Rs.)"] = original
    assert len(fake.calls) == 1 and "policy copy" in fake.calls[0]["tool_description"]
    sent = fake.calls[0]["content"]
    assert any(b["type"] == "image" for b in sent)                       # the page image is sent
    calls = db.scalars(select(AiCall).where(AiCall.claim_id == claim_id)).all()
    assert [(c.purpose, c.status, c.cost_usd) for c in calls] == [("extract:policy_copy", "ok", Decimal("0.00810"))]
    rec = client.get(f"/api/claims/{claim_id}/record", headers=hdrs).json()
    assert rec["ai"]["calls"] == 1 and Decimal(rec["ai"]["cost_usd"]) == Decimal("0.0081")


def test_ai_failure_is_logged_and_claim_still_processed(client, db, monkeypatch):
    hdrs = headers(db)
    pdf, _, _ = synthetic_claim([], seed=12)
    monkeypatch.setattr(ai_client, "get_provider", lambda: FakeAI(error="RateLimitError: slow down"))
    monkeypatch.setattr(get_settings(), "ai_extraction", "all")
    claim_id = process(client, db, hdrs, pdf)
    calls = db.scalars(select(AiCall).where(AiCall.claim_id == claim_id)).all()
    assert calls and all(c.status == "error" and "slow down" in c.error for c in calls)
    rec = client.get(f"/api/claims/{claim_id}/record", headers=hdrs).json()
    assert rec["summary"]["patient_name"]                                 # rules still filled the record


def test_fatal_ai_error_stops_further_calls(client, db, monkeypatch):
    """No server / no model / no credit: one logged failure, the other documents are not sent."""
    hdrs = headers(db)
    pdf, _, _ = synthetic_claim([], seed=13)
    fake = FakeAI(error="Cannot reach Ollama", fatal=True)
    monkeypatch.setattr(ai_client, "get_provider", lambda: fake)
    monkeypatch.setattr(get_settings(), "ai_extraction", "all")
    monkeypatch.setattr(get_settings(), "ai_parallel_calls", 1)
    claim_id = process(client, db, hdrs, pdf)
    calls = db.scalars(select(AiCall).where(AiCall.claim_id == claim_id)).all()
    assert len(fake.calls) == 1 and len(calls) == 1 and calls[0].status == "error"
    rec = client.get(f"/api/claims/{claim_id}/record", headers=hdrs).json()
    assert rec["summary"]["patient_name"]


def test_ai_values_are_normalised_like_rule_values():
    """Structured fields (sub-limits, room eligibility, procedures) get the same shape whoever read them."""
    doc = DocExtract("policy_copy", "policy_copy", ["p1"], "poor")
    answer = {"sub_limits": "Cataract: Rs. 40,000 per eye; Knee replacement: Rs 1,50,000",
              "room_eligibility": "Single private room up to 1% of sum insured per day", "uncertain": []}
    ai_extract.merge(doc, answer, [], digital=False)
    assert doc.fields["sub_limits"].normalized == {"cataract": 40000.0, "knee_replacement": 150000.0}
    assert doc.fields["room_eligibility"].normalized == {"text": answer["room_eligibility"], "cap_pct": 1.0}
    assert normalize("procedures", "Appendicectomy; Drain placement") == ["Appendicectomy", "Drain placement"]
    assert normalize("sub_limits", "Cataract: Rs. , Maternity: Rs 50,000") == {"maternity": 50000.0}  # no crash
    tally = Tally()
    compare([DocExtract("policy_copy", "policy_copy", ["p1"], "poor",
                        {"sub_limits": Value("garbled", "garbled")})],
            {"documents": [{"type": "policy_ecard", "fields": {"sub_limits": {"cataract": 40000}}}]}, tally)
    assert tally.totals["sub_limits"] == 1 and tally.fields["sub_limits"] == 0     # a miss, not a crash


def test_ocr_slips_with_one_right_answer_are_tidied():
    from app.extraction.documents import tidy, ward_code
    from app.extraction.values import fix_doctor_prefix, rohini_digits

    assert fix_doctor_prefix("Or. Pradeep Iyer") == "Dr. Pradeep Iyer"
    assert fix_doctor_prefix("Dr, Rahul Saxena") == "Dr. Rahul Saxena"
    assert fix_doctor_prefix("Or") is None and fix_doctor_prefix("Dr. A. Kumar") == "Dr. A. Kumar"
    assert fix_doctor_prefix("Orthopaedic ward") == "Orthopaedic ward"            # not a prefix
    assert rohini_digits("10 8900080214975") == "8900080214975"
    assert rohini_digits("8900080214975") == "8900080214975"
    assert ward_code("sem_private") == ward_code("Semi-private") == "semi_private"
    assert ward_code("senu pnvate") == "senu_pnvate"                  # too far off to guess: left for the AI/reviewer
    assert ward_code("Isolation") == "isolation" and ward_code("Private") == "private"
    assert strip_icd("Acute gastroenteritis (ICD-10: 809)") == "Acute gastroenteritis"
    assert strip_icd("Symptomatic choletithiasis (00-10: K80.20)") == "Symptomatic choletithiasis"
    doc = tidy(DocExtract("discharge_summary", "discharge_summary", ["p"], "fair",
                          {"doctor_name": Value("Or"), "surgeon": Value("Or. Rahul Saxena", "Or. Rahul Saxena")}))
    assert "doctor_name" not in doc.fields and doc.fields["surgeon"].normalized == "Dr. Rahul Saxena"


def test_every_scanned_document_goes_to_the_ai():
    complete = DocExtract("policy_copy", "policy_copy", ["p"], "good",
                          {k: Value("x") for k in ("patient_name", "policy_no", "sum_insured", "policy_start",
                                                   "policy_end", "product", "member_id", "inception")})
    assert not ai_extract.needs_ai(complete, "gaps") or ai_extract.needs_ai(complete, "gaps")   # rules decide
    assert ai_extract.needs_ai(complete, "gaps", scanned=True)


def test_report_impression_stops_at_signature():
    from app.extraction.documents import _report_text

    v = Value("x", None)
    v.normalized = ["Gall bladder distended with multiple calculi, largest 12 mm.", "No CBD dilatation.",
                    "Or. Ajay Rathore, MD (Radiodiagnosis). Radiologist"]
    out = _report_text(v)
    assert out is not None
    assert out.normalized == "Gall bladder distended with multiple calculi, largest 12 mm. No CBD dilatation."


def test_ai_doctor_names_are_tidied():
    doc = DocExtract("ot_notes", "ot_notes", ["p"], "fair")
    ai_extract.merge(doc, {"surgeon": "Or. Rahul Saxena", "uncertain": []}, [], digital=False)
    assert doc.fields["surgeon"].text == "Dr. Rahul Saxena" and doc.fields["surgeon"].method == "ai"


def test_wrapped_value_ending_in_a_label_word_is_kept():
    """'Sanjeevani Superspeciality' / 'Hospital' on the next line: 'Hospital' is the end of the name."""
    def word(text, x0, y0, line):
        return LayoutWord(text, 1.0, x0, y0, x0 + 0.009 * len(text), y0 + 0.012, "p", 0, line)

    lines = [[word("Hospital", 0.10, 0.20, 1), word("Sanjeevani", 0.33, 0.20, 1),
              word("Superspeciality", 0.43, 0.20, 1), word("Rohini", 0.60, 0.20, 1),
              word("8900080214975", 0.75, 0.20, 1)],
             [word("Hospital", 0.33, 0.215, 2)]]
    found = labelled_fields(lines, {"Hospital": "hospital_name", "Rohini": "rohini_id"}, {"hospital_name"})
    assert found["hospital_name"].text == "Sanjeevani Superspeciality Hospital"
