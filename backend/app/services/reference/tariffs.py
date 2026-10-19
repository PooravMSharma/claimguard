"""Tariff lookups and the CGHS rate calculator.

CGHS publishes one base rate table (Tier I city, semi-private ward) with
Non-NABH, NABH and Super Speciality columns. Every other combination is worked
out with the percentages in the OM, which are kept in
reference_data/cghs_2025_adjustments.json rather than in code, so a new OM
means a new data file, not a code change.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.enums import CityTier, TariffSource, WardType
from app.models.reference import ReferenceImport, TariffRate
from app.services.reference.paths import CGHS_ADJUSTMENTS_FILE

GRADES = ("non_nabh", "nabh", "super_speciality")


class TariffError(Exception):
    pass


@lru_cache
def cghs_rules() -> dict:
    return json.loads(CGHS_ADJUSTMENTS_FILE.read_text())


def is_ward_uniform(category: str | None, name: str | None) -> bool:
    """Consultations, investigations, radiotherapy, day care and minor procedures: same rate in every ward."""
    text = f"{category or ''} {name or ''}".lower()
    return any(re.search(rf"\b{re.escape(k)}", text) for k in cghs_rules()["ward_uniform_keywords"])


def grade_of(row: TariffRate) -> str:
    if row.super_speciality:
        return "super_speciality"
    return "non_nabh" if row.nabh_accredited is False else "nabh"


def rates_in_force(db: Session, source: TariffSource, code: str, on: date) -> list[TariffRate]:
    stmt = select(TariffRate).where(
        TariffRate.source == source,
        TariffRate.code == code.strip().upper(),
        TariffRate.effective_from <= on,
        or_(TariffRate.effective_to.is_(None), TariffRate.effective_to >= on),
    )
    return list(db.scalars(stmt))


@dataclass
class Adjustment:
    step: str
    factor: str
    note: str


@dataclass
class PriceResult:
    source: str
    code: str
    name: str
    category: str | None
    city_tier: str
    grade: str
    ward_type: str
    ward_uniform: bool
    base_rate: Decimal
    base_grade: str
    base_city_tier: str
    rate: Decimal
    effective_from: date
    adjustments: list[Adjustment] = field(default_factory=list)
    source_name: str | None = None
    source_url: str | None = None


def _pick_base(rows: list[TariffRate], tier: CityTier, grade: str) -> TariffRate | None:
    """Best published row: exact tier and grade, then Tier I same grade, then Tier I NABH."""
    def find(t: CityTier, g: str) -> TariffRate | None:
        return next((r for r in rows if r.city_tier in (t, CityTier.ANY) and grade_of(r) == g), None)

    return find(tier, grade) or find(CityTier.TIER_1, grade) or find(tier, "nabh") or find(CityTier.TIER_1, "nabh")


def cghs_price(
    db: Session,
    code: str,
    *,
    city_tier: CityTier = CityTier.TIER_1,
    grade: str = "nabh",
    ward_type: WardType = WardType.SEMI_PRIVATE,
    on: date | None = None,
) -> PriceResult:
    if grade not in GRADES:
        raise TariffError(f"grade must be one of {', '.join(GRADES)}")
    if city_tier == CityTier.ANY or ward_type == WardType.ANY:
        raise TariffError("Give a specific city tier and ward type")
    on = on or date.today()
    rows = rates_in_force(db, TariffSource.CGHS, code, on)
    if not rows:
        raise TariffError(f"No CGHS rate for {code.upper()} in force on {on.isoformat()}")
    base = _pick_base(rows, city_tier, grade)
    if base is None:
        raise TariffError(f"CGHS rate for {code.upper()} has no usable NABH base rate")

    rules = cghs_rules()
    rate = Decimal(base.rate)
    adjustments: list[Adjustment] = []

    base_grade = grade_of(base)
    if base_grade != grade:
        factor = Decimal(str(rules["grade_factor"][grade])) / Decimal(str(rules["grade_factor"][base_grade]))
        rate *= factor
        adjustments.append(Adjustment("hospital grade", f"{base_grade} → {grade} × {factor.normalize()}",
                                      rules["grade_factor"]["note"]))

    base_tier = base.city_tier if base.city_tier != CityTier.ANY else CityTier.TIER_1
    if base_tier != city_tier:
        factor = Decimal(str(rules["city_tier_factor"][city_tier.value])) / Decimal(
            str(rules["city_tier_factor"][base_tier.value]))
        rate *= factor
        adjustments.append(Adjustment("city tier", f"{base_tier.value} → {city_tier.value} × {factor.normalize()}",
                                      rules["city_tier_factor"]["note"]))

    if base.ward_uniform:
        if ward_type != WardType.SEMI_PRIVATE:
            adjustments.append(Adjustment("ward", "× 1 (same for every ward)", rules["ward_uniform_note"]))
    else:
        base_ward = base.ward_type if base.ward_type != WardType.ANY else WardType.SEMI_PRIVATE
        if base_ward != ward_type:
            factor = Decimal(str(rules["ward_factor"][ward_type.value])) / Decimal(
                str(rules["ward_factor"][base_ward.value]))
            rate *= factor
            adjustments.append(Adjustment("ward", f"{base_ward.value} → {ward_type.value} × {factor.normalize()}",
                                          rules["ward_factor"]["note"]))

    source = db.get(ReferenceImport, base.import_id) if base.import_id else None
    return PriceResult(
        source="cghs", code=base.code, name=base.name, category=base.category,
        city_tier=city_tier.value, grade=grade, ward_type=ward_type.value, ward_uniform=base.ward_uniform,
        base_rate=Decimal(base.rate), base_grade=base_grade, base_city_tier=base_tier.value,
        rate=rate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        effective_from=base.effective_from, adjustments=adjustments,
        source_name=source.source_name if source else None, source_url=source.source_url if source else None,
    )
