"""AI judgement checks: M-01, M-02, M-04, M-06 (medical necessity), X-08 (diagnosis change), B-11 (upcoding).

The questions are asked by the AI reviewer (app/rules/ai_review.py); this module turns its answers into results.
The rules for that are the same for every check:

- no AI reviewer, or the call failed  -> "pending_ai" (the claim still gets every rule check);
- the question does not apply (no ICU stay, no severity claimed, no pre-auth) -> "not_applicable", decided by
  the claim data, never by the model;
- an explicit negative verdict that cites real facts -> an amber finding whose evidence is the cited facts;
- "unclear", or a negative verdict without citations -> "not_evaluable", nothing is raised.

Medical-necessity findings are amber and marked for medical review (engine policy).
"""

from decimal import Decimal

from app.rules.ai_review import (
    Fact,
    Judgement,
    admission_gate,
    changed_diagnosis,
    claimed_severity,
    icu_facts,
    icu_gate,
    severity_gate,
)
from app.rules.checks.common import rs
from app.rules.context import ClaimContext, Evidence, Line
from app.rules.engine import (
    Flag,
    Result,
    check,
    flagged,
    not_applicable,
    not_evaluable,
    passed,
    pending_ai,
)
from app.rules.reference import norm

NO_AI = "Needs the AI reviewer: no AI model configured (AI_PROVIDER), or AI checks are off (AI_CHECKS=false)."


def evidence(ctx: ClaimContext, facts: list[Fact]) -> list[Evidence]:
    out, seen = [], set()
    for f in facts:
        if f.id in seen or f.doc is None or f.value is None:
            continue
        seen.add(f.id)
        e = ctx.ev(f.doc, f.value, f"{f.id} {f.kind}: {f.text}")
        e.reliable = False          # rests on a model's judgement, whatever the source of the value
        out.append(e)
    return out


def _claimed(ctx: ClaimContext) -> Decimal | None:
    hit = ctx.first("claimed_amount", ["claim_form"])
    return ctx.num(hit[1]) if hit else None


def _undecided(j: Judgement) -> Result:
    return not_evaluable(f"The AI reviewer could not decide: {j.reason}")


@check("M-01")
def admission_needed(ctx: ClaimContext) -> Result:
    if ctx.ai is None:
        return pending_ai(NO_AI)
    if not ctx.ai.facts(ctx):
        return not_evaluable("Nothing clinical was read from the claim to judge the admission by.")
    reason = admission_gate(ctx)
    if reason:
        return passed(f"Not put to the AI: {reason}.")
    j = ctx.ai.admission(ctx)
    if j is None:
        return pending_ai(ctx.ai.unavailable("check:clinical"))
    if j.verdict == "needed":
        return passed(f"AI: {j.reason}")
    if j.verdict == "not_needed" and evidence(ctx, j.facts):
        return flagged(Flag(
            "Admission may not have been needed",
            f"The AI reviewer judged that the documented condition is normally treated as an outpatient: {j.reason} "
            "Compare the complaints, vitals and treatment with the standard treatment guideline before deciding.",
            evidence(ctx, j.facts), amount_at_stake=_claimed(ctx), key="admission", model=ctx.ai.model_version))
    return _undecided(j)


@check("M-02")
def tests_relevant(ctx: ClaimContext) -> Result:
    if ctx.ai is None:
        return pending_ai(NO_AI)
    answers = ctx.ai.tests(ctx)
    if answers is None:
        return pending_ai(ctx.ai.unavailable("check:clinical"))
    if not answers and not any(f.kind == "billed test" for f in ctx.ai.facts(ctx)):
        return not_applicable("No investigations billed.")
    if not answers:
        return passed("No high-value test outside the routine ones billed (cheaper tests are not put to the AI).")
    flags = []
    for j in answers:
        line = j.subject.ref if j.subject else None
        if j.verdict != "not_relevant" or not isinstance(line, Line) or j.subject is None:
            continue
        ev = evidence(ctx, [j.subject, *j.facts])
        flags.append(Flag(
            f"{line.description}: no documented reason for this test",
            f"The AI reviewer found nothing in the diagnosis, complaints or findings that calls for "
            f"{line.description}: {j.reason} Check the clinical notes for an indication.",
            ev, amount_at_stake=line.amount, key=f"test|{norm(line.description)}", model=ctx.ai.model_version))
    unclear = sum(1 for j in answers if j.verdict == "unclear")
    detail = f"{len(answers)} billed test(s) judged" + (f", {unclear} unclear" if unclear else "") + "."
    return flagged(*flags, detail=detail)


@check("M-04")
def icu_justified(ctx: ClaimContext) -> Result:
    if ctx.ai is None:
        return pending_ai(NO_AI)
    icu = icu_facts(ctx.ai.facts(ctx))
    if not icu:
        return not_applicable("No ICU stay billed or charted.")
    reason = icu_gate(ctx)
    if reason:
        return passed(f"Not put to the AI: {reason}.")
    j = ctx.ai.icu(ctx)
    if j is None:
        return pending_ai(ctx.ai.unavailable("check:clinical"))
    if j.verdict == "justified":
        return passed(f"AI: {j.reason}")
    if j.verdict == "not_justified" and j.facts:
        lines = [f.ref for f in icu if isinstance(f.ref, Line)]
        at_stake = sum((x.amount for x in lines if x.amount is not None), Decimal(0)) or None
        return flagged(Flag(
            "ICU stay not supported by the charted vitals and treatment",
            f"The AI reviewer judged that the vitals, lab values and treatment recorded for the ICU days do not "
            f"show a need for intensive care: {j.reason} Check the ICU notes before deciding.",
            evidence(ctx, [*[f for f in icu if f.kind == "billed ICU"], *j.facts]), amount_at_stake=at_stake,
            key="icu", model=ctx.ai.model_version))
    return _undecided(j)


@check("M-06")
def severity_supported(ctx: ClaimContext) -> Result:
    if ctx.ai is None:
        return pending_ai(NO_AI)
    severity = claimed_severity(ctx)
    if severity is None:
        return not_applicable("The diagnosis does not claim a severity or complication.")
    status, reason = severity_gate(ctx)
    if status == "no_marker":
        return not_applicable(f"\"{severity}\": {reason}; not put to the AI.")
    if status == "no_readings":
        return not_evaluable(f"\"{severity}\": {reason}.")
    if status == "supported":
        return passed(f"{reason}: supports \"{severity}\".")
    j = ctx.ai.severity(ctx)
    if j is None:
        return pending_ai(ctx.ai.unavailable("check:clinical"))
    if j.verdict == "supported":
        return passed(f"AI: {j.reason}")
    if j.verdict == "not_supported" and j.facts:
        diagnosis = [f for f in ctx.ai.facts(ctx) if f.kind.startswith("final diagnosis")][:1]
        return flagged(Flag(
            "Severity in the diagnosis not supported by the lab values",
            f"The diagnosis says \"{severity}\", but the AI reviewer found the documented values do not show it: "
            f"{j.reason}", evidence(ctx, [*diagnosis, *j.facts]), key="severity", model=ctx.ai.model_version))
    return _undecided(j)


@check("X-08")
def diagnosis_change_explained(ctx: ClaimContext) -> Result:
    if not ctx.docs_of("preauth_form"):
        return not_applicable("No pre-authorisation in the file.")
    change = changed_diagnosis(ctx)
    if change is None:
        return passed("Final diagnosis matches the pre-authorisation.")
    if ctx.ai is None:
        return pending_ai(NO_AI)
    j = ctx.ai.diagnosis_change(ctx)
    if j is None:
        return pending_ai(ctx.ai.unavailable("check:coding"))
    pre, final = change
    if j.verdict in ("same_condition", "explained"):
        return passed(f"AI ({j.verdict.replace('_', ' ')}): {j.reason}")
    if j.verdict == "not_explained":
        return flagged(Flag(
            "Diagnosis changed from the pre-authorisation without explanation",
            f"Pre-authorisation was for \"{pre.text}\"; the final diagnosis is \"{final.text}\". The AI reviewer "
            f"found nothing in the notes explaining the change: {j.reason} Ask the hospital why the diagnosis "
            "changed; the pre-authorisation approval was for a different condition.",
            evidence(ctx, [pre, final, *j.facts]), key="diagnosis_change", model=ctx.ai.model_version))
    return _undecided(j)


@check("B-11")
def upcoding(ctx: ClaimContext) -> Result:
    billed = [f for f in (ctx.ai.facts(ctx) if ctx.ai else []) if f.kind == "billed procedure"]
    if ctx.ai is None:
        if not ctx.billed_procedures:
            return not_applicable("No procedure billed.")
        return pending_ai(NO_AI)
    if not billed:
        return not_applicable("No procedure billed.")
    answers = ctx.ai.upcoding(ctx)
    if answers is None:
        return pending_ai(ctx.ai.unavailable("check:coding"))
    if not answers:
        recorded = ctx.procedures("ot_notes") + ctx.procedures("discharge_summary")
        if not recorded:
            return not_evaluable("No procedure recorded in the OT notes or discharge summary to compare with.")
        return passed("Billed procedure is named as in the OT notes / discharge summary.")
    flags = []
    for j in answers:
        line = j.subject.ref if j.subject else None
        if j.verdict != "not_supported" or not isinstance(line, Line) or j.subject is None:
            continue
        recorded = "; ".join(p for _, _, p in ctx.procedures("ot_notes") + ctx.procedures("discharge_summary"))
        flags.append(Flag(
            "Billed procedure more complex than the notes describe",
            f"The bill charges \"{line.description}\" ({rs(line.amount)}); the notes record \"{recorded}\". The AI "
            f"reviewer judged the notes do not support the billed procedure: {j.reason} Check the operative "
            "notes; if they describe the simpler procedure, pay at its rate.",
            evidence(ctx, [j.subject, *j.facts]), key=f"upcoding|{norm(line.description)}",
            model=ctx.ai.model_version))
    if flags:
        return flagged(*flags)
    verdicts = {j.verdict for j in answers}
    if verdicts == {"supported"}:
        return passed(f"AI: {answers[0].reason}")
    return _undecided(answers[0])
