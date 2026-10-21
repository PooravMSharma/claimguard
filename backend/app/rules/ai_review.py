"""The AI reviewer: judgement checks that rules alone cannot make (Step 10).

How it works:
- The claim record is turned into a numbered list of facts (F1, F2, ...): diagnoses, complaints, course in
  hospital, vitals by day, lab results, imaging impressions, billed tests, ICU days, procedures. Every fact keeps
  the document, page and box it was read from.
- The model answers fixed questions with a fixed set of verdicts, always including "unclear", and cites the
  fact ids its answer rests on. The ids map back to the page, so every AI finding shows its evidence.
- A finding is raised only on an explicit negative verdict about something the claim actually contains. An
  "unclear" answer, a missing answer or invented fact ids raise nothing.
- Deterministic gates decide which questions are asked at all (no ICU stay: no ICU question), so a model
  cannot flag what the claim does not contain, and most genuine claims need a single call.
- Answers are cached by (model, question, facts): re-running the checks on an unchanged claim makes no calls.
- A fatal error (no server, no model, no credit) stops further calls for the claim; the checks that needed
  the AI then report "pending_ai".

The reviewer works the same with a local model (Ollama) and with the Anthropic API.
"""

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol

from app.ai.client import AIError, AIProvider, AIResult, image_block, text_block
from app.extraction.model import DocExtract, Value
from app.models.enums import BillCategory
from app.rules.context import ClaimContext, Line
from app.rules.reference import norm

log = logging.getLogger(__name__)

MAX_FACT_CHARS = 400
MAX_FACTS = 120

SEVERITY_WORDS = re.compile(
    r"(?i)\b(severe|critical|shock|ha?emorrhagic|complicated|with complications?|sepsis|septic|failure|"
    r"thrombocytopenia|dehydration|respiratory distress|perforat\w*|gangren\w*)\b")
ICU_WORDS = re.compile(r"(?i)\b(icu|iccu|hdu|ccu|micu|sicu|intensive care)\b")

# What the model is told about its role. Kept short: small local models follow short instructions better.
SYSTEM = (
    "You are a medical claims reviewer for Indian health insurance. You receive numbered facts taken from one "
    "hospital claim file and answer fixed questions about it. Judge only from these facts and standard "
    "treatment guidelines (ICMR / MoHFW). Cite the ids of the facts your answer rests on, e.g. [\"F3\", \"F12\"]. "
    "If the facts are not enough to decide, answer \"unclear\"; never guess. A human adjudicator makes the "
    "decision; you only point out what needs a closer look. Answer in JSON only.")


@dataclass
class Fact:
    id: str                       # F1, F2, ...
    kind: str                     # what it is, as shown to the model: "diagnosis", "vitals", "billed test", ...
    text: str
    doc: DocExtract | None = None
    value: Value | None = None    # where it is printed
    ref: object | None = None     # the bill Line or report document it describes, when there is one

    def show(self) -> str:
        return f"{self.id} [{self.kind}] {self.text}"


@dataclass
class Judgement:
    verdict: str
    reason: str
    facts: list[Fact]             # cited facts that exist in the pack
    subject: Fact | None = None   # the test / repeat / pair the answer is about


class AiCache(Protocol):
    def get(self, key: str) -> dict | None: ...

    def put(self, key: str, purpose: str, model: str, data: dict) -> None: ...


# purpose, result (None on error or cache hit), error text, served from cache
CallLogger = Callable[[str, AIResult | None, str | None, bool], None]


# ---------------------------------------------------------------- facts


def _clean(text: object) -> str:
    out = re.sub(r"\s+", " ", str(text or "")).strip()
    return out if len(out) <= MAX_FACT_CHARS else out[:MAX_FACT_CHARS - 3] + "..."


def row_value(values: dict[str, Value]) -> Value | None:
    """One Value standing for a whole table row: its text, and a box around the cells on the first cell's page."""
    cells = [v for v in values.values() if v is not None]
    if not cells:
        return None
    first = cells[0]
    boxes = [v.bbox for v in cells if v.bbox and v.page_id == first.page_id]
    bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
            max(b[3] for b in boxes)] if boxes else None
    return Value(" | ".join(v.text for v in cells), None, first.page_id, bbox,
                 min(v.confidence for v in cells), "ai" if any(v.method == "ai" for v in cells) else first.method)


def _text(value: Value | None) -> str:
    if value is None:
        return ""
    if isinstance(value.normalized, list):
        return "; ".join(str(x) for x in value.normalized)
    return value.text


def bill_kind(line: Line) -> str | None:
    if line.category == BillCategory.INVESTIGATION:
        return "billed test"
    if line.category == BillCategory.ICU or ICU_WORDS.search(line.description):
        return "billed ICU"
    if line.category == BillCategory.ROOM:
        return "billed room"
    if re.match(r"(?i)\s*surgeon'?s? fee", line.description):
        return "billed procedure"
    return None


DS_FIELDS = [("diagnosis", "final diagnosis"), ("icd_code", "ICD-10 code"), ("complaints", "presenting complaints"),
             ("history", "past history"), ("examination", "examination on admission"),
             ("investigations_text", "investigations (summary)"), ("procedures", "procedure (discharge summary)"),
             ("course", "course in hospital"), ("condition", "condition at discharge"),
             ("advice", "advice on discharge"), ("ward_type", "ward")]


def build_facts(ctx: ClaimContext) -> list[Fact]:
    """The claim as numbered facts, most telling first (diagnosis and narrative, then vitals, labs, bill)."""
    facts: list[Fact] = []

    def add(kind: str, text: object, doc: DocExtract | None = None, value: Value | None = None,
            ref: object | None = None) -> None:
        clean = _clean(text)
        if clean and len(facts) < MAX_FACTS:
            facts.append(Fact(f"F{len(facts) + 1}", kind, clean, doc, value, ref))

    for d in ctx.docs_of("preauth_form"):
        for name, kind in (("preauth_diagnosis", "pre-auth diagnosis"),
                           ("proposed_treatment", "pre-auth proposed treatment")):
            if name in d.fields:
                add(kind, _text(d.fields[name]), d, d.fields[name])
    summaries = ctx.docs_of("discharge_summary")
    for d in summaries:
        for name, kind in DS_FIELDS:
            if name in d.fields:
                add(kind, _text(d.fields[name]), d, d.fields[name])
    if not any("diagnosis" in d.fields for d in summaries):
        hit = ctx.first("diagnosis", ["claim_form"])
        if hit:
            add("final diagnosis (claim form)", _text(hit[1]), hit[0], hit[1])
    for d in ctx.docs_of("ot_notes"):
        for name, kind in (("procedures", "procedure (OT notes)"), ("findings", "operative findings"),
                           ("anaesthesia", "anaesthesia")):
            if name in d.fields:
                add(kind, _text(d.fields[name]), d, d.fields[name])
    if ctx.admission and ctx.discharge:
        stay = (f"Admitted {ctx.admission:%d %b %Y %H:%M}, discharged {ctx.discharge:%d %b %Y %H:%M}: "
                f"{ctx.stay_nights} night(s).")
        if ctx.package and ctx.package.los_days:
            stay += f" Usual stay for {ctx.package.name}: {ctx.package.los_days} day(s)."
        hit = ctx.date_of("admission")
        add("stay", stay, hit[0] if hit else None, hit[1] if hit else None)
    for d, row in ctx.nursing_days:
        def cell(name: str, row: dict[str, Value] = row) -> str:
            return row[name].text if name in row else "-"
        text = (f"{cell('date')} in {cell('location')}: temp {cell('temp')} F, pulse {cell('pulse')}, "
                f"BP {cell('bp')}, RR {cell('rr')}, SpO2 {cell('spo2')}")
        if "meds" in row:
            text += f"; medicines: {row['meds'].text}"
        add("vitals", text, d, row_value(row))
    for d in ctx.docs_of("lab_report"):
        test = d.fields["test"].text if "test" in d.fields else "Lab test"
        when = d.fields["report_date"].text if "report_date" in d.fields else ""
        rows = d.tables.get("results", [])
        for row in rows:
            parts = [row[k].text for k in ("parameter", "value", "unit") if k in row]
            ref = f" (reference {row['reference'].text})" if "reference" in row else ""
            add("lab result", f"{test} {when}: {' '.join(parts)}{ref}", d, row_value(row), d)
        if not rows and "impression" in d.fields:
            add("lab result", f"{test} {when}: {d.fields['impression'].text}", d, d.fields["impression"], d)
    for d in ctx.docs_of("imaging_report"):
        test = d.fields["test"].text if "test" in d.fields else "Imaging"
        when = d.fields["report_date"].text if "report_date" in d.fields else ""
        for name in ("impression", "findings"):
            if name in d.fields:
                add("imaging", f"{test} {when}: {d.fields[name].text}", d, d.fields[name], d)
                break
    for line in ctx.bill_lines:
        kind = bill_kind(line)
        if kind is None:
            continue
        qty = f"{line.qty:g} x " if line.qty is not None and line.qty != 1 else ""
        amount = f" = Rs. {line.amount:,.2f}" if line.amount is not None else ""
        day = f" on {line.day:%d %b %Y}" if line.day else ""
        add(kind, f"{qty}{line.description}{amount}{day}", line.doc, row_value(line.values) or line.main, line)
    return facts


# ---------------------------------------------------------------- gates (what is worth asking)


def similar(a: object, b: object) -> float:
    x, y = norm(a), norm(b)
    return SequenceMatcher(None, x, y).ratio() if x and y else 0.0


def icu_facts(facts: list[Fact]) -> list[Fact]:
    return [f for f in facts if f.kind == "billed ICU" or (f.kind == "vitals" and ICU_WORDS.search(f.text))]


NEGATED = re.compile(r"(?i)\b(without|no|not|ruled out)\s+[a-z ]+")
OBSTETRIC = re.compile(r"(?i)\b(deliver\w*|labou?r|pregnan\w*|caesarean|c-section|lscs|gestation|obstetric)\b")
CARDIAC = re.compile(r"(?i)\b(myocardial|infarction|stemi|nstemi|acs|angina|ptca|angioplasty|stent|cabg|"
                     r"arrhythmia|heart failure|cardiac)\b")

# Vitals a calm adult patient shows. A small local model is not reliable enough to overrule abnormal vitals,
# so the AI is only asked about admissions and ICU days whose recorded vitals are all inside these ranges.
NORMAL = {"temp": (96.5, 100.3), "pulse": (55, 100), "rr": (10, 20), "spo2": (95, 100), "sbp": (95, 140),
          "dbp": (55, 90)}


def claimed_severity(ctx: ClaimContext) -> str | None:
    """The diagnosis, when its wording claims a severity that lab values or vitals should show."""
    hit = ctx.first("diagnosis")
    if hit and SEVERITY_WORDS.search(NEGATED.sub(" ", hit[1].text)):
        return hit[1].text
    return None


# Severity words whose truth one lab value shows. Others (dehydration, sepsis...) have no single marker: the
# model is not asked about them, because a small model cannot weigh a whole picture reliably.
SEVERITY_MARKERS = [
    (re.compile(r"(?i)thrombocytopen"), re.compile(r"(?i)platelet"), "low", "platelet count"),
    (re.compile(r"(?i)ana?emi"), re.compile(r"(?i)ha?emoglobin|\bhb\b"), "low", "haemoglobin"),
    (re.compile(r"(?i)renal failure|kidney injury|\baki\b"), re.compile(r"(?i)creatinine"), "high", "creatinine"),
    (re.compile(r"(?i)hyponatra?emi"), re.compile(r"(?i)sodium"), "low", "sodium"),
    (re.compile(r"(?i)hypokala?emi"), re.compile(r"(?i)potassium"), "low", "potassium"),
]


def reference_range(text: str) -> tuple[float | None, float | None]:
    t = text.replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", t)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"<\s*(\d+(?:\.\d+)?)", t)
    if m:
        return None, float(m.group(1))
    m = re.search(r">\s*(\d+(?:\.\d+)?)", t)
    return (float(m.group(1)), None) if m else (None, None)


def severity_gate(ctx: ClaimContext) -> tuple[str, str | None]:
    """("ask", None) when every reading of the lab marker for the claimed severity is normal;
    otherwise (status, reason): "no_marker", "no_readings" or "supported"."""
    diagnosis = claimed_severity(ctx) or ""
    for term, marker, direction, label in SEVERITY_MARKERS:
        if not term.search(diagnosis):
            continue
        readings = []
        for d in ctx.docs_of("lab_report"):
            for row in d.tables.get("results", []):
                if "parameter" in row and marker.search(row["parameter"].text) and "value" in row:
                    value = _number(row["value"].text)
                    low, high = reference_range(row["reference"].text) if "reference" in row else (None, None)
                    if value is not None and (low is not None or high is not None):
                        readings.append((row["parameter"].text, value, low, high))
        if not readings:
            return "no_readings", f"no {label} result with a reference range"
        for name, value, low, high in readings:
            if (direction == "low" and low is not None and value < low) or \
                    (direction == "high" and high is not None and value > high):
                return "supported", f"{name} {value:g} is outside the reference range"
        return "ask", None
    return "no_marker", "no single lab value shows this severity"


def _number(text: str) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return float(m.group()) if m else None


def vitals_of(text_or_row: str | dict[str, Value]) -> dict[str, float]:
    """temp/pulse/rr/spo2/sbp/dbp from a nursing-chart row or an 'examination on admission' sentence."""
    out: dict[str, float] = {}
    if isinstance(text_or_row, dict):
        for name in ("temp", "pulse", "rr", "spo2"):
            if name in text_or_row and (n := _number(text_or_row[name].text)) is not None:
                out[name] = n
        bp = text_or_row["bp"].text if "bp" in text_or_row else ""
    else:
        text = text_or_row
        for name, pattern in (("temp", r"temp\w*\s*:?\s*(\d+(?:\.\d+)?)"), ("pulse", r"pulse\s*:?\s*(\d+)"),
                              ("rr", r"\bRR\s*:?\s*(\d+)"), ("spo2", r"spo2\s*:?\s*(\d+)")):
            m = re.search(pattern, text, re.I)
            if m:
                out[name] = float(m.group(1))
        m = re.search(r"\bBP\s*:?\s*(\d+\s*/\s*\d+)", text, re.I)
        bp = m.group(1) if m else ""
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)", bp)
    if m:
        out["sbp"], out["dbp"] = float(m.group(1)), float(m.group(2))
    return out


def abnormal_vitals(vitals: dict[str, float]) -> list[str]:
    return [f"{k} {v:g}" for k, v in vitals.items() if k in NORMAL and not NORMAL[k][0] <= v <= NORMAL[k][1]]


def admission_gate(ctx: ClaimContext) -> str | None:
    """Why the admission is plainly justified without asking the model; None when it is worth asking."""
    if ctx.billed_procedures or ctx.docs_of("ot_notes"):
        return "a procedure was done"
    if any(not re.match(r"(?i)\s*(none|nil|-|medical management|conservative)", p)
           for _, _, p in ctx.procedures("discharge_summary")):
        return "a procedure was done"
    words = " ".join(v.text for name in ("diagnosis", "complaints") for _, v in ctx.every(name))
    if OBSTETRIC.search(words):
        return "obstetric admission"
    if claimed_severity(ctx):
        return "the diagnosis claims a severity or complication"
    if icu_days(ctx):
        return "ICU stay (judged under M-04)"
    first = [vitals_of(row) for _, row in ctx.nursing_days[:1]]
    exam = ctx.first("examination", ["discharge_summary"])
    if exam:
        first.append(vitals_of(exam[1].text))
    bad = [x for v in first for x in abnormal_vitals(v)]
    if bad:
        return "abnormal vitals on admission (" + ", ".join(bad) + ")"
    if not any(first):
        return "no vitals recorded on admission to judge by"
    return None


def icu_days(ctx: ClaimContext) -> list[dict[str, Value]]:
    return [row for _, row in ctx.nursing_days if "location" in row and ICU_WORDS.search(row["location"].text)]


def icu_gate(ctx: ClaimContext) -> str | None:
    """Why the ICU stay is plainly justified without asking the model; None when it is worth asking."""
    words = " ".join(v.text for name in ("diagnosis", "complaints", "procedures", "course")
                     for _, v in ctx.every(name))
    if CARDIAC.search(words):
        return "cardiac condition (ICCU care is standard)"
    if ctx.billed_procedures or ctx.docs_of("ot_notes"):
        return "post-operative ICU care"
    days = icu_days(ctx)
    if not days:
        return "no ICU day charted with vitals to judge by"
    bad = [x for row in days for x in abnormal_vitals(vitals_of(row))]
    if bad:
        return "abnormal vitals on ICU days (" + ", ".join(bad) + ")"
    if not any(vitals_of(row) for row in days):
        return "no vitals charted for the ICU days"
    return None


def changed_diagnosis(ctx: ClaimContext) -> tuple[Fact, Fact] | None:
    """(pre-auth diagnosis, final diagnosis) facts when the wording changed, else None."""
    facts = ctx.ai.facts(ctx) if ctx.ai else build_facts(ctx)
    pre = next((f for f in facts if f.kind == "pre-auth diagnosis"), None)
    final = next((f for f in facts if f.kind.startswith("final diagnosis")), None)
    if pre is None or final is None:
        return None
    a, b = norm(pre.text), norm(final.text)
    if not a or not b or a == b or a in b or b in a or similar(a, b) >= 0.8:
        return None
    return pre, final


def baseline_test(name: str, baseline: list[str]) -> bool:
    low = norm(name)
    return any(norm(b) and norm(b) in low for b in baseline)


# ---------------------------------------------------------------- the reviewer


def _part(verdicts: list[str]) -> dict:
    return {"type": "object", "properties": {
        "verdict": {"type": "string", "enum": verdicts},
        "reason": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "string"}}},
        "required": ["verdict", "reason", "facts"]}


def _ids(raw: object) -> list[str]:
    items = raw if isinstance(raw, list) else re.findall(r"F?\d+", str(raw or ""))
    out = []
    for item in items:
        m = re.search(r"(\d+)", str(item))
        if m and f"F{int(m.group(1))}" not in out:
            out.append(f"F{int(m.group(1))}")
    return out


class AiReviewer:
    """Asks the model the judgement questions for one claim. Create one per claim run."""

    def __init__(self, provider: AIProvider, cache: AiCache | None = None, log_call: CallLogger | None = None):
        self.provider = provider
        self.cache = cache
        self.log_call = log_call
        self.fatal: str | None = None
        self.errors: dict[str, str] = {}
        self._answers: dict[str, dict | None] = {}
        self._facts: tuple[int, list[Fact]] | None = None

    @property
    def model_version(self) -> str:
        return f"{self.provider.name}:{self.provider.model}"

    @property
    def vision(self) -> bool:
        return bool(getattr(self.provider, "vision", True))

    def unavailable(self, purpose: str) -> str:
        if self.fatal:
            return f"AI reviewer unavailable: {self.fatal}"
        return f"AI review failed: {self.errors.get(purpose, 'no usable answer')}"

    def facts(self, ctx: ClaimContext) -> list[Fact]:
        if self._facts is None or self._facts[0] != id(ctx):
            self._facts = (id(ctx), build_facts(ctx))
        return self._facts[1]

    # -------------------------------------------------- one call, cached

    def ask(self, purpose: str, content: list[dict], schema: dict, max_tokens: int = 2000,
            system: str = SYSTEM) -> dict | None:
        key = hashlib.sha256(json.dumps([self.model_version, purpose, system, content, schema], sort_keys=True,
                                        default=str).encode()).hexdigest()
        if key in self._answers:
            return self._answers[key]
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                self._answers[key] = hit
                if self.log_call:
                    self.log_call(purpose, None, None, True)
                return hit
        if self.fatal:
            return None
        try:
            result = self.provider.structured(system=system, content=content, tool_name="claim_review",
                                              tool_description="Answers to the claim review questions",
                                              schema=schema, max_tokens=max_tokens)
        except AIError as exc:
            self.errors[purpose] = str(exc)[:300]
            if exc.fatal:
                self.fatal = str(exc)[:300]
            log.warning("AI %s failed: %s", purpose, exc)
            if self.log_call:
                self.log_call(purpose, None, str(exc)[:1000], False)
            self._answers[key] = None
            return None
        if self.log_call:
            self.log_call(purpose, result, None, False)
        if self.cache is not None:
            self.cache.put(key, purpose, self.model_version, result.data)
        self._answers[key] = result.data
        return result.data

    def _judgement(self, ctx: ClaimContext, raw: object, verdicts: list[str]) -> Judgement:
        if not isinstance(raw, dict):
            return Judgement("unclear", "No answer.", [])
        verdict = str(raw.get("verdict", "")).strip().lower().replace(" ", "_")
        by_id = {f.id: f for f in self.facts(ctx)}
        cited = [by_id[i] for i in _ids(raw.get("facts")) if i in by_id]
        return Judgement(verdict if verdict in verdicts else "unclear", _clean(raw.get("reason")) or "-", cited)

    def _facts_block(self, ctx: ClaimContext) -> str:
        return "Claim facts (id, kind, text):\n" + "\n".join(f.show() for f in self.facts(ctx))

    # -------------------------------------------------- clinical review: M-01, M-02, M-04, M-06

    def _tests_to_judge(self, ctx: ClaimContext) -> list[Fact]:
        cfg = ctx.config_of("M-02")
        baseline = cfg.get("baseline_tests", [])
        floor = cfg.get("min_amount_rs", 3000)
        return [f for f in self.facts(ctx) if f.kind == "billed test" and isinstance(f.ref, Line)
                and not baseline_test(f.ref.description, baseline)
                and f.ref.amount is not None and f.ref.amount >= floor]

    def _clinical(self, ctx: ClaimContext) -> dict | None:
        facts = self.facts(ctx)
        if not facts:
            return None
        questions: list[str] = []
        props: dict = {}
        if admission_gate(ctx) is None:
            questions.append(
                "admission: Did the condition, as documented, need admission to hospital? \"needed\" if the documented "
                "complaints, vitals, lab values or treatment (IV medicines, monitoring, surgery) needed an inpatient "
                "stay; \"not_needed\" only if everything documented is mild and is normally treated as an outpatient "
                "(OPD); \"unclear\" if the facts are not enough. Cite the facts that show it.")
            props["admission"] = _part(["needed", "not_needed", "unclear"])
        tests = self._tests_to_judge(ctx)
        if tests:
            questions.append(
                "tests: For each billed test " + ", ".join(f.id for f in tests) + ", is it relevant to the "
                "documented diagnosis, complaints or findings? One entry per test, with \"test\" set to its fact id. "
                "\"not_relevant\" only if nothing documented gives a reason for doing it.")
            item = _part(["relevant", "not_relevant", "unclear"])
            item["properties"]["test"] = {"type": "string"}
            item["required"] = ["test", *item["required"]]
            props["tests"] = {"type": "array", "items": item}
        icu = icu_facts(facts)
        if icu and icu_gate(ctx) is None:
            questions.append(
                "icu: The patient was in ICU (" + ", ".join(f.id for f in icu) + "). Do the vitals, lab values and "
                "treatment recorded for those days justify ICU care? \"not_justified\" only if vitals and labs on "
                "those days were normal and no ICU-level care (ventilation, inotropes, close monitoring of an "
                "unstable patient) is recorded.")
            props["icu"] = _part(["justified", "not_justified", "unclear"])
        severity = claimed_severity(ctx)
        if severity and severity_gate(ctx)[0] == "ask":
            questions.append(
                f"severity: The diagnosis is \"{severity}\". Do the documented lab values and vitals support the "
                "severity it claims? \"not_supported\" only if the documented values contradict it (for example "
                "\"severe thrombocytopenia\" while every platelet count is in the normal range).")
            props["severity"] = _part(["supported", "not_supported", "unclear"])
        if not props:
            return {}                     # nothing worth asking: no call
        schema = {"type": "object", "properties": props, "required": list(props)}
        prompt = self._facts_block(ctx) + "\n\nQuestions (answer each in the JSON field of the same name):\n" + \
            "\n".join(f"- {q}" for q in questions)
        return self.ask("check:clinical", [text_block(prompt)], schema)

    def admission(self, ctx: ClaimContext) -> Judgement | None:
        answer = self._clinical(ctx)
        return None if answer is None else self._judgement(ctx, answer.get("admission"),
                                                           ["needed", "not_needed", "unclear"])

    def tests(self, ctx: ClaimContext) -> list[Judgement] | None:
        """One judgement per billed test asked about (baseline tests are never asked)."""
        tests = self._tests_to_judge(ctx)
        if not tests:
            return []
        answer = self._clinical(ctx)
        if answer is None:
            return None
        by_id = {f.id: f for f in tests}
        out: list[Judgement] = []
        raw = answer.get("tests")
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            ids = _ids(item.get("test"))
            subject = by_id.get(ids[0]) if ids else None
            if subject is None or any(j.subject is subject for j in out):
                continue
            j = self._judgement(ctx, item, ["relevant", "not_relevant", "unclear"])
            j.subject = subject
            out.append(j)
        return out

    def icu(self, ctx: ClaimContext) -> Judgement | None:
        answer = self._clinical(ctx)
        return None if answer is None else self._judgement(ctx, answer.get("icu"),
                                                           ["justified", "not_justified", "unclear"])

    def severity(self, ctx: ClaimContext) -> Judgement | None:
        answer = self._clinical(ctx)
        return None if answer is None else self._judgement(ctx, answer.get("severity"),
                                                           ["supported", "not_supported", "unclear"])

    # -------------------------------------------------- coding review: X-08, B-11

    def _upcoding_subjects(self, ctx: ClaimContext) -> list[Fact]:
        """Billed procedures whose name is not the one the OT notes / discharge summary record."""
        facts = self.facts(ctx)
        recorded = [norm(p) for _, _, p in ctx.procedures("ot_notes") + ctx.procedures("discharge_summary")]
        if not recorded:
            return []
        out = []
        for f in facts:
            if f.kind != "billed procedure" or not isinstance(f.ref, Line):
                continue
            m = re.match(r"(?i)\s*surgeon'?s? fee\s*[-–:]\s*(.+)", f.ref.description)
            name = norm(m.group(1)) if m else ""
            if name and name not in recorded:
                out.append(f)
        return out

    def _coding(self, ctx: ClaimContext) -> dict | None:
        change = changed_diagnosis(ctx)
        billed = self._upcoding_subjects(ctx)
        if change is None and not billed:
            return {}
        questions, props = [], {}
        if change:
            pre, final = change
            questions.append(
                f"diagnosis_change: The pre-authorisation diagnosis was \"{pre.text}\" ({pre.id}); the final diagnosis "
                f"is \"{final.text}\" ({final.id}). \"same_condition\" if both name the same illness in different "
                "words; \"explained\" if documented findings (test results, course in hospital) explain the change, "
                "citing them; \"not_explained\" if nothing documented explains why the diagnosis changed.")
            props["diagnosis_change"] = _part(["same_condition", "explained", "not_explained", "unclear"])
        if billed:
            questions.append(
                "upcoding: The bill charges for " + ", ".join(f"\"{f.text}\" ({f.id})" for f in billed) + ". Do the "
                "OT notes and discharge summary (procedure performed, operative findings, course) support the "
                "procedure as billed, including any extra complexity its name claims (complicated, for perforation, "
                "with lavage, revision, bilateral, extended)? \"not_supported\" if the billed name claims more than "
                "the notes describe.")
            props["upcoding"] = _part(["supported", "not_supported", "unclear"])
        schema = {"type": "object", "properties": props, "required": list(props)}
        prompt = self._facts_block(ctx) + "\n\nQuestions (answer each in the JSON field of the same name):\n" + \
            "\n".join(f"- {q}" for q in questions)
        return self.ask("check:coding", [text_block(prompt)], schema)

    def diagnosis_change(self, ctx: ClaimContext) -> Judgement | None:
        change = changed_diagnosis(ctx)
        if change is None:
            return Judgement("same_condition", "Diagnosis wording did not change.", [])
        answer = self._coding(ctx)
        if answer is None:
            return None
        j = self._judgement(ctx, answer.get("diagnosis_change"),
                            ["same_condition", "explained", "not_explained", "unclear"])
        j.subject = change[0]
        return j

    def upcoding(self, ctx: ClaimContext) -> list[Judgement] | None:
        billed = self._upcoding_subjects(ctx)
        if not billed:
            return []
        answer = self._coding(ctx)
        if answer is None:
            return None
        j = self._judgement(ctx, answer.get("upcoding"), ["supported", "not_supported", "unclear"])
        out = []
        for f in billed:
            out.append(Judgement(j.verdict, j.reason, j.facts, f))
        return out

    # -------------------------------------------------- X-06: same procedure in different words?

    def same_procedure(self, pairs: list[tuple[str, str]]) -> list[str] | None:
        """For each (a, b): "same", "different" or "unclear"."""
        if not pairs:
            return []
        prompt = (
            "Each pair below names a surgical procedure as written on two documents of the same hospital claim. "
            "\"same\" only if both describe the same operation and differ only in wording, abbreviation or "
            "spelling (e.g. \"Lap. chole\" and \"Laparoscopic cholecystectomy\"). \"different\" if the approach "
            "(open or laparoscopic), organ, side or extent differs. \"unclear\" if you cannot tell.\n"
            + "\n".join(f"{n}. \"{a}\" | \"{b}\"" for n, (a, b) in enumerate(pairs, 1)))
        item = {"type": "object", "properties": {"pair": {"type": "integer"},
                                                 "verdict": {"type": "string", "enum": ["same", "different",
                                                                                         "unclear"]},
                                                 "reason": {"type": "string"}},
                "required": ["pair", "verdict", "reason"]}
        schema = {"type": "object", "properties": {"pairs": {"type": "array", "items": item}}, "required": ["pairs"]}
        answer = self.ask("check:procedures", [text_block(prompt)], schema, max_tokens=800)
        if answer is None:
            return None
        verdicts = ["unclear"] * len(pairs)
        for item in answer.get("pairs") or []:
            if isinstance(item, dict) and str(item.get("pair", "")).isdigit():
                n = int(item["pair"]) - 1
                v = str(item.get("verdict", "")).lower()
                if 0 <= n < len(pairs) and v in ("same", "different", "unclear"):
                    verdicts[n] = v
        return verdicts

    # -------------------------------------------------- M-03: a reason for repeating a test?

    def repeat_reasons(self, ctx: ClaimContext, repeats: list[str]) -> list[Judgement] | None:
        if not repeats:
            return []
        prompt = (self._facts_block(ctx) + "\n\nThese tests were repeated within a short time:\n"
                  + "\n".join(f"{n}. {r}" for n, r in enumerate(repeats, 1))
                  + "\nFor each repeat: is a reason for repeating it documented in the facts (a change in the "
                  "patient's condition, a complication, a doctor's note asking for it)? \"reason_documented\" only "
                  "if you can cite the fact that gives the reason; otherwise \"no_reason\" or \"unclear\".")
        item = _part(["reason_documented", "no_reason", "unclear"])
        item["properties"]["repeat"] = {"type": "integer"}
        item["required"] = ["repeat", *item["required"]]
        schema = {"type": "object", "properties": {"repeats": {"type": "array", "items": item}},
                  "required": ["repeats"]}
        answer = self.ask("check:repeats", [text_block(prompt)], schema, max_tokens=1000)
        if answer is None:
            return None
        out = [Judgement("unclear", "-", []) for _ in repeats]
        for item in answer.get("repeats") or []:
            if isinstance(item, dict) and str(item.get("repeat", "")).isdigit():
                n = int(item["repeat"]) - 1
                if 0 <= n < len(repeats):
                    out[n] = self._judgement(ctx, item, ["reason_documented", "no_reason", "unclear"])
        return out

    # -------------------------------------------------- C-02: signature, stamp, registration number (vision)

    def signature(self, png: bytes) -> dict | None:
        """{"signature": present|absent|unclear, "stamp": ..., "registration_number": str} from the page image."""
        if not self.vision:
            return None
        prompt = (
            "This is the last page of a hospital discharge summary. Look at the bottom of the page, where the "
            "treating doctor signs. Answer only from what you can see: is there a doctor's signature (handwritten "
            "or digital)? A hospital or doctor's stamp or seal? The doctor's medical council registration number "
            "(for example \"Reg. No. MMC/2005/69836\")? Copy the registration number exactly, or \"\" if none.")
        state = {"type": "string", "enum": ["present", "absent", "unclear"]}
        schema = {"type": "object", "properties": {"signature": state, "stamp": state,
                                                   "registration_number": {"type": "string"}},
                  "required": ["signature", "stamp", "registration_number"]}
        answer = self.ask("check:signature", [image_block(png), text_block(prompt)], schema, max_tokens=300)
        if answer is None:
            return None
        return {"signature": str(answer.get("signature", "unclear")).lower(),
                "stamp": str(answer.get("stamp", "unclear")).lower(),
                "registration_number": str(answer.get("registration_number") or "").strip()}
