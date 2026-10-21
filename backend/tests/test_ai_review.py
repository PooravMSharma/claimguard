"""Step 10: the AI reviewer and the AI judgement checks, with a scripted fake model (never a real one).

The fake answers by question: it looks at which fields the JSON schema asks for (admission / tests / icu /
severity for the clinical call, diagnosis_change / upcoding for coding, pairs, repeats, signature).
"""

import copy
from decimal import Decimal

from app.ai.client import AIError, AIResult
from app.extraction.model import Value
from app.rules.ai_review import AiReviewer
from app.rules.engine import run_checks
from tests.test_rules_checks import PROC, context, d, genuine_docs, line, row, set_amounts, v


class ScriptedAI:
    """Fake provider: `answers` maps a question name to a dict, or to a function of the facts text -> dict."""

    name, model = "fake", "fake-model"

    def __init__(self, answers: dict | None = None, error: str | None = None, fatal: bool = False,
                 vision: bool = True):
        self.answers, self.error, self.fatal, self.vision = answers or {}, error, fatal, vision
        self.calls: list[dict] = []

    def question(self, schema: dict) -> str:
        props = set(schema.get("properties", {}))
        for name in ("pairs", "repeats", "signature"):
            if name in props:
                return name
        return "coding" if props & {"diagnosis_change", "upcoding"} else "clinical"

    def structured(self, **kwargs) -> AIResult:
        self.calls.append(kwargs)
        if self.error:
            raise AIError(self.error, fatal=self.fatal)
        q = self.question(kwargs["schema"])
        text = " ".join(b.get("text", "") for b in kwargs["content"] if b.get("type") == "text")
        answer = self.answers.get(q, {})
        data = answer(text) if callable(answer) else answer
        assert isinstance(data, dict)
        return AIResult(data, self.name, self.model, 2000, 200, Decimal("0"), 1500)


class MemoryCache:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    def get(self, key):
        return self.rows.get(key)

    def put(self, key, purpose, model, data):
        self.rows[key] = data


def fact_id(ctx, kind: str, contains: str = "") -> str:
    return next(f.id for f in ctx.ai.facts(ctx) if f.kind == kind and contains.lower() in f.text.lower())


def run(ctx):
    return {o.rule_id: o for o in run_checks(ctx, {})}


def with_ai(docs=None, **kw) -> tuple:
    ai = ScriptedAI(**kw)
    ctx = context(docs)
    ctx.ai = AiReviewer(ai, MemoryCache())
    return ctx, ai


OK = {"admission": {"verdict": "needed", "reason": "IV fluids needed.", "facts": ["F1"]}, "tests": []}


def medical_docs():
    """The genuine claim turned into a short medical admission with calm vitals: the case M-01 asks about."""
    docs = [x for x in genuine_docs() if x.doc_type != "ot_notes"]
    lines = d(docs, "final_bill").tables["lines"]
    surgical = [r for r in lines if r["description"].text.startswith(("Surgeon", "Anaesthetist", "Operation"))]
    lines[:] = [r for r in lines if r not in surgical]
    set_amounts(docs, -sum(float(str(r["amount"].normalized)) for r in surgical))
    for doc_type in ("discharge_summary", "claim_form", "preauth_form"):
        doc = d(docs, doc_type)
        name = "preauth_diagnosis" if doc_type == "preauth_form" else "diagnosis"
        doc.fields[name] = v("Acute gastroenteritis without dehydration", page=doc.page_ids[0])
        doc.fields.pop("procedures", None)
    ds = d(docs, "discharge_summary")
    ds.fields["complaints"] = v("Loose stools 2-3 episodes since morning.", page="p3")
    ds.fields["examination"] = v("Temp 98.4 F, Pulse 78/min, BP 120/80 mmHg, RR 16/min, SpO2 98%", page="p3")
    for day in d(docs, "nursing_chart").tables["days"]:
        day.update(temp=v("98.4", page="p9"), pulse=v("78", page="p9"), bp=v("120/80", page="p9"),
                   rr=v("16", page="p9"), spo2=v("98%", page="p9"))
    return docs


# ---------------------------------------------------------------- gates: what is never put to the model


def test_surgical_claim_with_routine_tests_needs_no_call():
    ctx, ai = with_ai(answers={"clinical": OK})
    out = run(ctx)
    assert out["M-01"].status == "passed" and "procedure" in (out["M-01"].detail or "")
    assert out["M-02"].status == "passed"                         # CBC (baseline), USG Rs. 1,200 (cheap)
    assert out["M-04"].status == "not_applicable" and out["M-06"].status == "not_applicable"
    assert out["X-08"].status == "passed" and out["B-11"].status == "passed"
    assert not [o.rule_id for o in out.values() if o.findings]
    assert ai.calls == []


def test_abnormal_admission_vitals_or_severity_are_not_asked():
    docs = medical_docs()
    d(docs, "nursing_chart").tables["days"][0]["pulse"] = v("112", page="p9")
    ctx, ai = with_ai(docs, answers={"clinical": OK})
    o = run(ctx)["M-01"]
    assert o.status == "passed" and "pulse 112" in (o.detail or "")
    docs = medical_docs()
    d(docs, "discharge_summary").fields["diagnosis"] = v("Acute gastroenteritis with moderate dehydration", page="p3")
    ctx, _ = with_ai(docs, answers={"clinical": OK})
    assert "severity" in (run(ctx)["M-01"].detail or "")
    docs = medical_docs()
    d(docs, "discharge_summary").fields["diagnosis"] = v("Single spontaneous delivery", page="p3")
    ctx, _ = with_ai(docs, answers={"clinical": OK})
    assert "obstetric" in (run(ctx)["M-01"].detail or "")


# ---------------------------------------------------------------- clinical review


def test_calm_medical_admission_is_asked_and_one_call_serves_all():
    ctx, ai = with_ai(medical_docs(), answers={"clinical": OK})
    out = run(ctx)
    assert len(ai.calls) == 1 and "admission" in ai.calls[0]["schema"]["properties"]
    assert out["M-01"].status == "passed" and not [o for o in out.values() if o.findings]


def test_admission_not_needed_raises_amber_with_cited_evidence():
    def answer(text):
        return {"admission": {"verdict": "not_needed", "reason": "Mild symptoms, normal vitals.",
                              "facts": ["F3", "f4", "F999"]}, "tests": []}
    ctx, _ = with_ai(medical_docs(), answers={"clinical": answer})
    o = run(ctx)["M-01"]
    assert o.status == "flagged"
    f = o.findings[0]
    assert f.severity == "amber" and f.needs_medical_review and f.model_version == "fake:fake-model"
    assert [e["quote"].split()[0] for e in f.evidence] == ["F3", "F4"]          # invented F999 is dropped
    assert all(e["page_id"] for e in f.evidence)
    assert f.amount_at_stake and f.amount_at_stake > 0


def test_no_finding_without_real_citations_or_when_unclear():
    docs = medical_docs()
    ctx, _ = with_ai(docs, answers={"clinical": {"admission": {"verdict": "not_needed", "reason": "x",
                                                               "facts": ["F999"]}, "tests": []}})
    o = run(ctx)["M-01"]
    assert o.status == "not_evaluable" and not o.findings
    ctx, _ = with_ai(docs, answers={"clinical": {"admission": {"verdict": "unclear", "reason": "Not enough.",
                                                               "facts": []}}})
    o = run(ctx)["M-01"]
    assert o.status == "not_evaluable" and "Not enough" in (o.detail or "")
    ctx, _ = with_ai(docs, answers={"clinical": {"admission": {"verdict": "maybe?", "reason": "", "facts": ["F1"]}}})
    assert run(ctx)["M-01"].status == "not_evaluable"            # unknown verdict counts as unclear


def test_irrelevant_test_flagged_with_its_amount():
    docs = genuine_docs()
    d(docs, "final_bill").tables["lines"].append(row(date="2026-04-05", category="Investigations",
                                                     description="MRI Brain (plain)", qty=1, rate=6500, amount=6500))

    def answer(text):
        mri = next(ln.split()[0] for ln in text.splitlines() if "MRI Brain" in ln)
        assert "USG" not in text.split("For each billed test")[1]  # Rs. 1,200: below the floor, not asked
        return {"tests": [
            {"test": mri, "verdict": "not_relevant", "reason": "No neurological complaint.", "facts": []}]}
    ctx, _ = with_ai(docs, answers={"clinical": answer})
    o = run(ctx)["M-02"]
    assert o.status == "flagged" and len(o.findings) == 1
    f = o.findings[0]
    assert "MRI Brain" in f.title and f.amount_at_stake == Decimal("6500.00") and f.severity == "amber"
    assert f.evidence[0]["page_id"] and f.evidence[0]["bbox"]


def icu_docs():
    docs = medical_docs()
    d(docs, "final_bill").tables["lines"].append(row(date="2026-04-05", category="ICU", description="ICU charges",
                                                     qty=2, rate=6000, amount=12000))
    d(docs, "nursing_chart").tables["days"][0]["location"] = v("ICU", page="p9")
    return docs


def test_icu_with_calm_vitals_is_asked_and_flagged():
    def answer(text):
        icu = [ln.split()[0] for ln in text.splitlines() if "ICU" in ln and ln.startswith("F")]
        return {"tests": [], "icu": {"verdict": "not_justified", "reason": "Normal vitals in ICU.", "facts": icu}}
    ctx, ai = with_ai(icu_docs(), answers={"clinical": answer})
    o = run(ctx)["M-04"]
    assert "icu" in ai.calls[0]["schema"]["properties"]
    assert o.status == "flagged" and o.findings[0].amount_at_stake == Decimal("12000.00")
    assert o.findings[0].needs_medical_review and o.findings[0].severity == "amber"


def test_icu_with_abnormal_vitals_or_cardiac_case_not_asked():
    docs = icu_docs()
    d(docs, "nursing_chart").tables["days"][0]["rr"] = v("26", page="p9")
    ctx, ai = with_ai(docs, answers={"clinical": OK})
    o = run(ctx)["M-04"]
    assert o.status == "passed" and "rr 26" in (o.detail or "")
    assert all("icu" not in c["schema"]["properties"] for c in ai.calls)
    docs = icu_docs()
    d(docs, "discharge_summary").fields["diagnosis"] = v("Non-ST elevation myocardial infarction", page="p3")
    ctx, _ = with_ai(docs, answers={"clinical": OK})
    assert "cardiac" in (run(ctx)["M-04"].detail or "")


def test_severity_only_asked_when_its_lab_marker_is_normal():
    docs = medical_docs()
    d(docs, "discharge_summary").fields["diagnosis"] = v("Dengue fever with severe thrombocytopenia", page="p3")
    d(docs, "lab_report").tables["results"] = [row(parameter="Platelet Count", value="2,10,000", unit="/cumm",
                                                   reference="1,50,000 - 4,10,000")]

    def answer(text):
        plt = next(ln.split()[0] for ln in text.splitlines() if "Platelet" in ln)
        return {"tests": [], "severity": {"verdict": "not_supported", "reason": "Platelets normal.", "facts": [plt]}}
    ctx, _ = with_ai(docs, answers={"clinical": answer})
    o = run(ctx)["M-06"]
    assert o.status == "flagged" and "severe thrombocytopenia" in o.findings[0].explanation
    assert o.findings[0].evidence[0]["quote"].startswith(fact_id(ctx, "final diagnosis"))
    d(docs, "lab_report").tables["results"][0]["value"] = v("42,000", page="p6")
    ctx, ai = with_ai(docs, answers={"clinical": answer})
    o = run(ctx)["M-06"]
    assert o.status == "passed" and all("severity" not in c["schema"]["properties"] for c in ai.calls)
    d(docs, "discharge_summary").fields["diagnosis"] = v("Severe sepsis", page="p3")
    ctx, _ = with_ai(docs, answers={"clinical": answer})
    assert run(ctx)["M-06"].status == "not_applicable"          # no single lab marker: not asked


# ---------------------------------------------------------------- coding review: X-08, B-11


def test_changed_diagnosis_without_explanation():
    docs = genuine_docs()
    d(docs, "preauth_form").fields["preauth_diagnosis"] = v("Right ureteric colic", page="p2")
    ctx, ai = with_ai(docs, answers={"clinical": OK, "coding": {
        "diagnosis_change": {"verdict": "not_explained", "reason": "No finding explains it.", "facts": []}}})
    o = run(ctx)["X-08"]
    assert o.status == "flagged"
    quotes = " ".join(e["quote"] for e in o.findings[0].evidence)
    assert "ureteric colic" in quotes and "cholelithiasis" in quotes
    assert any("diagnosis_change" in c["schema"]["properties"] for c in ai.calls)


def test_changed_diagnosis_explained_passes():
    docs = genuine_docs()
    d(docs, "preauth_form").fields["preauth_diagnosis"] = v("Acute abdomen", page="p2")
    ctx, _ = with_ai(docs, answers={"clinical": OK, "coding": {
        "diagnosis_change": {"verdict": "explained", "reason": "USG showed calculi.", "facts": ["F10"]}}})
    assert run(ctx)["X-08"].status == "passed"


def test_upcoding_only_asked_when_billed_name_differs():
    docs = genuine_docs()
    upcoded = "Laparoscopic cholecystectomy for gangrenous gallbladder with drain"
    line(docs, "Surgeon fee")["description"] = v(f"Surgeon fee - {upcoded}", page="p4")

    def coding(text):
        return {"upcoding": {"verdict": "not_supported", "reason": "Notes describe a routine procedure.",
                             "facts": [fact_id(ctx, "procedure (OT notes)")]}}
    ctx, ai = with_ai(docs, answers={"clinical": OK, "coding": coding,
                                     "pairs": {"pairs": [{"pair": 1, "verdict": "different", "reason": "-"}]}})
    out = run(ctx)
    assert out["B-11"].status == "flagged" and out["B-11"].findings[0].severity == "amber"
    assert "gangrenous" in out["B-11"].findings[0].explanation
    assert out["X-06"].status == "flagged"            # the rule part flags the name mismatch too
    assert "also reads them as different" in out["X-06"].findings[0].explanation


def test_x06_same_procedure_in_other_words_is_amber_not_red():
    docs = genuine_docs()
    d(docs, "discharge_summary").fields["procedures"] = v("Lap. chole", ["Lap. chole"], "p3")
    ctx, _ = with_ai(docs, answers={"clinical": OK, "pairs": lambda text: {"pairs": [
        {"pair": n, "verdict": "same", "reason": "Abbreviation."} for n in range(1, 5)]}})
    o = run(ctx)["X-06"]
    assert o.status == "flagged" and {f.severity for f in o.findings} == {"amber"}
    assert all("same procedure written differently" in f.explanation for f in o.findings)
    ctx, _ = with_ai(docs, answers={"clinical": OK, "pairs": {"pairs": []}})          # no answer: rule stands
    assert {f.severity for f in run(ctx)["X-06"].findings} == {"red"}


# ---------------------------------------------------------------- M-03 repeat with a documented reason


def test_repeat_with_documented_reason_becomes_info():
    docs = genuine_docs()
    again = copy.deepcopy(d(docs, "imaging_report"))
    again.key = "imaging_report:2"
    again.fields["report_date"] = v("05/04/2026 20:00", "2026-04-05T20:00:00", "p7")
    docs.append(again)
    d(docs, "discharge_summary").fields["course"] = v("Sudden abdominal pain at 7 pm; USG repeated.", page="p3")

    def repeats(text):
        course = next(ln.split()[0] for ln in text.splitlines() if "Sudden abdominal pain" in ln)
        return {"repeats": [{"repeat": 1, "verdict": "reason_documented", "reason": "Pain worsened.",
                             "facts": [course]}]}
    ctx, _ = with_ai(docs, answers={"clinical": OK, "repeats": repeats})
    o = run(ctx)["M-03"]
    assert o.status == "flagged" and o.findings[0].severity == "info"
    assert "documented reason" in o.findings[0].explanation and len(o.findings[0].evidence) == 3
    ctx, _ = with_ai(docs, answers={"clinical": OK, "repeats": {"repeats": [
        {"repeat": 1, "verdict": "reason_documented", "reason": "?", "facts": []}]}})
    assert run(ctx)["M-03"].findings[0].severity == "amber"     # no citation: stays amber


# ---------------------------------------------------------------- C-02 on a scan: the model looks at the page


def scanned_summary(docs):
    ds = d(docs, "discharge_summary")
    ds.fields["signed"] = Value("", False, "p3", None, 0.5, "ocr")
    del ds.fields["doctor_reg_no"]
    return docs


def test_signature_checked_on_the_page_image():
    docs = scanned_summary(genuine_docs())
    ai = ScriptedAI(answers={"clinical": OK, "signature": {"signature": "absent", "stamp": "absent",
                                                           "registration_number": ""}})
    ctx = context(docs)
    for p in ctx.pages:
        if p.id == "p3":
            p.source = "ocr"
            p.image = lambda: _png()
    ctx.ai = AiReviewer(ai, MemoryCache())
    o = run(ctx)["C-02"]
    assert o.status == "flagged" and "no doctor's signature" in o.findings[0].explanation
    assert any(c["content"][0]["type"] == "image" for c in ai.calls)
    ai.answers["signature"] = {"signature": "present", "stamp": "present", "registration_number": "MMC/1999/88069"}
    ctx.ai = AiReviewer(ai, MemoryCache())
    assert run(ctx)["C-02"].status == "passed"
    ctx.ai = AiReviewer(ScriptedAI(answers={"clinical": OK}, vision=False), MemoryCache())
    assert run(ctx)["C-02"].status == "not_evaluable"         # text-only model: cannot look


def _png() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("L", (200, 280), 255).save(buffer, "PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------- cache and failures


def test_cached_answers_are_reused():
    cache = MemoryCache()
    first = ScriptedAI(answers={"clinical": OK})
    ctx = context(medical_docs())
    ctx.ai = AiReviewer(first, cache)
    run(ctx)
    second = ScriptedAI(error="should not be called")
    ctx = context(medical_docs())
    ctx.ai = AiReviewer(second, cache)
    logged = []
    ctx.ai.log_call = lambda purpose, result, error, cached: logged.append((purpose, cached))
    out = run(ctx)
    assert not second.calls and out["M-01"].status == "passed"
    assert logged == [("check:clinical", True)]


def test_fatal_error_stops_calls_and_leaves_checks_pending():
    docs = medical_docs()
    d(docs, "preauth_form").fields["preauth_diagnosis"] = v("Right ureteric colic", page="p2")
    ctx, ai = with_ai(docs, error="Ollama is not running", fatal=True)
    out = run(ctx)
    assert len(ai.calls) == 1
    for rule in ("M-01", "X-08"):
        assert out[rule].status == "pending_ai" and "unavailable" in (out[rule].detail or "")
    assert not [o for o in out.values() if o.status == "error"]


def test_failed_call_is_pending_not_a_finding():
    ctx, ai = with_ai(medical_docs(), error="Model returned broken JSON")
    out = run(ctx)
    assert out["M-01"].status == "pending_ai" and "broken JSON" in (out["M-01"].detail or "")
    assert not [o for o in out.values() if o.findings]


def test_procedure_name_of_the_package_is_known():
    ctx, _ = with_ai(answers={"clinical": OK})
    assert any(PROC in f.text for f in ctx.ai.facts(ctx) if f.kind == "billed procedure")
