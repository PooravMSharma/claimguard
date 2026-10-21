"""C-layer: are the documents a claim of this kind needs actually in the file?"""

import re

from app.extraction.model import DocExtract, Value
from app.models.enums import BillCategory
from app.rules.checks.common import doc_evidence, label, rs, similar
from app.rules.context import ClaimContext, Evidence
from app.rules.engine import (
    Flag,
    Result,
    check,
    flagged,
    not_applicable,
    not_evaluable,
    passed,
)
from app.rules.reference import norm

MEDICAL_MANAGEMENT = re.compile(r"(?i)^\s*(medical management|conservative|nil|none|not applicable|-)\s*$")


def is_surgical(ctx: ClaimContext) -> bool:
    if ctx.billed_procedures:
        return True
    return any(not MEDICAL_MANAGEMENT.match(p) for _, _, p in ctx.procedures("discharge_summary"))


@check("C-01")
def mandatory_documents(ctx: ClaimContext) -> Result:
    cfg = ctx.config_of("C-01")
    need = list(cfg.get("always", []))
    reasons: dict[str, str] = {}
    if ctx.facts.stage in cfg.get("cashless_stages", []):
        for t in cfg.get("cashless", []):
            need.append(t)
            reasons[t] = "cashless claim"
    if is_surgical(ctx):
        for t in cfg.get("surgical", []):
            need.append(t)
            reasons[t] = "procedure billed"
    if any(line.category == BillCategory.PHARMACY for line in ctx.bill_lines):
        for t in cfg.get("when_pharmacy_billed", []):
            need.append(t)
            reasons[t] = "pharmacy billed"
    missing = [t for t in dict.fromkeys(need) if not ctx.has(t)]
    if not missing:
        return passed(f"All {len(set(need))} required documents present.")
    names = [label(t) + (f" ({reasons[t]})" if t in reasons else "") for t in missing]
    anchor = ctx.doc("claim_form") or (ctx.docs[0] if ctx.docs else None)
    evidence = [doc_evidence(ctx, anchor, "Documents in the file")] if anchor else []
    return flagged(Flag(
        f"Missing document{'s' if len(missing) > 1 else ''}: {', '.join(label(t) for t in missing)}",
        f"For a {ctx.facts.stage.replace('_', ' ')} claim the file should contain: {', '.join(names)}. "
        "Not found among the uploaded pages. Ask the hospital or insured for them before deciding.",
        evidence, key="missing:" + ",".join(missing)))


@check("C-02")
def discharge_summary_signed(ctx: ClaimContext) -> Result:
    doc = ctx.doc("discharge_summary")
    if doc is None:
        return not_applicable("No discharge summary (see C-01).")
    signed = doc.fields.get("signed")
    reg = doc.fields.get("doctor_reg_no")
    problems = []
    if signed is not None and signed.normalized is False:
        problems.append("no doctor's signature block")
    if reg is None:
        problems.append("no registration number of the treating doctor")
    if not problems:
        return passed("Signed, with the doctor's registration number.")
    if ctx.scanned(doc) and not (signed is not None and signed.method in ("ai", "reviewer")):
        # OCR missing a signature or a number is weak evidence that it is not on the paper: look at the page
        return _signature_on_scan(ctx, doc, signed)
    evidence = [ctx.ev(doc, signed, "Signature: not found")] if signed is not None and signed.page_id else []
    if not evidence:
        evidence = [doc_evidence(ctx, doc, "Discharge summary")]
        evidence[0].page_id = doc.page_ids[-1] if doc.page_ids else None
    return flagged(Flag(
        "Discharge summary not properly signed",
        f"The discharge summary has {' and '.join(problems)}. IRDAI-standard claim documents need the treating "
        "doctor's signature, stamp and registration number.", evidence, key="|".join(problems)))


def _signature_on_scan(ctx: ClaimContext, doc: DocExtract, signed: Value | None) -> Result:
    """AI part of C-02: the model looks at the page where the doctor signs (vision models only)."""
    unclear = "Signature / registration number not found by OCR on the scan; check it on the page."
    page_id = signed.page_id if signed is not None and signed.page_id else (doc.page_ids[-1] if doc.page_ids else None)
    page = ctx.page(page_id) if page_id else None
    if ctx.ai is None or page is None or page.image is None or not ctx.ai.vision:
        return not_evaluable(unclear)
    seen = ctx.ai.signature(page.image())
    if seen is None:
        return not_evaluable(unclear + " " + ctx.ai.unavailable("check:signature"))
    reg = seen["registration_number"]
    if seen["signature"] == "present" and (reg or seen["stamp"] == "present"):
        return passed("AI: doctor's signature" + (f" and registration number {reg}" if reg else " and stamp")
                      + " seen on the page.")
    if seen["signature"] != "absent":
        return not_evaluable(unclear + " The AI reviewer could not tell either.")
    problems = ["no doctor's signature"]
    if seen["stamp"] == "absent":
        problems.append("no stamp")
    if not reg:
        problems.append("no registration number of the treating doctor")
    return flagged(Flag(
        "Discharge summary not properly signed",
        f"The AI reviewer looked at the page and found {', '.join(problems)}. IRDAI-standard claim documents need "
        "the treating doctor's signature, stamp and registration number. Confirm on the page.",
        [Evidence(doc.key, page_id, None, "Signature area: " + ", ".join(problems), False)],
        key="|".join(problems), model=ctx.ai.model_version))


@check("C-03")
def investigations_have_reports(ctx: ClaimContext) -> Result:
    billed = [line for line in ctx.bill_lines if line.category == BillCategory.INVESTIGATION]
    if not billed:
        return not_applicable("No investigations billed.")
    reports = [(d, d.fields["test"]) for d in ctx.docs if d.doc_type in ("lab_report", "imaging_report")
               and "test" in d.fields]
    used: set[int] = set()
    flags = []
    unclear = 0
    for line in billed:
        best, score = None, 0.0
        for i, (_, test) in enumerate(reports):
            if i in used:
                continue
            s = similar(test.text, line.description)
            a, b = norm(test.text), norm(line.description)
            if len(a) > 5 and (a in b or b in a):
                s = max(s, 0.9)
            if s > score:
                best, score = i, s
        if best is not None and score >= 0.8:
            used.add(best)
            continue
        if not ctx.usable(line.doc, line.main):
            unclear += 1
            continue
        flags.append(Flag(
            f"No report for billed test: {line.description}",
            f"'{line.description}' is billed for {rs(line.amount)} but no matching lab or imaging report is in "
            "the file. A billed investigation needs its report.",
            [ctx.ev(line.doc, line.main)], amount_at_stake=line.amount, key=f"{line.description}|{line.no}"))
    detail = f"{len(billed)} billed investigation(s) checked against {len(reports)} report(s)."
    if unclear and not flags:
        return not_evaluable(f"{unclear} billed test name(s) unclear on the scan. " + detail)
    return flagged(*flags, detail=detail)


@check("C-04")
def implant_documents(ctx: ClaimContext) -> Result:
    implants = [line for line in ctx.bill_lines if line.category == BillCategory.IMPLANT]
    if not implants:
        return not_applicable("No implant billed.")
    missing = [t for t in ("implant_invoice", "implant_sticker") if not ctx.has(t)]
    if not missing:
        return passed("Implant invoice and sticker attached.")
    line = implants[0]
    return flagged(Flag(
        f"Implant billed without {' and '.join(label(t) for t in missing)}",
        f"'{line.description}' is billed for {rs(line.amount)}. The {' and '.join(label(t) for t in missing)} "
        "proving which implant was used is not in the file.",
        [ctx.ev(line.doc, line.main)], amount_at_stake=sum((x.amount or 0) for x in implants),
        key=",".join(missing)))


@check("C-05")
def accident_has_mlc(ctx: ClaimContext) -> Result:
    words = ctx.config_of("C-05").get("accident_words", [])
    for name in ("diagnosis", "complaints", "history", "nature"):
        for doc, value in ctx.every(name):
            text = value.text.lower()
            hit = next((w for w in words if re.search(rf"\b{re.escape(w)}\b", text)), None)
            if hit is None:
                continue
            if ctx.has("mlc_fir"):
                return passed("Accident case with MLC / FIR attached.")
            return flagged(Flag(
                "Accident case without MLC / FIR",
                f"The {label(doc.doc_type)} records '{value.text[:120]}' ({hit}). Accident and injury claims need "
                "the medico-legal case report or FIR copy, which is not in the file.",
                [ctx.ev(doc, value)], key="mlc"))
    return not_applicable("Not an accident or injury case.") if ctx.docs else not_evaluable("No documents read.")
