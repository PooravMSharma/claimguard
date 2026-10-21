"""P-layer: is this admission covered by the policy, and how much of it?

The insurer's own policy record (policy_records) is preferred; the policy copy in the claim file is the
fallback. Waiting periods, the moratorium and room limits come from the policy terms table (standard IRDAI
terms until a client loads its own), never from code.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.extraction.model import DocExtract, Value
from app.rules.checks.common import day, rs, same_person
from app.rules.context import ClaimContext, Evidence
from app.rules.engine import Flag, Result, check, flagged, not_applicable, not_evaluable, passed
from app.rules.reference import norm

KNOWN_CASE = re.compile(r"(?i)\b(?:known case of|k/c/o|kco|history of|on treatment for)\s+([a-z][a-z \-]{2,60})")
MATERNITY = re.compile(r"(?i)\b(delivery|caesarean|cesarean|lscs|pregnan|labour|labor|obstetric|antenatal)\b")


@dataclass
class PolicyView:
    source: str                               # "insurer record" | "policy copy"
    start: date | None = None
    end: date | None = None
    inception: date | None = None
    sum_insured: Decimal | None = None
    used_si: Decimal | None = None
    sub_limits: dict = field(default_factory=dict)
    product: str | None = None
    member_name: str | None = None
    declared_peds: tuple[str, ...] = ()
    copy_values: dict[str, tuple[DocExtract, Value]] = field(default_factory=dict)
    sub_limits_text: str | None = None

    def evidence(self, ctx: ClaimContext, *names: str) -> list[Evidence]:
        out = [ctx.ev(*self.copy_values[n]) for n in names if n in self.copy_values]
        if not out and self.source == "insurer record":
            out.append(Evidence("insurer policy record", None, None, ", ".join(names)))
        return out


def _as_date(ctx: ClaimContext, value: Value | None) -> date | None:
    moment = ctx.when(value)
    return moment.date() if moment else None


def policy_view(ctx: ClaimContext) -> PolicyView | None:
    copy = ctx.doc("policy_copy")
    record = ctx.policy_record
    if record is None and copy is None:
        return None
    view = PolicyView("insurer record" if record else "policy copy")
    if copy is not None:
        # a value OCR'd unclearly from a scanned policy copy is left out rather than trusted
        f = {k: v for k, v in copy.fields.items() if ctx.usable(copy, v)}
        for name in ("policy_start", "policy_end", "inception", "sum_insured", "used_si", "sub_limits",
                     "product_code", "patient_name", "balance_si"):
            if name in f:
                view.copy_values[name] = (copy, f[name])
        view.sub_limits_text = copy.fields["sub_limits"].text if "sub_limits" in copy.fields else None
        view.start, view.end = _as_date(ctx, f.get("policy_start")), _as_date(ctx, f.get("policy_end"))
        view.inception = _as_date(ctx, f.get("inception"))
        view.sum_insured, view.used_si = ctx.num(f.get("sum_insured")), ctx.num(f.get("used_si"))
        sub = f.get("sub_limits")
        view.sub_limits = dict(sub.normalized) if sub is not None and isinstance(sub.normalized, dict) else {}
        product = f.get("product_code")
        view.product = product.text if product else None
        name = f.get("patient_name")
        view.member_name = name.text if name else None
    if record is not None:
        view.start = record.start or view.start
        view.end = record.end or view.end
        view.inception = record.inception or view.inception
        view.sum_insured = record.sum_insured if record.sum_insured is not None else view.sum_insured
        view.used_si = record.used_si if record.used_si is not None else view.used_si
        view.sub_limits = dict(record.sub_limits) if record.sub_limits is not None else view.sub_limits
        view.product = record.product_code or view.product
        view.member_name = record.member_name or view.member_name
        view.declared_peds = record.declared_peds
    return view


def _months(start: date, end: date) -> float:
    return (end - start).days / 30.4375


def _admission(ctx: ClaimContext) -> tuple[DocExtract, Value, date] | None:
    hit = ctx.date_of("admission")
    return (hit[0], hit[1], hit[2].date()) if hit and ctx.usable(hit[0], hit[1]) else None


def _clinical_text(ctx: ClaimContext) -> str:
    parts = [v.text for name in ("diagnosis", "preauth_diagnosis", "procedures") for _, v in ctx.every(name)]
    parts += [p for _, p in ctx.billed_procedures]
    return " ".join(parts)


def _icd_categories(ctx: ClaimContext) -> set[str]:
    return {re.sub(r"[^A-Z0-9]", "", v.text.upper())[:3] for _, v in ctx.every("icd_code")}


def _accident(ctx: ClaimContext) -> bool:
    words = ctx.config_of("C-05").get("accident_words") or ["accident", "injury", "fracture", "fall from"]
    text = " ".join(v.text for n in ("diagnosis", "complaints", "history") for _, v in ctx.every(n)).lower()
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words) or ctx.has("mlc_fir")


@check("P-01")
def policy_active(ctx: ClaimContext) -> Result:
    view = policy_view(ctx)
    adm = _admission(ctx)
    if view is None or adm is None or view.start is None or view.end is None:
        return not_evaluable("Policy period or admission date not known.")
    flags = []
    claimed = ctx.first("claimed_amount", ["claim_form"])
    at_stake = ctx.num(claimed[1]) if claimed else None
    if not view.start <= adm[2] <= view.end:
        when = "before the policy started" if adm[2] < view.start else "after the policy ended"
        flags.append(Flag(
            "Admission outside the policy period",
            f"Admitted {day(adm[2])}, {when} (policy period {day(view.start)} to {day(view.end)}, per the "
            f"{view.source}).", [ctx.ev(adm[0], adm[1])] + view.evidence(ctx, "policy_start", "policy_end"),
            amount_at_stake=at_stake, key=f"period|{view.start}|{view.end}"))
    patient = ctx.first("patient_name", ["claim_form", "discharge_summary"])
    if view.member_name and patient and ctx.usable(*patient) and not same_person(
            view.member_name, patient[1].text, lenient=ctx.scanned(patient[0]) or view.source == "policy copy"):
        flags.append(Flag(
            "Patient is not the insured member on this policy",
            f"The {view.source} names the member '{view.member_name}'; the claim is for '{patient[1].text}'.",
            [ctx.ev(*patient)] + view.evidence(ctx, "patient_name"), amount_at_stake=at_stake,
            key=f"member|{norm(view.member_name)}|{norm(patient[1].text)}"))
    return flagged(*flags, detail=f"Policy {day(view.start)} to {day(view.end)} ({view.source}).")


@check("P-02")
def waiting_periods(ctx: ClaimContext) -> Result:
    view = policy_view(ctx)
    adm = _admission(ctx)
    if view is None or adm is None or view.inception is None:
        return not_evaluable("First inception date or admission date not known.")
    product = view.product
    months = _months(view.inception, adm[2])
    claimed = ctx.first("claimed_amount", ["claim_form"])
    at_stake = ctx.num(claimed[1]) if claimed else None
    evidence = [ctx.ev(adm[0], adm[1])] + view.evidence(ctx, "inception")
    flags = []
    initial = (ctx.ref.term("initial_waiting_days", product) or {}).get("days")
    if initial and (adm[2] - view.inception).days < initial and not _accident(ctx):
        flags.append(Flag(
            "Admission within the initial waiting period",
            f"Cover started {day(view.inception)}; admission on {day(adm[2])} is within the first {initial} days, "
            "when only accidents are covered.", evidence, amount_at_stake=at_stake, key="initial"))
    specific = ctx.ref.term("specific_diseases", product) or {}
    wait = (ctx.ref.term("specific_disease_waiting_months", product) or {}).get("months")
    text, icds = _clinical_text(ctx).lower(), _icd_categories(ctx)
    condition = next((c for c in specific.get("conditions", [])
                      if icds & set(c.get("icd", [])) or any(w in text for w in c.get("words", []))), None)
    if condition and wait and months < wait:
        flags.append(Flag(
            f"Specific-disease waiting period not served: {condition['name']}",
            f"{condition['name']} has a {wait}-month waiting period; cover started {day(view.inception)}, only "
            f"{months:.0f} months before admission on {day(adm[2])}.", evidence, amount_at_stake=at_stake,
            key=f"specific|{condition['name']}"))
    ped_wait = (ctx.ref.term("ped_waiting_months", product) or {}).get("months")
    history = " ".join(v.text for _, v in ctx.every("history"))
    found = KNOWN_CASE.search(history)
    if ped_wait and found and months < ped_wait:
        disease = found.group(1).strip()
        declared = any(norm(disease).split()[0] in norm(p) for p in view.declared_peds) if norm(disease) else False
        doc, value = ctx.every("history")[0]
        flags.append(Flag(
            "Pre-existing condition within its waiting period",
            f"The notes record '{found.group(0)}'. Cover started {day(view.inception)} ({months:.0f} months before "
            f"admission); pre-existing conditions wait {ped_wait} months"
            + (" (declared at proposal)." if declared else " and this one was not declared.")
            + " Check whether this admission is related to it.",
            [ctx.ev(doc, value)] + view.evidence(ctx, "inception"), severity="amber", key=f"ped|{norm(disease)}"))
    return flagged(*flags, detail=f"{months:.0f} months of continuous cover at admission.")


@check("P-03")
def exclusions_and_sub_limits(ctx: ClaimContext) -> Result:
    view = policy_view(ctx)
    if view is None:
        return not_evaluable("No policy record or policy copy.")
    claimed_hit = ctx.first("claimed_amount", ["claim_form"])
    claimed = ctx.num(claimed_hit[1]) if claimed_hit else None
    text = _clinical_text(ctx)
    flags = []
    icds = _icd_categories(ctx)
    maternity = MATERNITY.search(text) or any(c.startswith("O") for c in icds)
    covered = any("maternity" in k.lower() for k in view.sub_limits) or \
        "matern" in (view.sub_limits_text or "").lower()
    copy = ctx.doc("policy_copy")
    unclear = copy is not None and ctx.scanned(copy) and "sub_limits" not in view.copy_values
    if maternity and view.source == "policy copy" and unclear and not covered:
        return not_evaluable("Maternity case; the policy copy's cover details are unclear on the scan.")
    if maternity and not covered:
        exclusions = (ctx.ref.term("standard_exclusions", view.product) or {}).get("codes", {})
        code = next((k for k, v in exclusions.items() if "maternity" in v.lower()), "Excl18")
        diag = ctx.first("diagnosis")
        flags.append(Flag(
            "Maternity treatment under a policy without maternity cover",
            f"The claim is for maternity treatment ({diag[1].text if diag else 'delivery'}); the policy has no "
            f"maternity cover, so standard exclusion {code} applies.",
            ([ctx.ev(*diag)] if diag else []) + view.evidence(ctx, "sub_limits", "product_code"),
            amount_at_stake=claimed, key=f"exclusion|{code}"))
    for key, limit in view.sub_limits.items():
        words = [w for w in key.lower().replace("_", " ").split() if w not in ("per", "eye", "limit", "sub")]
        if not words or "maternity" in words or not all(w in text.lower() for w in words[:1]):
            continue
        try:
            cap = Decimal(str(limit))
        except Exception:
            continue
        if claimed is not None and claimed > cap:
            flags.append(Flag(
                f"Above the {key.replace('_', ' ')} sub-limit",
                f"The policy limits {key.replace('_', ' ')} to {rs(cap)}; {rs(claimed)} is claimed, "
                f"{rs(claimed - cap)} over the limit.",
                ([ctx.ev(*claimed_hit)] if claimed_hit else []) + view.evidence(ctx, "sub_limits"),
                amount_at_stake=claimed - cap, suggested_deduction=claimed - cap, key=f"sublimit|{key}"))
    if not maternity and not view.sub_limits:
        return passed("No exclusion or sub-limit applies.")
    return flagged(*flags, detail="Exclusions and sub-limits checked.")


@check("P-04")
def early_claim(ctx: ClaimContext) -> Result:
    view = policy_view(ctx)
    adm = _admission(ctx)
    if view is None or adm is None or view.inception is None:
        return not_evaluable("First inception date or admission date not known.")
    limit = ctx.config_of("P-04").get("days_after_inception", 90)
    days = (adm[2] - view.inception).days
    if days > limit:
        return passed(f"Claim {days} days after cover started.")
    return flagged(Flag(
        f"Claim {days} days after the policy started",
        f"Cover started {day(view.inception)} and admission was {day(adm[2])}, {days} days later. Early claims are "
        "a common pattern in proposal fraud (a condition known at purchase): check the history carefully.",
        [ctx.ev(adm[0], adm[1])] + view.evidence(ctx, "inception"), key=f"early|{view.inception}"))


@check("P-05")
def moratorium(ctx: ClaimContext) -> Result:
    view = policy_view(ctx)
    adm = _admission(ctx)
    if view is None or adm is None or view.inception is None:
        return not_evaluable("First inception date or admission date not known.")
    term = (ctx.ref.term("moratorium_months", view.product) or {}).get("months")
    if not term:
        return not_evaluable("No moratorium term loaded.")
    months = _months(view.inception, adm[2])
    if months < term:
        return not_applicable(f"{months:.0f} months of cover; the moratorium starts at {term}.")
    history = [(d, v) for d, v in ctx.every("history") if KNOWN_CASE.search(v.text)]
    if not history:
        return passed(f"{months:.0f} months of continuous cover; no pre-existing condition noted.")
    doc, value = history[0]
    return flagged(Flag(
        "Moratorium applies: non-disclosure cannot be a ground",
        f"The notes record '{KNOWN_CASE.search(value.text).group(0)}'. Cover has been continuous for "  # type: ignore[union-attr]
        f"{months:.0f} months (since {day(view.inception)}), past the {term}-month moratorium, so the claim cannot "
        "be refused for non-disclosure of this condition (fraud excepted).",
        [ctx.ev(doc, value)] + view.evidence(ctx, "inception"), key=f"moratorium|{view.inception}"))


@check("P-06")
def sum_insured_left(ctx: ClaimContext) -> Result:
    view = policy_view(ctx)
    claimed = ctx.first("claimed_amount", ["claim_form"])
    if view is None or view.sum_insured is None or claimed is None:
        return not_evaluable("Sum insured or claimed amount not known.")
    amount = ctx.num(claimed[1])
    if amount is None:
        return not_evaluable("Claimed amount not a number.")
    left = view.sum_insured - (view.used_si or 0)
    tol = Decimal(str(ctx.config_of("P-06").get("tolerance_rs", 1.0)))
    if not ctx.usable(*claimed):
        return not_evaluable("Claimed amount unclear on the scan.")
    if view.source == "policy copy" and "balance_si" in view.copy_values:
        balance = ctx.num(view.copy_values["balance_si"][1])
        if balance is not None and abs(balance - left) > tol:
            return not_evaluable("Sum insured, amount used and balance on the policy copy do not agree "
                                 f"({rs(view.sum_insured)} - {rs(view.used_si or 0)} vs {rs(balance)}).")
    if amount <= left + tol:
        return passed(f"{rs(amount)} claimed; {rs(left)} of the sum insured left.")
    return flagged(Flag(
        "Claim is more than the sum insured left",
        f"Sum insured {rs(view.sum_insured)}, already used {rs(view.used_si or 0)}: {rs(left)} left. "
        f"{rs(amount)} is claimed, {rs(amount - left)} more than is available.",
        [ctx.ev(*claimed)] + view.evidence(ctx, "sum_insured", "used_si"),
        amount_at_stake=amount - left, suggested_deduction=amount - left, key=f"{left}|{amount}"))
