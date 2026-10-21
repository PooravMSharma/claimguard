"""X-layer: do the documents in one claim tell the same story?"""

import re
from collections import Counter
from datetime import timedelta

from app.extraction.model import DocExtract, Value
from app.models.enums import BillCategory
from app.rules.checks.common import (
    code,
    label,
    lenient,
    moment,
    num_text,
    rohini,
    rs,
    same_code,
    same_person,
    similar,
)
from app.rules.context import ClaimContext
from app.rules.engine import Flag, Result, check, flagged, not_applicable, not_evaluable, passed
from app.rules.reference import norm


def _reference_value(ctx: ClaimContext, name: str, order: list[str], key) -> tuple[DocExtract, Value] | None:
    """The value most documents agree on; ties go to the most trusted document."""
    values = ctx.every(name)
    if not values:
        return None
    counts = Counter(key(v.text) for _, v in values)
    top = max(counts.values())
    for t in order:
        for d, v in values:
            if d.doc_type == t and counts[key(v.text)] == top:
                return d, v
    return next((d, v) for d, v in values if counts[key(v.text)] == top)


@check("X-01")
def patient_identity(ctx: ClaimContext) -> Result:
    cfg = ctx.config_of("X-01")
    sim = cfg.get("name_word_similarity", 0.75)
    flags = []
    ref = _reference_value(ctx, "patient_name", ["claim_form", "discharge_summary", "final_bill"],
                           lambda t: " ".join(norm(t).split()))
    if ref is None:
        return not_evaluable("Patient name not read from any document.")
    rdoc, rval = ref
    for d, v in ctx.every("patient_name"):
        if d is rdoc or not ctx.usable(d, v) or not ctx.usable(rdoc, rval):
            continue
        slip = lenient(ctx, d, rdoc)
        if same_person(v.text, rval.text, lenient=slip,
                       word_similarity=cfg.get("name_word_similarity_scan", 0.6) if slip else sim):
            continue
        flags.append(Flag(
            f"Patient name differs on the {label(d.doc_type)}",
            f"The {label(d.doc_type)} is in the name '{v.text}'; the {label(rdoc.doc_type)} and other documents "
            f"say '{rval.text}'. Documents from another patient may have been added to this claim.",
            [ctx.ev(d, v), ctx.ev(rdoc, rval)], key=f"name|{d.key}|{norm(v.text)}"))
    uref = _reference_value(ctx, "uhid", ["discharge_summary", "final_bill"], code)
    if uref:
        udoc, uval = uref
        for d, v in ctx.every("uhid"):
            if d is udoc or not ctx.usable(d, v) or not ctx.usable(udoc, uval):
                continue
            if same_code(v.text, uval.text, lenient=lenient(ctx, d, udoc)):
                continue
            flags.append(Flag(
                f"Hospital patient ID (UHID) differs on the {label(d.doc_type)}",
                f"UHID '{v.text}' on the {label(d.doc_type)}; '{uval.text}' on the {label(udoc.doc_type)}.",
                [ctx.ev(d, v), ctx.ev(udoc, uval)], key=f"uhid|{d.key}|{code(v.text)}"))
    ages: list[tuple[DocExtract, Value, int]] = []
    for d, v in ctx.every("age"):
        n = ctx.num(v)
        if n is not None and ctx.usable(d, v):
            ages.append((d, v, int(n)))
    if ages:
        tol = cfg.get("age_tolerance_years", 1)
        common = Counter(a for _, _, a in ages).most_common(1)[0][0]
        base = next((d, v) for d, v, a in ages if a == common)
        for d, v, a in ages:
            if abs(a - common) > tol and not lenient(ctx, d):
                flags.append(Flag(
                    f"Patient age differs on the {label(d.doc_type)}",
                    f"Age {a} on the {label(d.doc_type)}; {common} on the {label(base[0].doc_type)}.",
                    [ctx.ev(d, v), ctx.ev(*base)], key=f"age|{d.key}|{a}"))
    genders = [(d, v, str(v.normalized or v.text)[:1].upper()) for d, v in ctx.every("gender")]
    genders = [(d, v, g) for d, v, g in genders if g in ("M", "F") and ctx.usable(d, v)]
    if genders:
        common_g = Counter(g for _, _, g in genders).most_common(1)[0][0]
        base_g = next((d, v) for d, v, g in genders if g == common_g)
        for d, v, g in genders:
            if g != common_g and not lenient(ctx, d):
                flags.append(Flag(
                    f"Patient gender differs on the {label(d.doc_type)}",
                    f"Gender {g} on the {label(d.doc_type)}; {common_g} on the {label(base_g[0].doc_type)}.",
                    [ctx.ev(d, v), ctx.ev(*base_g)], key=f"gender|{d.key}|{g}"))
    return flagged(*flags, detail=f"Identity compared across {len(ctx.every('patient_name'))} document(s).")


@check("X-02")
def dates_in_order(ctx: ClaimContext) -> Result:
    adm, dis = ctx.date_of("admission"), ctx.date_of("discharge")
    if adm is None or dis is None:
        return not_evaluable("Admission or discharge date not read.")
    if not (ctx.usable(adm[0], adm[1]) and ctx.usable(dis[0], dis[1])):
        return not_evaluable("Admission or discharge date unclear on the scan.")
    tol = timedelta(hours=ctx.config_of("X-02").get("tolerance_hours", 1))
    flags = []
    if dis[2] < adm[2] - tol:
        flags.append(Flag("Discharge before admission",
                          f"Discharge {moment(dis[2])} is before admission {moment(adm[2])}.",
                          [ctx.ev(dis[0], dis[1]), ctx.ev(adm[0], adm[1])], key="discharge<admission"))
    for d in ctx.docs_of("ot_notes"):
        v = d.fields.get("procedure_date")
        when = ctx.when(v)
        if v is None or when is None or not ctx.usable(d, v):
            continue
        if when > dis[2] + tol or when < adm[2] - tol:
            where = "after discharge" if when > dis[2] else "before admission"
            flags.append(Flag(
                f"Procedure dated {where}",
                f"The OT notes date the procedure {moment(when)}, {where} (admitted {moment(adm[2])}, discharged "
                f"{moment(dis[2])}).", [ctx.ev(d, v), ctx.ev(dis[0], dis[1]), ctx.ev(adm[0], adm[1])],
                key=f"procedure|{when.isoformat()}"))
    for d in ctx.docs:
        if d.doc_type not in ("lab_report", "imaging_report"):
            continue
        v = d.fields.get("report_date")
        when = ctx.when(v)
        if v is not None and when is not None and when > dis[2] + tol and ctx.usable(d, v):
            test = d.fields.get("test")
            flags.append(Flag(
                "Report dated after discharge",
                f"{test.text if test else 'A report'} is dated {moment(when)}, after discharge on {moment(dis[2])}.",
                [ctx.ev(d, v), ctx.ev(dis[0], dis[1])], key=f"report|{d.key}|{when.isoformat()}"))
    sub = ctx.date_of("submitted", ["claim_form"])
    # a claim form often carries only a date: compare whole days then
    dated_only = sub is not None and ":" not in sub[1].text
    if sub and ctx.usable(sub[0], sub[1]) and (
            sub[2].date() < dis[2].date() if dated_only else sub[2] < dis[2] - tol):
        flags.append(Flag(
            "Claim submitted before discharge",
            f"The claim form is dated {moment(sub[2])}, before discharge on {moment(dis[2])}.",
            [ctx.ev(sub[0], sub[1]), ctx.ev(dis[0], dis[1])], key="submitted<discharge"))
    return flagged(*flags, detail=f"Admitted {moment(adm[2])}, discharged {moment(dis[2])}.")


@check("X-03")
def reports_in_window(ctx: ClaimContext) -> Result:
    adm = ctx.date_of("admission")
    if adm is None or not ctx.usable(adm[0], adm[1]):
        return not_evaluable("Admission date not read clearly.")
    reports = [d for d in ctx.docs if d.doc_type in ("lab_report", "imaging_report")]
    if not reports:
        return not_applicable("No reports in the file.")
    term = ctx.ref.term("pre_hospitalisation_days", ctx.policy_record.product_code if ctx.policy_record else None)
    days = (term or {}).get("days") or ctx.config_of("X-03").get("pre_admission_days_if_no_policy_term", 30)
    earliest = adm[2] - timedelta(days=days)
    flags = []
    for d in reports:
        v = d.fields.get("report_date")
        when = ctx.when(v)
        if v is None or when is None or when >= earliest or not ctx.usable(d, v):
            continue
        test = d.fields.get("test")
        flags.append(Flag(
            "Report dated outside the claim period",
            f"{test.text if test else 'A report'} is dated {moment(when)}, more than {days} days before admission "
            f"on {moment(adm[2])} (the pre-hospitalisation window). It may belong to another episode.",
            [ctx.ev(d, v), ctx.ev(adm[0], adm[1])], key=f"{d.key}|{when.isoformat()}"))
    return flagged(*flags, detail=f"{len(reports)} report(s) inside the window from {moment(earliest)}.")


@check("X-04")
def medicines_charted(ctx: ClaimContext) -> Result:
    if not ctx.pharmacy_lines:
        return not_applicable("No pharmacy lines.")
    charted = ctx.charted_medicines
    if not charted:
        return not_evaluable("No medication chart read.")
    flags = []
    chart_docs = {d.key: d for d, _ in ctx.nursing_days}
    if any(ctx.scanned(d) for d in chart_docs.values()) and not all(
            ctx.usable(d, day["meds"]) for d, day in ctx.nursing_days if "meds" in day):
        return not_evaluable("Medication chart unclear on the scan.")
    for doc, row in ctx.pharmacy_lines:
        drug = row["drug"]
        name = norm(drug.text)
        if name in charted or not ctx.usable(doc, drug):
            continue
        threshold = 0.75 if lenient(ctx, doc) else 0.95
        if any(similar(name, c) >= threshold or (len(name) > 6 and (name in c or c in name)) for c in charted):
            continue
        amount = ctx.num(row.get("amount"))
        flags.append(Flag(
            f"Medicine billed but not in the medication chart: {drug.text}",
            f"The pharmacy bill charges {drug.text}" + (f" ({rs(amount)})" if amount is not None else "")
            + ", but the nursing medication record never shows it being given.",
            [ctx.ev(doc, drug)], amount_at_stake=amount, key=name))
    return flagged(*flags, detail=f"{len(ctx.pharmacy_lines)} pharmacy line(s) against {len(charted)} charted "
                                  "medicine(s).")


ICU = re.compile(r"\b(icu|iccu|micu|sicu|nicu|picu|ccu|intensive care|hdu)\b", re.I)


@check("X-05")
def icu_days_match(ctx: ClaimContext) -> Result:
    icu_lines = [line for line in ctx.bill_lines if line.category == BillCategory.ICU]
    if not icu_lines:
        return not_applicable("No ICU billed.")
    if not ctx.nursing_days:
        return not_evaluable("No nursing chart read.")
    if not all(ctx.usable(x.doc, x.values.get("qty")) for x in icu_lines) or not all(
            ctx.usable(d, row["location"]) for d, row in ctx.nursing_days if "location" in row):
        return not_evaluable("ICU days or nursing chart locations unclear on the scan.")
    billed = sum((line.qty or 0) for line in icu_lines)
    charted = [(d, row) for d, row in ctx.nursing_days if "location" in row and ICU.search(row["location"].text)]
    if billed <= len(charted):
        return passed(f"{num_text(billed)} ICU day(s) billed, {len(charted)} in the nursing chart.")
    line = icu_lines[0]
    extra = billed - len(charted)
    chart_doc = ctx.nursing_days[0][0]
    evidence = [ctx.ev(line.doc, line.main)]
    first_day = ctx.nursing_days[0][1]
    if "location" in first_day:
        evidence.append(ctx.ev(chart_doc, first_day["location"], f"Location: {first_day['location'].text}"))
    return flagged(Flag(
        "ICU days billed are not in the nursing chart",
        f"{num_text(billed)} ICU day(s) billed but the nursing chart shows {len(charted)} day(s) in ICU "
        f"(the other days are in the ward).", evidence,
        amount_at_stake=(line.rate or 0) * extra if line.rate else None, key=f"icu|{billed}|{len(charted)}"))


def _same_procedure(a: str, b: str) -> bool:
    return norm(a) == norm(b) or similar(a, b) >= 0.88


def _named_in(proc: str, others: list[str]) -> bool:
    """Named on the other document, even if it lists several procedures in one line."""
    joined = norm(" ".join(others))
    return any(_same_procedure(proc, o) for o in others) or (len(norm(proc)) > 8 and norm(proc) in joined)


@check("X-06")
def procedures_match(ctx: ClaimContext) -> Result:
    sources = {
        "final bill": [(line.doc, line.main, p) for line, p in ctx.billed_procedures],
        "OT notes": ctx.procedures("ot_notes"),
        "discharge summary": ctx.procedures("discharge_summary"),
    }
    if any(not ctx.usable(d, v) for items in sources.values() for d, v, _ in items):
        return not_evaluable("A procedure name is unclear on the scan.")
    present = {k: v for k, v in sources.items() if v}
    if not present or (len(present) == 1 and "discharge summary" in present
                       and all(re.match(r"(?i)medical management", p) for _, _, p in present["discharge summary"])):
        return not_applicable("No procedure billed or recorded.")
    if len(present) < 2:
        return passed("Only one document names the procedure.")
    flags = []
    names = list(present)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for doc, value, proc in present[a]:
                if _named_in(proc, [other for _, _, other in present[b]]):
                    continue
                theirs = "; ".join(p for _, _, p in present[b])
                d2, v2, _ = present[b][0]
                flags.append(Flag(
                    f"Procedure on the {a} differs from the {b}",
                    f"The {a} names '{proc}'; the {b} records '{theirs}'.",
                    [ctx.ev(doc, value, proc), ctx.ev(d2, v2, theirs)], key=f"{a}|{b}|{norm(proc)}"))
    return flagged(*flags, detail="Compared: " + ", ".join(names) + ".")


def _model_in(model: str, billed: str, slip: bool) -> bool:
    """Is this model code printed in the bill's implant description (one misread character allowed on scans)?"""
    if model in billed:
        return True
    if slip and len(model) >= 5:
        n = len(model)
        return any(sum(1 for a, b in zip(model, billed[i:i + n]) if a != b) <= 1
                   for i in range(0, len(billed) - n + 1))
    return False


@check("X-07")
def implant_matches(ctx: ClaimContext) -> Result:
    implants = [line for line in ctx.bill_lines if line.category == BillCategory.IMPLANT]
    if not implants:
        return not_applicable("No implant billed.")
    billed = "|".join(code(line.description) for line in implants)
    others: list[tuple[str, DocExtract, Value]] = []
    for d in ctx.docs_of("implant_sticker"):
        if "model" in d.fields:
            others.append(("implant sticker", d, d.fields["model"]))
    for d in ctx.docs_of("ot_notes") + ctx.docs_of("implant_invoice"):
        for row in d.tables.get("implants", []):
            if "model" in row:
                others.append((label(d.doc_type), d, row["model"]))
    if not others:
        return not_evaluable("No implant sticker, invoice or OT implant record read.")
    flags = []
    line = implants[0]
    if not all(ctx.usable(x.doc, x.main) for x in implants):
        return not_evaluable("Implant line unclear on the scan.")
    for where, d, v in others:
        model = code(v.text)
        if len(model) < 3 or not ctx.usable(d, v) or _model_in(model, billed, lenient(ctx, d, line.doc)):
            continue
        flags.append(Flag(
            f"Implant on the {where} differs from the bill",
            f"Bill: '{line.description}'. The {where} shows model '{v.text}'. The implant used should match the one "
            "billed.", [ctx.ev(line.doc, line.main), ctx.ev(d, v)],
            amount_at_stake=line.amount, key=f"{where}|{model}"))
    return flagged(*flags, detail=f"{len(others)} implant record(s) compared with the bill.")


@check("X-09")
def hospital_identity(ctx: ClaimContext) -> Result:
    flags = []
    values = [(d, v, rohini(v)) for d, v in ctx.every("rohini_id") if ctx.usable(d, v)]
    values = [(d, v, r) for d, v, r in values if r]
    if not values:
        return not_evaluable("No clear Rohini ID on the documents.")
    common = Counter(r for _, _, r in values).most_common(1)[0][0]
    base = next((d, v) for d, v, r in values if r == common)
    for d, v, r in values:
        if r != common and not same_code(r, common, lenient=lenient(ctx, d, base[0])):
            flags.append(Flag(
                f"Hospital Rohini ID differs on the {label(d.doc_type)}",
                f"Rohini ID {r} on the {label(d.doc_type)}; {common} on the {label(base[0].doc_type)}. "
                "Documents from another hospital may have been mixed in.",
                [ctx.ev(d, v), ctx.ev(*base)], key=f"rohini|{d.key}|{r}"))
    names = [(d, v) for d, v in ctx.every("hospital_name") if ctx.usable(d, v)]
    if names:
        sim = ctx.config_of("X-09").get("name_similarity", 0.8)
        ref = _reference_value(ctx, "hospital_name", ["claim_form", "discharge_summary"], norm)
        if ref and ctx.usable(*ref):
            for d, v in names:
                if d is ref[0] or similar(v.text, ref[1].text) >= (sim - 0.1 if lenient(ctx, d, ref[0]) else sim):
                    continue
                if norm(v.text) in norm(ref[1].text) or norm(ref[1].text) in norm(v.text):
                    continue
                flags.append(Flag(
                    f"Hospital name differs on the {label(d.doc_type)}",
                    f"'{v.text}' on the {label(d.doc_type)}; '{ref[1].text}' on the {label(ref[0].doc_type)}.",
                    [ctx.ev(d, v), ctx.ev(*ref)], key=f"name|{d.key}|{norm(v.text)}"))
    return flagged(*flags, detail=f"Rohini ID {common} on {len(values)} document(s).")
