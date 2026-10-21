"""Load the registries the R-layer and policy checks look things up in.

Until live feeds are connected (NMC / state medical councils, HFR / Rohini, the insurer's watchlist and its
policy administration system), these come from CSV files in one folder:

  doctors.csv     registration_no, name, qualification, council
  hospitals.csv   rohini_id, hfr_id, name, address, city, state, city_tier, nabh
  watchlist.csv   rohini_id, name, reason
  policies.csv    policy_no, member_id, member_name, product_code, sum_insured, used_si, policy_start,
                  policy_end, first_inception, room_eligibility, room_rent_cap_pct, sub_limits (JSON),
                  declared_peds (JSON list)

Each file that is present replaces that registry completely (a registry is a snapshot, not a log).
"""

import csv
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.enums import CityTier
from app.models.reference import (
    PolicyRecord,
    ReferenceImport,
    RegistryDoctor,
    RegistryHospital,
    RegistryWatchlist,
)
from app.services.reference.text import parse_bool, parse_money

FILES = ("doctors.csv", "hospitals.csv", "watchlist.csv", "policies.csv")


class RegistryImportError(Exception):
    pass


@dataclass
class RegistryCounts:
    doctors: int | None = None
    hospitals: int | None = None
    watchlist: int | None = None
    policies: int | None = None
    files: list[str] = field(default_factory=list)


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                for row in csv.DictReader(handle)]


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        raise RegistryImportError(f"Bad date {value!r} (expected YYYY-MM-DD)") from None


def _json(value: str, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        raise RegistryImportError(f"Bad JSON value {value!r}") from None


def _tier(value: str) -> CityTier:
    try:
        return CityTier(value.strip().lower()) if value else CityTier.ANY
    except ValueError:
        raise RegistryImportError(f"Unknown city tier {value!r}") from None


def import_registries(db: Session, folder: Path, *, source_name: str, version: str,
                      user_id: uuid.UUID | None = None) -> tuple[ReferenceImport, RegistryCounts]:
    present = [name for name in FILES if (folder / name).exists()]
    if not present:
        raise RegistryImportError(f"No registry files in {folder} (expected one of {', '.join(FILES)})")
    digest = hashlib.sha256()
    for name in present:
        digest.update((folder / name).read_bytes())
    rec = ReferenceImport(dataset="registries", version=version, source_name=source_name,
                          file_name=", ".join(present), file_sha256=digest.hexdigest(), imported_by=user_id)
    db.add(rec)
    db.flush()
    counts = RegistryCounts(files=present)

    if "doctors.csv" in present:
        db.execute(delete(RegistryDoctor))
        seen: set[str] = set()
        for r in _rows(folder / "doctors.csv"):
            reg = r.get("registration_no", "").upper()
            if reg and reg not in seen:
                seen.add(reg)
                db.add(RegistryDoctor(registration_no=reg, name=r.get("name", ""), import_id=rec.id,
                                      qualification=r.get("qualification") or None, council=r.get("council") or None))
        counts.doctors = len(seen)
    if "hospitals.csv" in present:
        db.execute(delete(RegistryHospital))
        seen = set()
        for r in _rows(folder / "hospitals.csv"):
            rohini = r.get("rohini_id", "")
            if rohini and rohini not in seen:
                seen.add(rohini)
                db.add(RegistryHospital(rohini_id=rohini, hfr_id=r.get("hfr_id") or None, name=r.get("name", ""),
                                        address=r.get("address") or None, city=r.get("city") or None,
                                        state=r.get("state") or None, city_tier=_tier(r.get("city_tier", "")),
                                        nabh=parse_bool(r.get("nabh")), import_id=rec.id))
        counts.hospitals = len(seen)
    if "watchlist.csv" in present:
        db.execute(delete(RegistryWatchlist))
        rows = [r for r in _rows(folder / "watchlist.csv") if r.get("rohini_id")]
        for r in rows:
            db.add(RegistryWatchlist(rohini_id=r["rohini_id"], name=r.get("name") or None,
                                     reason=r.get("reason") or "Listed", import_id=rec.id))
        counts.watchlist = len(rows)
    if "policies.csv" in present:
        db.execute(delete(PolicyRecord))
        keys: set[tuple[str, str]] = set()
        for r in _rows(folder / "policies.csv"):
            key = (r.get("policy_no", ""), r.get("member_id", ""))
            if not key[0] or key in keys:
                continue
            keys.add(key)
            cap = parse_money(r.get("room_rent_cap_pct"))
            db.add(PolicyRecord(
                policy_no=key[0], member_id=key[1] or None, member_name=r.get("member_name") or None,
                product_code=r.get("product_code") or None, sum_insured=parse_money(r.get("sum_insured")),
                used_si=parse_money(r.get("used_si")), policy_start=_date(r.get("policy_start", "")),
                policy_end=_date(r.get("policy_end", "")), first_inception=_date(r.get("first_inception", "")),
                room_eligibility=r.get("room_eligibility") or None, room_rent_cap_pct=cap,
                sub_limits=_json(r.get("sub_limits", ""), {}), declared_peds=_json(r.get("declared_peds", ""), []),
                import_id=rec.id))
        counts.policies = len(keys)
    rec.row_count = sum(n for n in (counts.doctors, counts.hospitals, counts.watchlist, counts.policies) if n)
    db.flush()
    return rec, counts
