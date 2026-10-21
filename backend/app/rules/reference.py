"""Reference data the checks look things up in, loaded once per run.

Everything here is read-only and plain Python, so checks can be unit-tested without a database and the
offline evaluation can build the same object from files.
"""

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.reference import (
    NonPayableItem,
    Package,
    PolicyRecord,
    PolicyTerm,
    RegistryDoctor,
    RegistryHospital,
    RegistryWatchlist,
    TariffRate,
)
from app.services.reference.non_payables import NonPayableMatcher
from app.services.reference.text import normalise, parse_bool, parse_money, split_list

POLICY_TERMS_FILE = Path(__file__).resolve().parents[2] / "reference_data" / "policy_terms_standard.json"
NON_PAYABLES_FILE = Path(__file__).resolve().parents[2] / "reference_data" / "irdai_non_payables.csv"


@dataclass(frozen=True)
class PackageRef:
    code: str
    name: str
    rate: Decimal | None
    los_days: int | None
    day_care: bool | None
    inclusions: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class TariffRef:
    code: str
    name: str
    category: str | None
    city_tier: str        # tier_1 | tier_2 | tier_3 | any
    ward_type: str        # general | semi_private | private | any
    rate: Decimal
    effective_from: date | None = None
    effective_to: date | None = None

    def in_force(self, on: date | None) -> bool:
        if on is None:
            return self.effective_to is None
        return (self.effective_from is None or self.effective_from <= on) and \
            (self.effective_to is None or on <= self.effective_to)


@dataclass(frozen=True)
class HospitalRef:
    rohini_id: str
    hfr_id: str | None
    name: str
    city: str | None
    city_tier: str


@dataclass(frozen=True)
class PolicyRef:
    policy_no: str
    member_id: str | None
    member_name: str | None
    product_code: str | None
    sum_insured: Decimal | None
    used_si: Decimal | None
    start: date | None
    end: date | None
    inception: date | None
    room_eligibility: str | None
    room_rent_cap_pct: Decimal | None
    sub_limits: dict = field(default_factory=dict)
    declared_peds: tuple[str, ...] = ()


@dataclass
class Reference:
    terms: dict[str, dict[str, dict]]                       # product_code -> key -> value ("STANDARD" = default)
    non_payables: NonPayableMatcher | None = None
    packages: list[PackageRef] = field(default_factory=list)
    tariffs: list[TariffRef] = field(default_factory=list)
    doctors: dict[str, str] | None = None                   # registration no -> name (None = not loaded)
    hospitals: dict[str, HospitalRef] | None = None
    watchlist: dict[str, str] | None = None                 # rohini id -> reason
    policies: dict[str, list[PolicyRef]] | None = None      # policy no -> members

    def term(self, key: str, product: str | None = None) -> dict | None:
        """A policy term: the product's own value if it has one, else the standard value."""
        if product and key in self.terms.get(product, {}):
            return self.terms[product][key]
        return self.terms.get("STANDARD", {}).get(key)

    def package(self, code: str) -> PackageRef | None:
        code = code.strip().upper()
        return next((p for p in self.packages if p.code == code), None)

    def policy(self, policy_no: str | None, member_id: str | None = None) -> PolicyRef | None:
        if not policy_no or self.policies is None:
            return None
        members = self.policies.get(_policy_key(policy_no), [])
        if member_id:
            hit = next((m for m in members if m.member_id and m.member_id.upper() == member_id.upper()), None)
            if hit:
                return hit
        return members[0] if len(members) == 1 else None

    # ------------------------------------------------------------ loading

    @classmethod
    def from_db(cls, db: Session) -> "Reference":
        terms: dict[str, dict[str, dict]] = {}
        for t in db.scalars(select(PolicyTerm)):
            terms.setdefault(t.product_code, {})[t.key] = t.value
        items = list(db.scalars(select(NonPayableItem)))
        packages = [PackageRef(p.code, p.name, p.rate, p.expected_los_days, p.is_day_care, tuple(p.inclusions or []),
                               tuple(p.keywords or [])) for p in db.scalars(select(Package))]
        tariffs = [TariffRef(t.code, t.name, t.category, t.city_tier.value, t.ward_type.value, t.rate,
                             t.effective_from, t.effective_to) for t in db.scalars(select(TariffRate))]
        doctors = {d.registration_no.upper(): d.name for d in db.scalars(select(RegistryDoctor))}
        hospitals = {h.rohini_id: HospitalRef(h.rohini_id, h.hfr_id, h.name, h.city, h.city_tier.value)
                     for h in db.scalars(select(RegistryHospital))}
        watch = {w.rohini_id: w.reason for w in db.scalars(select(RegistryWatchlist))}
        policies: dict[str, list[PolicyRef]] = {}
        for r in db.scalars(select(PolicyRecord)):
            policies.setdefault(_policy_key(r.policy_no), []).append(PolicyRef(
                r.policy_no, r.member_id, r.member_name, r.product_code, r.sum_insured, r.used_si, r.policy_start,
                r.policy_end, r.first_inception, r.room_eligibility, r.room_rent_cap_pct, dict(r.sub_limits or {}),
                tuple(r.declared_peds or [])))
        registries_loaded = bool(doctors or hospitals)
        return cls(terms=terms, non_payables=NonPayableMatcher(items) if items else None, packages=packages,
                   tariffs=tariffs, doctors=doctors if registries_loaded else None,
                   hospitals=hospitals if registries_loaded else None,
                   watchlist=watch if registries_loaded else None, policies=policies or None)

    @classmethod
    def from_files(cls, synthetic_root: Path | None = None) -> "Reference":
        """Bundled defaults plus, optionally, a synthetic set's registries and benchmarks (offline evaluation)."""
        data = json.loads(POLICY_TERMS_FILE.read_text())
        terms = {data.get("product_code", "STANDARD"): {t["key"]: t["value"] for t in data["terms"]}}
        items = []
        with open(NON_PAYABLES_FILE, newline="") as handle:
            for n, row in enumerate(csv.DictReader(handle), 1):
                items.append(NonPayableItem(id=n, list_number=int(row["list_number"]), item_name=row["item_name"],
                                            synonyms=split_list(row.get("synonyms")), notes=row.get("notes")))
        ref = cls(terms=terms, non_payables=NonPayableMatcher(items))
        if synthetic_root is None:
            return ref
        refdir, regdir = synthetic_root / "reference", synthetic_root / "registries"
        for row in _csv(refdir / "packages.csv"):
            ref.packages.append(PackageRef(row["code"], row["name"], parse_money(row["rate"]),
                                           int(row["expected_los_days"]) if row.get("expected_los_days") else None,
                                           parse_bool(row.get("is_day_care")), tuple(split_list(row.get("inclusions"))),
                                           tuple(split_list(row.get("keywords")))))
        for row in _csv(refdir / "tariffs.csv"):
            ref.tariffs.append(TariffRef(row["code"], row["name"], row.get("category") or None, row["city_tier"],
                                         row["ward_type"], parse_money(row["rate"]) or Decimal(0)))
        ref.doctors = {r["registration_no"].upper(): r["name"] for r in _csv(regdir / "doctors.csv")}
        ref.hospitals = {r["rohini_id"]: HospitalRef(r["rohini_id"], r.get("hfr_id"), r["name"], r.get("city"),
                                                     r.get("city_tier") or "any")
                         for r in _csv(regdir / "hospitals.csv")}
        ref.watchlist = {r["rohini_id"]: r["reason"] for r in _csv(regdir / "watchlist.csv")}
        ref.policies = {}
        for r in _csv(regdir / "policies.csv"):
            ref.policies.setdefault(_policy_key(r["policy_no"]), []).append(PolicyRef(
                r["policy_no"], r.get("member_id") or None, r.get("member_name") or None, r.get("product_code") or None,
                parse_money(r.get("sum_insured")), parse_money(r.get("used_si")), _date(r.get("policy_start")),
                _date(r.get("policy_end")), _date(r.get("first_inception")), r.get("room_eligibility") or None,
                parse_money(r.get("room_rent_cap_pct")), json.loads(r.get("sub_limits") or "{}"),
                tuple(json.loads(r.get("declared_peds") or "[]"))))
        return ref


def _policy_key(policy_no: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", policy_no.upper())


def _csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def norm(text: object) -> str:
    """Lower-case words only, for comparing names and descriptions."""
    return normalise(str(text or ""))
