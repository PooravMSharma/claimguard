"""M-layer (rule part): length of stay, day care, repeated imaging.

Always amber at most and marked for medical review until a clinician has reviewed the rule. Judgements
about whether care was needed (M-01, M-02, M-04, M-06) are in checks/judgement.py (AI reviewer).
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from app.models.enums import BillCategory
from app.rules.checks.common import moment, rs, similar
from app.rules.checks.judgement import evidence
from app.rules.context import ClaimContext
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


@check("M-03")
def repeated_tests(ctx: ClaimContext) -> Result:
    cfg = ctx.config_of("M-03")
    kinds = {f"{k}_report" for k in cfg.get("kinds", ["imaging"])}
    window = timedelta(hours=cfg.get("repeat_window_hours", 24))
    groups = defaultdict(list)
    for d in ctx.docs:
        if d.doc_type in kinds and "test" in d.fields:
            when = ctx.when(d.fields.get("report_date"))
            if when and ctx.usable(d, d.fields.get("report_date")):
                groups[norm(d.fields["test"].text)].append((when, d))
    if not groups:
        return not_applicable("No " + " / ".join(sorted(k.split("_")[0] for k in kinds)) + " reports.")
    flags, repeats = [], []
    for reports in groups.values():
        reports.sort(key=lambda x: x[0])
        for (t1, d1), (t2, d2) in zip(reports, reports[1:]):
            if t2 - t1 > window:
                continue
            test = d2.fields["test"].text
            line = next((x for x in ctx.bill_lines if x.category == BillCategory.INVESTIGATION
                         and similar(x.description, test) >= 0.8), None)
            hours = (t2 - t1).total_seconds() / 3600
            repeats.append(f"{test}: done at {moment(t1)} and again at {moment(t2)}")
            flags.append(Flag(
                f"{test} repeated within {hours:.0f} hours",
                f"{test} was done at {moment(t1)} and again at {moment(t2)}. Check the notes for a reason for "
                "repeating it (a change in condition); without one the repeat may not be payable.",
                [ctx.ev(d1, d1.fields["report_date"]), ctx.ev(d2, d2.fields["report_date"])],
                amount_at_stake=line.amount if line else None, key=f"{norm(test)}|{t2.isoformat()}"))
    detail = f"{sum(len(v) for v in groups.values())} report(s) checked for repeats."
    if flags and ctx.ai is not None:
        # AI part: a repeat with a documented reason (a change in condition) is shown as information only.
        answers = ctx.ai.repeat_reasons(ctx, repeats)
        if answers is None:
            detail += " " + ctx.ai.unavailable("check:repeats")
        else:
            for flag, j in zip(flags, answers):
                if j.verdict == "reason_documented" and j.facts:
                    flag.severity = "info"
                    flag.model = ctx.ai.model_version
                    flag.explanation += f" The AI reviewer found a documented reason: {j.reason}"
                    flag.evidence += evidence(ctx, j.facts)
                elif j.verdict == "no_reason":
                    flag.model = ctx.ai.model_version
                    flag.explanation += " The AI reviewer found no documented reason in the notes."
    return flagged(*flags, detail=detail)


def _stay_is_clear(ctx: ClaimContext) -> bool:
    """Dates read clearly; on a scan, also confirmed by the room days billed (a misread date can add months)."""
    adm, dis = ctx.date_of("admission"), ctx.date_of("discharge")
    if adm is None or dis is None or not (ctx.usable(adm[0], adm[1]) and ctx.usable(dis[0], dis[1])):
        return False
    if not (ctx.scanned(adm[0]) or ctx.scanned(dis[0])):
        return True
    nights = ctx.stay_nights or 0
    rooms = [x for x in ctx.bill_lines if x.category in (BillCategory.ROOM, BillCategory.ICU) and x.qty is not None]
    billed = sum((x.qty or 0) for x in rooms)
    return abs(billed - nights) <= 1 or (not rooms and nights <= 1)


def _room_rate(ctx: ClaimContext) -> Decimal | None:
    room = next((x for x in ctx.bill_lines if x.category in (BillCategory.ROOM, BillCategory.ICU) and x.rate), None)
    return room.rate if room else None


@check("M-05")
def length_of_stay(ctx: ClaimContext) -> Result:
    pkg = ctx.package
    if pkg is None:
        return not_evaluable("No package matched for this claim.")
    if not pkg.los_days or pkg.day_care:
        return not_applicable(f"No length-of-stay norm for {pkg.code}.")
    nights = ctx.stay_nights
    if nights is None or not _stay_is_clear(ctx):
        return not_evaluable("Admission or discharge date not read clearly.")
    allowed = pkg.los_days + ctx.config_of("M-05").get("extra_days_allowed", 2)
    if nights <= allowed:
        return passed(f"{nights} day(s) against a norm of {pkg.los_days} for {pkg.code}.")
    adm, dis = ctx.date_of("admission"), ctx.date_of("discharge")
    rate = _room_rate(ctx)
    extra = nights - pkg.los_days
    evidence = [ctx.ev(adm[0], adm[1]), ctx.ev(dis[0], dis[1])] if adm and dis else []
    return flagged(Flag(
        f"Stay of {nights} days is long for {pkg.name}",
        f"{nights} days in hospital against a usual {pkg.los_days} for {pkg.name} ({pkg.code}). Look for a "
        "complication or reason in the discharge summary; extra days without one may not be payable.",
        evidence, amount_at_stake=rate * extra if rate else None, key=f"{pkg.code}|{nights}"))


@check("M-07")
def day_care_overnight(ctx: ClaimContext) -> Result:
    pkg = ctx.package
    if pkg is None:
        return not_evaluable("No package matched for this claim.")
    if not pkg.day_care:
        return not_applicable(f"{pkg.code} is not a day-care package.")
    nights = ctx.stay_nights
    if nights is None or not _stay_is_clear(ctx):
        return not_evaluable("Admission or discharge date not read clearly.")
    if nights < 1:
        return passed("Discharged the same day.")
    adm, dis = ctx.date_of("admission"), ctx.date_of("discharge")
    rooms = [x for x in ctx.bill_lines if x.category in (BillCategory.ROOM, BillCategory.ICU)]
    at_stake = sum((x.amount or Decimal(0)) for x in rooms) or None
    evidence = [ctx.ev(adm[0], adm[1]), ctx.ev(dis[0], dis[1])] if adm and dis else []
    evidence += [ctx.ev(x.doc, x.main) for x in rooms[:1]]
    return flagged(Flag(
        f"Day-care procedure with {nights} night(s) in hospital",
        f"{pkg.name} ({pkg.code}) is a day-care procedure, but the patient stayed {nights} night(s)"
        + (f" and room rent of {rs(at_stake)} is billed" if at_stake else "") + ".",
        evidence, amount_at_stake=at_stake, key=f"{pkg.code}|{nights}"))
