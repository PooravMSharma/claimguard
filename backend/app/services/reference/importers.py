"""Load reference datasets into the database.

Every load is recorded as a ReferenceImport (what, which version, from which
official source, valid from when, file checksum, who ran it). Re-loading the
same version replaces it; loading a newer tariff closes the old rates the day
before the new ones start, so older claims are still checked against the rates
that applied on their admission date.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.enums import CityTier, TariffSource, WardType
from app.models.reference import (
    Icd10Code,
    NonPayableItem,
    Package,
    PolicyTerm,
    ReferenceImport,
    TariffRate,
)
from app.services.reference.paths import ICD10_FILE, NON_PAYABLES_FILE, POLICY_TERMS_FILE
from app.services.reference.tables import read_table
from app.services.reference.tariffs import is_ward_uniform
from app.services.reference.text import parse_bool, parse_int, parse_money, split_list

DATASETS = ("packages", "tariffs", "non_payables", "icd10", "policy_terms", "registries")


class ReferenceImportError(Exception):
    """Raised for a bad file or bad options. Nothing is saved."""


@dataclass
class SourceInfo:
    version: str
    source_name: str
    source_url: str | None = None
    effective_from: date | None = None
    notes: str | None = None


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record(db: Session, dataset: str, info: SourceInfo, path: Path | None, user_id: uuid.UUID | None,
            ) -> ReferenceImport:
    rec = ReferenceImport(
        dataset=dataset, version=info.version, source_name=info.source_name, source_url=info.source_url,
        effective_from=info.effective_from, file_name=path.name if path else None,
        file_sha256=sha256_of(path) if path else None, notes=info.notes, imported_by=user_id, row_count=0,
    )
    db.add(rec)
    db.flush()
    return rec


# ---------------------------------------------------------------- non-payables

def import_non_payables(db: Session, path: Path = NON_PAYABLES_FILE, info: SourceInfo | None = None,
                        user_id: uuid.UUID | None = None) -> ReferenceImport:
    info = info or SourceInfo(
        version="IRDAI-2019",
        source_name="IRDAI Guidelines on Standardization in Health Insurance (2019), Annexure: List I-IV "
                    "of items not payable / subsumed (synonyms added by ClaimGuard)",
        source_url="https://irdai.gov.in/",
        effective_from=date(2020, 4, 1),
    )
    rows = read_table(path, {"list_number": ["list"], "item_name": ["item", "name"], "synonyms": [], "notes": []},
                      required=["list_number", "item_name"])
    rec = _record(db, "non_payables", info, path, user_id)
    db.execute(delete(NonPayableItem))
    for r in rows:
        number = parse_int(r["list_number"])
        if number not in (1, 2, 3, 4):
            raise ReferenceImportError(f"Bad list number {r['list_number']!r} for {r['item_name']!r}")
        db.add(NonPayableItem(list_number=number, item_name=str(r["item_name"]).strip(),
                              synonyms=split_list(r.get("synonyms")), notes=(r.get("notes") or None),
                              import_id=rec.id))
    rec.row_count = len(rows)
    db.flush()
    return rec


# ---------------------------------------------------------------- policy terms

def import_policy_terms(db: Session, path: Path = POLICY_TERMS_FILE, user_id: uuid.UUID | None = None,
                        ) -> ReferenceImport:
    data = json.loads(path.read_text())
    product = data.get("product_code", "STANDARD")
    info = SourceInfo(version=data["version"], source_name=data["source_name"], source_url=data.get("source_url"),
                      effective_from=date.fromisoformat(data["effective_from"]) if data.get("effective_from") else None,
                      notes=data.get("notes"))
    rec = _record(db, "policy_terms", info, path, user_id)
    for term in data["terms"]:
        values = {"product_code": product, "key": term["key"], "value": term["value"],
                  "description": term.get("description"), "source": term.get("source"), "import_id": rec.id}
        stmt = pg_insert(PolicyTerm).values(**values)
        db.execute(stmt.on_conflict_do_update(
            constraint="uq_policy_terms_product_key",
            set_={k: stmt.excluded[k] for k in ("value", "description", "source", "import_id")},
        ))
    rec.row_count = len(data["terms"])
    db.flush()
    return rec


# ---------------------------------------------------------------- ICD-10

def import_icd10(db: Session, path: Path = ICD10_FILE, info: SourceInfo | None = None,
                 user_id: uuid.UUID | None = None) -> ReferenceImport:
    info = info or SourceInfo(
        version="ICD-10-CM (simple-icd-10-cm 1.5.0)",
        source_name="ICD-10-CM code set (US NCHS/CDC, public domain), via the simple-icd-10-cm package",
        source_url="https://www.cdc.gov/nchs/icd/icd-10-cm/",
        notes="Clinical modification of WHO ICD-10. Lookups fall back to the 3-character category for WHO-only codes.",
    )
    rows = read_table(path, {"code": [], "description": [], "chapter": [], "category": [], "is_billable": []},
                      required=["code", "description"])
    rec = _record(db, "icd10", info, path, user_id)
    db.execute(delete(Icd10Code))
    batch: list[dict] = []
    for r in rows:
        code = str(r["code"]).strip().upper()
        batch.append({"code": code, "description": str(r["description"])[:500], "chapter": r.get("chapter") or None,
                      "category": (r.get("category") or code.split(".")[0]),
                      "is_billable": parse_bool(r.get("is_billable")) is not False, "import_id": rec.id})
        if len(batch) >= 5000:
            db.execute(pg_insert(Icd10Code), batch)
            batch.clear()
    if batch:
        db.execute(pg_insert(Icd10Code), batch)
    rec.row_count = len(rows)
    db.flush()
    return rec


# ---------------------------------------------------------------- packages (PM-JAY HBP)

PACKAGE_COLUMNS = {
    "code": ["package code", "procedure code", "hbp code", "package id", "procedure id"],
    "name": ["package name", "procedure name", "procedure", "package", "name of the procedure"],
    "specialty": ["speciality", "specialty name", "speciality name"],
    "rate": ["package rate", "package price", "price", "rate (inr)", "rate inr", "amount"],
    "expected_los_days": ["los", "alos", "length of stay", "expected los", "alos days"],
    "is_day_care": ["day care", "daycare"],
    "mandatory_documents": ["mandatory documents", "documents required"],
    "inclusions": ["package inclusions"],
    "keywords": ["icd codes", "diagnosis codes", "match terms", "diagnosis keywords"],
}


def import_packages(db: Session, path: Path, info: SourceInfo, sheet: str | None = None,
                    user_id: uuid.UUID | None = None) -> ReferenceImport:
    rows = read_table(path, PACKAGE_COLUMNS, required=["code", "name", "rate"], sheet=sheet)
    if not rows:
        raise ReferenceImportError("The file has a header but no rows")
    rec = _record(db, "packages", info, path, user_id)
    db.execute(delete(Package).where(Package.version == info.version))
    seen: set[str] = set()
    for r in rows:
        code = str(r["code"]).strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        db.add(Package(code=code, version=info.version, name=str(r["name"]).strip()[:500],
                       specialty=(str(r["specialty"]).strip() or None) if r.get("specialty") else None,
                       rate=parse_money(r["rate"]), expected_los_days=parse_int(r.get("expected_los_days")),
                       is_day_care=parse_bool(r.get("is_day_care")),
                       mandatory_documents=split_list(r.get("mandatory_documents")),
                       inclusions=split_list(r.get("inclusions")),
                       keywords=split_list(r.get("keywords")),
                       effective_from=info.effective_from, import_id=rec.id))
    rec.row_count = len(seen)
    db.flush()
    return rec


# ---------------------------------------------------------------- tariffs (CGHS / client)

TARIFF_COLUMNS = {
    "code": ["cghs code", "procedure code", "code no", "item code"],
    "name": ["cghs treatment procedure/investigation list", "treatment procedure investigation list",
             "procedure name", "investigation", "procedure", "description", "name of procedure"],
    "category": ["section", "type", "group"],
    "specialty": ["speciality", "speciality classification", "specialty classification"],
    "rate": ["rate (inr)", "amount"],
    "rate_non_nabh": ["non nabh", "nonnabh", "non-nabh rate", "non nabh rate", "rates for non nabh"],
    "rate_nabh": ["nabh", "nabh rate", "rates for nabh"],
    "rate_super_speciality": ["super speciality", "superspeciality", "super speciality rate", "super specialty"],
    "city_tier": ["tier", "city"],
    "ward_type": ["ward"],
    "max_los_days": ["los", "length of stay", "max los"],
}
GRADE_COLUMNS = {"rate_non_nabh": (False, False), "rate_nabh": (True, False), "rate_super_speciality": (True, True)}


def _tier(value: object, default: CityTier) -> CityTier:
    text = str(value or "").strip().lower().replace(" ", "_")
    aliases = {"x": "tier_1", "y": "tier_2", "z": "tier_3", "1": "tier_1", "2": "tier_2", "3": "tier_3",
               "tier1": "tier_1", "tier2": "tier_2", "tier3": "tier_3", "tier_i": "tier_1", "tier_ii": "tier_2",
               "tier_iii": "tier_3"}
    text = aliases.get(text, text)
    try:
        return CityTier(text) if text else default
    except ValueError:
        raise ReferenceImportError(f"Unknown city tier {value!r}")


def _ward(value: object, default: WardType) -> WardType:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return WardType(text) if text else default
    except ValueError:
        raise ReferenceImportError(f"Unknown ward type {value!r}")


def import_tariffs(db: Session, path: Path, source: TariffSource, info: SourceInfo, *, sheet: str | None = None,
                   default_tier: CityTier = CityTier.TIER_1, default_ward: WardType = WardType.SEMI_PRIVATE,
                   user_id: uuid.UUID | None = None) -> ReferenceImport:
    if info.effective_from is None:
        raise ReferenceImportError("Tariffs need an effective-from date")
    rows = read_table(path, TARIFF_COLUMNS, required=["code", "name"], sheet=sheet)
    rate_cols = [c for c in ("rate", *GRADE_COLUMNS) if any(r.get(c) not in (None, "") for r in rows[:50])]
    if not rate_cols:
        raise ReferenceImportError("No rate column found (expected Rate, or Non-NABH / NABH / Super Speciality)")

    rec = _record(db, "tariffs", info, path, user_id)
    # Same source + same start date = a re-load: replace those rows.
    db.execute(delete(TariffRate).where(TariffRate.source == source,
                                        TariffRate.effective_from == info.effective_from))
    # Older open-ended rates stop the day before the new ones start.
    db.execute(update(TariffRate)
               .where(TariffRate.source == source, TariffRate.effective_to.is_(None),
                      TariffRate.effective_from < info.effective_from)
               .values(effective_to=info.effective_from - timedelta(days=1)))

    count = 0
    for r in rows:
        code = str(r["code"] or "").strip().upper()
        name = str(r["name"] or "").strip()
        if not code or not name:
            continue
        category = (str(r["category"]).strip() or None) if r.get("category") else None
        common = dict(
            source=source, code=code, name=name[:500], category=category[:80] if category else None,
            specialty=(str(r["specialty"]).strip()[:120] or None) if r.get("specialty") else None,
            city_tier=_tier(r.get("city_tier"), default_tier), ward_type=_ward(r.get("ward_type"), default_ward),
            ward_uniform=is_ward_uniform(category, name), max_los_days=parse_int(r.get("max_los_days")),
            effective_from=info.effective_from, import_id=rec.id,
        )
        for col in rate_cols:
            amount = parse_money(r.get(col))
            if amount is None:
                continue
            nabh, super_sp = GRADE_COLUMNS.get(col, (None, False))
            db.add(TariffRate(rate=amount, nabh_accredited=nabh, super_speciality=super_sp, **common))
            count += 1
    if count == 0:
        raise ReferenceImportError("No rows with a rate were found")
    rec.row_count = count
    db.flush()
    return rec


# ---------------------------------------------------------------- defaults

def load_defaults(db: Session, include_icd: bool = True, user_id: uuid.UUID | None = None) -> list[ReferenceImport]:
    """Load the datasets bundled with ClaimGuard (non-payables, policy terms, ICD-10)."""
    done = [import_non_payables(db, user_id=user_id), import_policy_terms(db, user_id=user_id)]
    if include_icd:
        done.append(import_icd10(db, user_id=user_id))
    return done


def latest_imports(db: Session) -> dict[str, ReferenceImport]:
    latest: dict[str, ReferenceImport] = {}
    for rec in db.scalars(select(ReferenceImport).order_by(ReferenceImport.imported_at, ReferenceImport.id)):
        latest[rec.dataset] = rec
    return latest
