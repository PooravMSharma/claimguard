"""Reference data search and lookups. Read-only: data is loaded with the CLI (python -m app.cli)."""

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.permissions import Permission
from app.db.session import get_db
from app.models.enums import CityTier, TariffSource, WardType
from app.models.reference import Icd10Code, NonPayableItem, Package, PolicyTerm, ReferenceImport, TariffRate
from app.models.user import User
from app.schemas.reference import (
    DatasetSummary,
    IcdLookupOut,
    IcdOut,
    ImportOut,
    MatchOut,
    MatchRequest,
    NonPayableOut,
    PackageOut,
    Page,
    PolicyTermOut,
    PriceOut,
    TariffOut,
)
from app.services.reference import icd10
from app.services.reference.importers import latest_imports
from app.services.reference.non_payables import NonPayableMatcher
from app.services.reference.tariffs import GRADES, TariffError, cghs_price

router = APIRouter(prefix="/reference", tags=["reference"])
can_view = require_permission(Permission.REFERENCE_VIEW)

DATASETS = [
    ("packages", "PM-JAY packages", Package),
    ("tariffs", "Tariff rates (CGHS)", TariffRate),
    ("non_payables", "IRDAI non-payable items", NonPayableItem),
    ("icd10", "ICD-10 codes", Icd10Code),
    ("policy_terms", "Policy terms", PolicyTerm),
]


def _like(text: str) -> str:
    escaped = text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _page(db: Session, stmt, order, limit: int, offset: int) -> tuple[list, int]:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    items = list(db.scalars(stmt.order_by(*order).limit(limit).offset(offset)))
    return items, total


@router.get("/summary", response_model=list[DatasetSummary])
def summary(db: Session = Depends(get_db), _: User = Depends(can_view)) -> list[DatasetSummary]:
    latest = latest_imports(db)
    out = []
    for key, label, model in DATASETS:
        rows = db.scalar(select(func.count()).select_from(model)) or 0
        rec = latest.get(key)
        out.append(DatasetSummary(dataset=key, label=label, rows=rows,
                                  latest_import=ImportOut.model_validate(rec) if rec else None))
    return out


@router.get("/imports", response_model=list[ImportOut])
def imports(dataset: str | None = None, db: Session = Depends(get_db), _: User = Depends(can_view)) -> list:
    stmt = select(ReferenceImport).order_by(ReferenceImport.imported_at.desc(), ReferenceImport.id.desc())
    if dataset:
        stmt = stmt.where(ReferenceImport.dataset == dataset)
    return list(db.scalars(stmt.limit(200)))


@router.get("/packages", response_model=Page[PackageOut])
def packages(
    q: str | None = Query(default=None, max_length=100),
    specialty: str | None = Query(default=None, max_length=120),
    version: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(can_view),
) -> Page[PackageOut]:
    stmt = select(Package)
    if q:
        stmt = stmt.where(or_(Package.code.ilike(_like(q)), Package.name.ilike(_like(q))))
    if specialty:
        stmt = stmt.where(Package.specialty == specialty)
    if version:
        stmt = stmt.where(Package.version == version)
    items, total = _page(db, stmt, [Package.code, Package.version], limit, offset)
    return Page[PackageOut](items=items, total=total, limit=limit, offset=offset)


@router.get("/packages/{code}", response_model=list[PackageOut])
def package_versions(code: str, db: Session = Depends(get_db), _: User = Depends(can_view)) -> list:
    rows = list(db.scalars(select(Package).where(Package.code == code.upper()).order_by(Package.version.desc())))
    if not rows:
        raise HTTPException(status_code=404, detail="Package not found")
    return rows


@router.get("/tariffs", response_model=Page[TariffOut])
def tariffs(
    q: str | None = Query(default=None, max_length=100),
    source: TariffSource | None = None,
    on: date | None = Query(default=None, description="Only rates in force on this date (default: today)"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(can_view),
) -> Page[TariffOut]:
    on = on or date.today()
    stmt = select(TariffRate).where(
        TariffRate.effective_from <= on,
        or_(TariffRate.effective_to.is_(None), TariffRate.effective_to >= on),
    )
    if q:
        stmt = stmt.where(or_(TariffRate.code.ilike(_like(q)), TariffRate.name.ilike(_like(q))))
    if source:
        stmt = stmt.where(TariffRate.source == source)
    items, total = _page(db, stmt, [TariffRate.source, TariffRate.code, TariffRate.super_speciality,
                                    TariffRate.nabh_accredited], limit, offset)
    return Page[TariffOut](items=items, total=total, limit=limit, offset=offset)


@router.get("/tariffs/cghs/{code}/price", response_model=PriceOut)
def cghs_rate(
    code: str,
    city_tier: CityTier = CityTier.TIER_1,
    grade: str = Query(default="nabh", pattern="^(" + "|".join(GRADES) + ")$"),
    ward_type: WardType = WardType.SEMI_PRIVATE,
    on: date | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(can_view),
) -> PriceOut:
    try:
        result = cghs_price(db, code, city_tier=city_tier, grade=grade, ward_type=ward_type, on=on)
    except TariffError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return PriceOut(**asdict(result))


@router.get("/non-payables", response_model=list[NonPayableOut])
def non_payables(
    list_number: int | None = Query(default=None, ge=1, le=4),
    q: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
    _: User = Depends(can_view),
) -> list:
    stmt = select(NonPayableItem).order_by(NonPayableItem.list_number, NonPayableItem.id)
    if list_number:
        stmt = stmt.where(NonPayableItem.list_number == list_number)
    if q:
        stmt = stmt.where(NonPayableItem.item_name.ilike(_like(q)))
    return list(db.scalars(stmt))


@router.post("/non-payables/match", response_model=list[MatchOut])
def match_non_payables(body: MatchRequest, db: Session = Depends(get_db),
                       _: User = Depends(can_view)) -> list[MatchOut]:
    matcher = NonPayableMatcher.from_db(db)
    out = []
    for line in body.lines:
        hit = matcher.match(line)
        if hit:
            out.append(MatchOut(line=line, matched=True, list_number=hit.list_number, item_name=hit.item_name,
                                matched_term=hit.matched_term, method=hit.method, score=hit.score, notes=hit.notes))
        else:
            out.append(MatchOut(line=line, matched=False))
    return out


@router.get("/icd10", response_model=Page[IcdOut])
def icd10_search(
    q: str = Query(min_length=2, max_length=100),
    billable_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(can_view),
) -> Page[IcdOut]:
    code = icd10.normalise_code(q)
    if code:
        stmt = select(Icd10Code).where(Icd10Code.code.startswith(code))
    else:
        words = [w for w in q.split() if w]
        stmt = select(Icd10Code).where(*[Icd10Code.description.ilike(_like(w)) for w in words])
    if billable_only:
        stmt = stmt.where(Icd10Code.is_billable.is_(True))
    items, total = _page(db, stmt, [func.length(Icd10Code.code), Icd10Code.code], limit, offset)
    return Page[IcdOut](items=items, total=total, limit=limit, offset=offset)


@router.get("/icd10/lookup/{code}", response_model=IcdLookupOut)
def icd10_lookup(code: str, db: Session = Depends(get_db), _: User = Depends(can_view)) -> IcdLookupOut:
    return IcdLookupOut(**asdict(icd10.lookup(db, code)))


@router.get("/policy-terms", response_model=list[PolicyTermOut])
def policy_terms(product_code: str = "STANDARD", db: Session = Depends(get_db),
                 _: User = Depends(can_view)) -> list:
    return list(db.scalars(select(PolicyTerm).where(PolicyTerm.product_code == product_code)
                           .order_by(PolicyTerm.id)))
