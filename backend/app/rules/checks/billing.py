"""B-layer (rule part): arithmetic, claimed vs billed, benchmarks, non-payables, room, multiple surgery, MRP."""

import re
from dataclasses import dataclass
from decimal import Decimal

from app.models.enums import BillCategory
from app.rules.checks.common import num_text, rs
from app.rules.context import ClaimContext, Line, ward_of
from app.rules.engine import Flag, Result, check, flagged, not_applicable, not_evaluable, passed
from app.rules.reference import TariffRef, norm

ZERO = Decimal(0)
WARD_RANK = {"general": 0, "semi_private": 1, "private": 2}


def _d(value: float | int | str) -> Decimal:
    return Decimal(str(value))


def line_ok(ctx: ClaimContext, line: Line, tol: Decimal = Decimal("1")) -> bool:
    """Numbers of a bill line safe to rely on: exact text, or on a scan clearly read and adding up."""
    if not ctx.scanned(line.doc):
        return True
    values = [line.values.get(k) for k in ("qty", "rate", "amount")]
    if any(v is None or not ctx.usable(line.doc, v) for v in values) or not ctx.usable(line.doc, line.main):
        return False
    return line.qty is not None and line.rate is not None and line.amount is not None and \
        abs(line.qty * line.rate - line.amount) <= tol


def _total_agrees(ctx: ClaimContext, doc, total_name: str, amounts: list[Decimal | None], tol: Decimal) -> bool:
    """On a scan, the line amounts are only trusted if they add up to the printed total."""
    total = doc.fields.get(total_name)
    if total is None or not ctx.usable(doc, total) or any(a is None for a in amounts):
        return False
    stated = ctx.num(total)
    return stated is not None and abs(sum(a for a in amounts if a is not None) - stated) <= tol


@check("B-01")
def bill_arithmetic(ctx: ClaimContext) -> Result:
    if not ctx.bill_lines and not ctx.pharmacy_lines:
        return not_evaluable("No bill lines read.")
    tol = _d(ctx.config_of("B-01").get("tolerance_rs", 1.0))
    flags = []
    bill_docs = {line.doc.key: line.doc for line in ctx.bill_lines}
    corroborated = {key: _total_agrees(ctx, d, "bill_total", [x.amount for x in ctx.bill_lines if x.doc is d], tol)
                    for key, d in bill_docs.items()}
    for line in ctx.bill_lines:
        if line.qty is None or line.rate is None or line.amount is None:
            continue
        if ctx.scanned(line.doc) and not (corroborated.get(line.doc.key) and all(
                ctx.usable(line.doc, line.values.get(k)) for k in ("qty", "rate", "amount"))):
            continue
        right = (line.qty * line.rate).quantize(Decimal("0.01"))
        if abs(right - line.amount) > tol:
            flags.append(Flag(
                f"Line total wrong: {line.description}",
                f"{num_text(line.qty)} x {rs(line.rate)} = {rs(right)}, but the bill shows {rs(line.amount)}.",
                [ctx.ev(line.doc, line.values.get("amount") or line.main, f"{line.description}: {rs(line.amount)}")],
                amount_at_stake=line.amount - right, key=f"line|{line.description}|{line.amount}"))
    for doc, row in ctx.pharmacy_lines:
        qty, rate, amount = ctx.num(row.get("qty")), ctx.num(row.get("rate")), ctx.num(row.get("amount"))
        if qty is None or rate is None or amount is None:
            continue
        if ctx.scanned(doc) and not (_total_agrees(ctx, doc, "pharmacy_total", [
                ctx.num(r.get("amount")) for d, r in ctx.pharmacy_lines if d is doc], tol) and all(
                ctx.usable(doc, row.get(k)) for k in ("qty", "rate", "amount"))):
            continue
        right = (qty * rate).quantize(Decimal("0.01"))
        if abs(right - amount) > tol:
            flags.append(Flag(
                f"Pharmacy line total wrong: {row['drug'].text}",
                f"{num_text(qty)} x {rs(rate)} = {rs(right)}, but the pharmacy bill shows {rs(amount)}.",
                [ctx.ev(doc, row.get("amount") or row["drug"])], amount_at_stake=amount - right,
                key=f"pharmacy|{row['drug'].text}|{amount}"))
    for doc in ctx.docs_of("final_bill"):
        total = doc.fields.get("bill_total")
        lines = [x for x in ctx.bill_lines if x.doc is doc and x.amount is not None]
        if total is None or not lines or len(lines) != len(doc.tables.get("lines", [])):
            continue
        if ctx.scanned(doc) and not (ctx.usable(doc, total) and all(line_ok(ctx, x, tol) for x in lines)):
            continue
        summed = sum((x.amount or ZERO) for x in lines)
        stated = ctx.num(total)
        if stated is not None and abs(summed - stated) > tol:
            flags.append(Flag(
                "Bill grand total does not add up",
                f"The lines add up to {rs(summed)}, but the grand total printed is {rs(stated)}.",
                [ctx.ev(doc, total)], amount_at_stake=stated - summed, key=f"total|{stated}|{summed}"))
    for doc in ctx.docs_of("pharmacy_bill"):
        total = doc.fields.get("pharmacy_total")
        rows = [r for d, r in ctx.pharmacy_lines if d is doc]
        amounts = [ctx.num(r.get("amount")) for r in rows]
        if total is None or not rows or any(a is None for a in amounts):
            continue
        if ctx.scanned(doc) and not (ctx.usable(doc, total) and all(
                ctx.usable(doc, r.get(k)) for r in rows for k in ("qty", "rate", "amount"))):
            continue
        summed = sum(a for a in amounts if a is not None)
        stated = ctx.num(total)
        if stated is not None and abs(summed - stated) > tol:
            flags.append(Flag(
                "Pharmacy bill total does not add up",
                f"The pharmacy lines add up to {rs(summed)}, but the total printed is {rs(stated)}.",
                [ctx.ev(doc, total)], amount_at_stake=stated - summed, key=f"pharmacy_total|{stated}"))
    return flagged(*flags, detail=f"{len(ctx.bill_lines)} bill and {len(ctx.pharmacy_lines)} pharmacy line(s).")


@check("B-02")
def claimed_equals_billed(ctx: ClaimContext) -> Result:
    claimed = ctx.first("claimed_amount", ["claim_form"])
    total = ctx.first("bill_total", ["final_bill"])
    if claimed is None or total is None:
        return not_evaluable("Claimed amount or bill total not read.")
    c, t = ctx.num(claimed[1]), ctx.num(total[1])
    if c is None or t is None:
        return not_evaluable("Claimed amount or bill total not a number.")
    if not ctx.usable(*claimed):
        return not_evaluable("Claimed amount unclear on the scan.")
    if ctx.scanned(total[0]):
        lines = [x for x in ctx.bill_lines if x.doc is total[0]]
        summed = sum((x.amount or ZERO) for x in lines)
        if lines and all(line_ok(ctx, x) for x in lines):
            t = summed                    # every line read cleanly: their sum is the bill total
        elif not (ctx.usable(*total) and lines and abs(summed - t) <= 1):
            return not_evaluable("Bill total unclear on the scan.")
    if c <= t + _d(ctx.config_of("B-02").get("tolerance_rs", 1.0)):
        return passed(f"Claimed {rs(c)} against a bill of {rs(t)}.")
    return flagged(Flag(
        "Claimed amount is more than the bill",
        f"The claim form claims {rs(c)}, {rs(c - t)} more than the final bill total of {rs(t)}.",
        [ctx.ev(*claimed), ctx.ev(*total)], amount_at_stake=c - t, suggested_deduction=c - t, key=f"{c}|{t}"))


# ---------------------------------------------------------------- B-03 benchmark rates

def _candidates(ctx: ClaimContext, line: Line) -> list[str]:
    desc = re.sub(r"\(.*?\)", " ", line.description)
    names = [desc]
    procedures = [p for _, p in ctx.billed_procedures] + [p for _, _, p in ctx.procedures("ot_notes")]
    if ctx.package:
        procedures.append(ctx.package.name)
    if not re.search(r"[-–:]", desc):
        names += [f"{desc} - {p}" for p in procedures]
    return [norm(n) for n in names]


@dataclass
class Bench:
    ref: TariffRef
    rate: Decimal                       # the benchmark for this claim, after any CGHS adjustment
    how: str                            # how the bill line was matched to the item
    adjustments: list[str]


CANCER_ICD = re.compile(r"^(C\d|D0\d|D[1-4]\d)", re.I)


def _cancer(ctx: ClaimContext) -> bool:
    hit = ctx.first("icd_code")
    return bool(hit and CANCER_ICD.match(re.sub(r"[^A-Z0-9]", "", hit[1].text.upper())))


def _hospital_grade(ctx: ClaimContext) -> str | None:
    hospital = ctx.ref.hospitals.get(ctx.rohini_id) if ctx.ref.hospitals and ctx.rohini_id else None
    if hospital is None or hospital.nabh is None:
        return None
    return "nabh" if hospital.nabh else "non_nabh"


def _cghs_rate(row: TariffRef, tier: str, ward: str | None, grade: str | None) -> tuple[Decimal, list[str]]:
    """A CGHS rate for this claim's city tier, ward and hospital grade (CGHS OM 03.10.2025 percentages).

    Unknown hospital grade: the highest rate that could apply (super speciality for procedures, NABH for tests),
    so an unknown never makes a fair bill look overcharged.
    """
    from app.services.reference.tariffs import cghs_rules

    rules = cghs_rules()
    rate, notes = Decimal(row.rate), []
    if row.city_tier in rules["city_tier_factor"] and tier in rules["city_tier_factor"] and row.city_tier != tier:
        f = _d(rules["city_tier_factor"][tier]) / _d(rules["city_tier_factor"][row.city_tier])
        rate *= f
        notes.append(f"{tier.replace('_', ' ')} city ×{f.normalize()}")
    if not row.ward_uniform and row.ward_type in rules["ward_factor"]:
        # unknown ward: the private-ward rate, the highest that could apply
        target: str = ward if ward and ward in rules["ward_factor"] else "private"
        if target != row.ward_type:
            f = _d(rules["ward_factor"][target]) / _d(rules["ward_factor"][row.ward_type])
            rate *= f
            notes.append(f"{target.replace('_', ' ')} ward ×{f.quantize(Decimal('0.001')).normalize()}"
                         + ("" if ward in rules["ward_factor"] else " (ward not known: highest rate used)"))
    wanted = grade or ("super_speciality" if row.kind in ("procedure", "cancer_procedure") else "nabh")
    if row.nabh is not None and wanted != row.grade:    # only lists that state the grade they are for
        f = _d(rules["grade_factor"][wanted]) / _d(rules["grade_factor"][row.grade])
        rate *= f
        notes.append(f"{wanted.replace('_', ' ')} hospital ×{f.quantize(Decimal('0.001')).normalize()}"
                     + ("" if grade else " (hospital grade not known: highest rate used)"))
    return rate.quantize(Decimal("0.01")), notes


def _pick(ctx: ClaimContext, rows: list[TariffRef], on, how: str) -> Bench | None:
    """Of the rows for one item, the one for this claim's tier and ward, or a CGHS row adjusted to them."""
    tier = ctx.city_tier or "tier_1"     # unknown tier: use the highest rates, so nothing is flagged unfairly
    ward = ctx.ward
    rows = [t for t in rows if t.in_force(on)]
    exact = [t for t in rows if t.city_tier in (tier, "any") and (t.ward_type == "any" or not ward
                                                                  or t.ward_type == ward or t.ward_uniform)]
    if exact:
        best = next((t for t in exact if t.city_tier == tier), exact[0])
        if best.source != "cghs":
            return Bench(best, Decimal(best.rate), how, [])
        rate, notes = _cghs_rate(best, tier, ward, _hospital_grade(ctx))
        return Bench(best, rate, how, notes)
    cghs = [t for t in rows if t.source == "cghs"]
    if not cghs:
        return None
    rate, notes = _cghs_rate(cghs[0], tier, ward, _hospital_grade(ctx))
    return Bench(cghs[0], rate, how, notes)


def benchmark(ctx: ClaimContext, line: Line) -> Bench | None:
    """The benchmark rate for a bill line: a confirmed mapping, then the same wording, then close wording."""
    from app.rules.tariff_match import core, line_kinds

    on = line.day or (ctx.admission.date() if ctx.admission else None)
    tariffs = ctx.ref.tariffs
    code = ctx.ref.tariff_mappings.get(core(line.description))
    if code:
        rows = [t for t in tariffs if t.code == code]
        if rows:
            return _pick(ctx, rows, on, "confirmed mapping")
    names = set(_candidates(ctx, line))
    same = [t for t in tariffs if norm(t.name) in names]
    if same:
        picked = _pick(ctx, same, on, "same wording")
        if picked:
            return picked
    kinds = line_kinds(line.category.value, cancer=_cancer(ctx))
    if kinds is None:
        return None
    text = line.description + (" inpatient" if line.category == BillCategory.CONSULTATION else "")
    min_score = float(ctx.config_of("B-03").get("min_match_score", 0.6))
    # an agreed tariff (the client's own, PM-JAY) before CGHS, which is only a government reference rate
    agreed = {t.source for t in tariffs} - {"cghs"}
    for groups in ([agreed, {"cghs"}] if agreed else [{"cghs"}]):
        match = ctx.ref.tariff_matcher.best(text, kinds, min_score, groups=groups)
        if match is None:
            continue
        item = ctx.ref.tariff_at(match.key)
        picked = _pick(ctx, [t for t in tariffs if t.code == item.code and t.source == item.source], on, match.how)
        if picked:
            return picked
    return None


@check("B-03")
def rates_vs_benchmark(ctx: ClaimContext) -> Result:
    if not ctx.ref.tariffs:
        return not_evaluable("No tariff or benchmark rates loaded.")
    cfg = ctx.config_of("B-03")
    skip = set(cfg.get("skip_categories", []))
    over_agreed = _d(cfg.get("over_benchmark_pct", 25)) / 100
    over_cghs = _d(cfg.get("over_cghs_pct", 150)) / 100
    flags, compared = [], 0
    for line in ctx.bill_lines:
        if line.category.value in skip or line.rate is None or not line_ok(ctx, line):
            continue
        bench = benchmark(ctx, line)
        if bench is None or not bench.rate:
            continue
        compared += 1
        ref = bench.ref
        cghs = ref.source == "cghs"
        if line.rate <= bench.rate * (1 + (over_cghs if cghs else over_agreed)):
            continue
        excess = ((line.rate - bench.rate) * (line.qty or 1)).quantize(Decimal("0.01"))
        pct = (line.rate / bench.rate - 1) * 100
        source = {"cghs": "CGHS", "pmjay": "PM-JAY"}.get(ref.source, "tariff")
        adjusted = f", adjusted for {', '.join(bench.adjustments)}" if bench.adjustments else ""
        flags.append(Flag(
            f"Rate above benchmark: {line.description}",
            f"Billed at {rs(line.rate)}; the {source} benchmark {ref.code} '{ref.name[:80]}' (matched by "
            f"{bench.how}{adjusted}) is {rs(bench.rate)}: {pct:.0f}% higher. "
            + ("CGHS is a government reference rate and private hospitals usually charge more: check the rate "
               "agreed with this hospital before deducting anything." if cghs else
               "Check the item is the same before deducting."),
            [ctx.ev(line.doc, line.values.get("rate") or line.main, f"{line.description}: {rs(line.rate)}")],
            amount_at_stake=excess, suggested_deduction=None if cghs else excess,
            key=f"{line.description}|{line.rate}"))
    if not compared:
        return not_evaluable("No billed item matched a benchmark rate.")
    return flagged(*flags, detail=f"{compared} line(s) compared with benchmark rates.")


# ---------------------------------------------------------------- B-04 unbundling

@check("B-04")
def unbundling(ctx: ClaimContext) -> Result:
    package_lines = [x for x in ctx.bill_lines if x.category == BillCategory.PACKAGE]
    if not package_lines:
        return not_applicable("No package rate billed.")
    pkg = ctx.package
    if pkg is None or not pkg.inclusions:
        return not_evaluable("Package not identified, so its inclusions are unknown.")
    included = {i.lower() for i in pkg.inclusions}
    extra = [x for x in ctx.bill_lines if x.category.value in included and ctx.usable(x.doc, x.main)]
    if not extra:
        return passed(f"Nothing included in {pkg.code} is billed separately.")
    total = sum((x.amount or ZERO) for x in extra)
    return flagged(Flag(
        f"Items included in the package billed again ({len(extra)})",
        f"The package rate for {pkg.name} ({pkg.code}) is billed, and it includes "
        f"{', '.join(sorted(included))}. Billed separately as well: "
        + "; ".join(f"{x.description} {rs(x.amount)}" for x in extra) + ".",
        [ctx.ev(package_lines[0].doc, package_lines[0].main)] + [ctx.ev(x.doc, x.main) for x in extra],
        amount_at_stake=total, suggested_deduction=total, key="|".join(sorted(norm(x.description) for x in extra))))


# ---------------------------------------------------------------- B-05 / B-06 IRDAI non-payables

def _non_payables(ctx: ClaimContext, lists: set[int], severity_note: str) -> Result:
    matcher = ctx.ref.non_payables
    if matcher is None:
        return not_evaluable("IRDAI non-payable lists not loaded.")
    flags = []
    for line in ctx.bill_lines:
        if line.category in (BillCategory.PHARMACY, BillCategory.PACKAGE, BillCategory.IMPLANT):
            continue
        if not ctx.usable(line.doc, line.main):
            continue
        hit = matcher.match(line.description)
        if hit is None or hit.list_number not in lists:
            continue
        flags.append(Flag(
            f"IRDAI List {['', 'I', 'II', 'III', 'IV'][hit.list_number]} item: {line.description}",
            f"'{line.description}' ({rs(line.amount)}) matches '{hit.item_name.title()}' on IRDAI List "
            f"{['', 'I', 'II', 'III', 'IV'][hit.list_number]}. {severity_note}",
            [ctx.ev(line.doc, line.main)], amount_at_stake=line.amount, suggested_deduction=line.amount,
            confidence=hit.score, key=f"{norm(line.description)}|{hit.item_id}"))
    return flagged(*flags, detail=f"{len(ctx.bill_lines)} line(s) matched against the IRDAI lists.")


@check("B-05")
def list_one(ctx: ClaimContext) -> Result:
    return _non_payables(ctx, {1}, "List I items are not payable.")


@check("B-06")
def lists_two_to_four(ctx: ClaimContext) -> Result:
    return _non_payables(ctx, {2, 3, 4}, "These are part of room, procedure or treatment charges and are not "
                                         "payable as separate items.")


# ---------------------------------------------------------------- B-07 consumables per day

@check("B-07")
def consumables_per_day(ctx: ClaimContext) -> Result:
    lines = [x for x in ctx.bill_lines if x.category == BillCategory.CONSUMABLES and x.qty]
    if not lines:
        return not_applicable("No consumables billed.")
    days = ctx.stay_days
    if days is None:
        return not_evaluable("Length of stay not known.")
    norms = {k.lower(): v for k, v in ctx.config_of("B-07").get("per_day", {}).items()}
    flags, compared = [], 0
    for line in lines:
        item = next((k for k in norms if k in line.description.lower()), None)
        if item is None or line.qty is None or not line_ok(ctx, line):
            continue
        compared += 1
        allowed = _d(norms[item]) * days
        if line.qty <= allowed:
            continue
        extra = line.qty - allowed
        flags.append(Flag(
            f"Too many {item}s billed: {num_text(line.qty)} in {days} day(s)",
            f"'{line.description}': {num_text(line.qty)} billed over {days} day(s), {line.qty / days:.0f} a day "
            f"against a usual maximum of {norms[item]} a day.",
            [ctx.ev(line.doc, line.values.get("qty") or line.main, f"{line.description}: {num_text(line.qty)}")],
            amount_at_stake=(extra * line.rate).quantize(Decimal("0.01")) if line.rate else None,
            key=f"{norm(line.description)}|{line.qty}"))
    if not compared:
        return not_evaluable("No consumable with a per-day norm.")
    return flagged(*flags, detail=f"{compared} consumable line(s) compared with per-day norms.")


# ---------------------------------------------------------------- B-08 room category

@check("B-08")
def room_eligibility(ctx: ClaimContext) -> Result:
    rooms = [x for x in ctx.bill_lines if x.category == BillCategory.ROOM]
    if not rooms:
        return not_applicable("No room rent billed.")
    record = ctx.policy_record
    eligible = (record.room_eligibility if record else None)
    copy = ctx.first("room_eligibility", ["policy_copy"])
    if eligible is None and copy is not None:
        eligible = ward_of(str((copy[1].normalized or {}).get("text", copy[1].text))
                           if isinstance(copy[1].normalized, dict) else copy[1].text)
    cap_pct = record.room_rent_cap_pct if record else None
    si = record.sum_insured if record else None
    if eligible is None and not (cap_pct and si):
        return not_evaluable("Room eligibility not known (no policy record or policy copy wording).")
    room = rooms[0]
    if not line_ok(ctx, room):
        return not_evaluable("Room rent line unclear on the scan.")
    billed_ward = ward_of(room.description) or ctx.ward
    reasons, eligible_rate = [], None
    if eligible and billed_ward and WARD_RANK.get(billed_ward, 0) > WARD_RANK.get(eligible, 0):
        reasons.append(f"a {billed_ward.replace('_', '-')} room is billed; the policy allows "
                       f"{eligible.replace('_', '-')}")
        tier = ctx.city_tier or "tier_1"
        eligible_rate = next((t.rate for t in ctx.ref.tariffs if t.ward_type == eligible and t.category == "Room"
                              and t.city_tier in (tier, "any") and "room" in t.name.lower()), None)
    if cap_pct and si and room.rate and room.rate > si * cap_pct / 100:
        cap = (si * cap_pct / 100).quantize(Decimal("0.01"))
        reasons.append(f"room rent {rs(room.rate)} a day is above the limit of {cap_pct.normalize():f}% of the sum "
                       f"insured ({rs(cap)})")
        eligible_rate = min(eligible_rate, cap) if eligible_rate else cap
    if not reasons:
        return passed(f"{(billed_ward or 'room').replace('_', '-').capitalize()} room within eligibility.")
    term = ctx.ref.term("proportionate_deduction", record.product_code if record else None) or {}
    deduction = None
    if eligible_rate and room.rate and term.get("applies", True):
        ratio = eligible_rate / room.rate
        exempt = {"pharmacy", "consumables", "implant", "investigation", "medical_devices", "diagnostics"}
        exempt |= {c.rstrip("s") for c in term.get("exempt_categories", [])}
        if term.get("icu_exempt", True):
            exempt.add("icu")
        base = sum((x.amount or ZERO) for x in ctx.bill_lines
                   if x.category.value not in exempt and x.category.value.rstrip("s") not in exempt)
        deduction = (base * (1 - ratio)).quantize(Decimal("0.01"))
    return flagged(Flag(
        "Room above the policy's eligibility",
        ("; ".join(reasons) + ".")[:1].upper() + ("; ".join(reasons) + ".")[1:]
        + (f" Proportionate deduction on associated charges: {rs(deduction)} (pharmacy, consumables, implants and "
           "diagnostics excluded)." if deduction else ""),
        [ctx.ev(room.doc, room.main)] + ([ctx.ev(*copy)] if copy else []),
        amount_at_stake=deduction, suggested_deduction=deduction, key=f"{billed_ward}|{room.rate}"))


# ---------------------------------------------------------------- B-09 multiple surgeries

@check("B-09")
def multiple_surgeries(ctx: ClaimContext) -> Result:
    fees = [(line, p) for line, p in ctx.billed_procedures if line.amount and line_ok(ctx, line)]
    distinct = {norm(p) for _, p in fees}
    if len(distinct) < 2:
        return not_applicable("One procedure billed.")
    cfg = ctx.config_of("B-09")
    share = _d(cfg.get("full_rate_share", 0.9))
    fees.sort(key=lambda x: x[0].amount or ZERO, reverse=True)
    main = fees[0][0]
    deductions, evidence, named = ZERO, [ctx.ev(main.doc, main.main)], []
    for rank, (line, proc) in enumerate(fees[1:], 2):
        pay_pct = _d(cfg.get("second_procedure_pct", 50) if rank == 2 else cfg.get("further_procedure_pct", 25))
        if (line.amount or ZERO) < (main.amount or ZERO) * share:   # already billed at a reduced rate
            continue
        related = [line] + [x for x in ctx.bill_lines if x is not line and x.category == BillCategory.OT_CHARGES
                            and norm(proc) in norm(x.description)]
        cut = sum((x.amount or ZERO) for x in related) * (1 - pay_pct / 100)
        deductions += cut
        evidence += [ctx.ev(x.doc, x.main) for x in related]
        named.append(f"{proc} ({num_text(pay_pct)}% payable)")
    if not named:
        return passed("Additional procedures already billed at reduced rates.")
    return flagged(Flag(
        "Additional surgery billed at full rate",
        f"{len(fees)} procedures in one admission, each billed at the full surgeon fee and OT charges. "
        f"Usual practice pays the main procedure in full and the others at a reduced rate: {'; '.join(named)}.",
        evidence, amount_at_stake=deductions.quantize(Decimal("0.01")),
        suggested_deduction=deductions.quantize(Decimal("0.01")), key="|".join(sorted(distinct))))


@check("B-10")
def above_mrp(ctx: ClaimContext) -> Result:
    if not ctx.pharmacy_lines:
        return not_applicable("No pharmacy lines.")
    tol = _d(ctx.config_of("B-10").get("tolerance_rs", 0.01))
    flags, compared = [], 0
    for doc, row in ctx.pharmacy_lines:
        mrp, rate, qty = ctx.num(row.get("mrp")), ctx.num(row.get("rate")), ctx.num(row.get("qty"))
        if mrp is None or rate is None:
            continue
        if ctx.scanned(doc):
            amount = ctx.num(row.get("amount"))
            if not all(ctx.usable(doc, row.get(k)) for k in ("mrp", "rate", "qty", "amount")) or qty is None or \
                    amount is None or abs(qty * rate - amount) > 1:
                continue
        compared += 1
        if rate <= mrp + tol:
            continue
        over = ((rate - mrp) * (qty or 1)).quantize(Decimal("0.01"))
        flags.append(Flag(
            f"Charged above MRP: {row['drug'].text}",
            f"{row['drug'].text}: {rs(rate)} per unit against an MRP of {rs(mrp)}"
            + (f", {num_text(qty)} units: {rs(over)} over MRP." if qty else "."),
            [ctx.ev(doc, row.get("rate") or row["drug"], f"{row['drug'].text}: rate {rs(rate)}, MRP {rs(mrp)}")],
            amount_at_stake=over, suggested_deduction=over, key=f"{norm(row['drug'].text)}|{rate}"))
    if not compared:
        return not_evaluable("MRP or rate not read on the pharmacy bill.")
    return flagged(*flags, detail=f"{compared} pharmacy line(s) compared with MRP.")
