"""Reference data changes from the admin screen (reference.manage): file imports and policy-term edits.

The same importers as the CLI (app.cli import-packages / import-tariffs / import-registries) run here, so a
file imported from the browser is checked and versioned exactly like one imported from the command line.
Every import and every edit is in the audit log; imports are also listed in reference_imports with the file's
SHA-256, so the data behind any finding can be traced to the file it came from.
"""

import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.permissions import Permission
from app.db.session import get_db
from app.models.enums import CityTier, TariffSource, WardType
from app.models.reference import PolicyTerm
from app.models.user import User
from app.rules.library import RuleEditError, check_value
from app.schemas.reference import ImportOut, PolicyTermOut, PolicyTermUpdate
from app.services.audit import log_event
from app.services.reference import importers
from app.services.reference.registries import FILES as REGISTRY_FILES
from app.services.reference.registries import RegistryImportError, import_registries
from app.services.reference.tables import TableError

router = APIRouter(prefix="/reference", tags=["reference"])
can_manage = require_permission(Permission.REFERENCE_MANAGE)

MAX_BYTES = 25 * 1024 * 1024
TABLE_TYPES = (".csv", ".xlsx", ".xlsm")


def _save(folder: Path, upload: UploadFile, data: bytes) -> Path:
    name = Path(upload.filename or "file").name
    path = folder / name
    path.write_bytes(data)
    return path


@router.post("/imports", response_model=list[ImportOut], status_code=201)
async def import_file(
    request: Request,
    dataset: str = Form(..., pattern="^(packages|tariffs|tariff_mappings|registries)$"),
    files: list[UploadFile] = File(...),
    version: str = Form(..., min_length=1, max_length=80),
    source_name: str = Form(..., min_length=3, max_length=300),
    source_url: str | None = Form(None, max_length=500),
    effective_from: date | None = Form(None),
    notes: str | None = Form(None, max_length=2000),
    sheet: str | None = Form(None, max_length=100),
    tariff_source: TariffSource = Form(TariffSource.CGHS),
    city_tier: CityTier = Form(CityTier.TIER_1),
    ward: WardType = Form(WardType.SEMI_PRIVATE),
    db: Session = Depends(get_db),
    user: User = Depends(can_manage),
) -> list:
    blobs = []
    for upload in files:
        data = await upload.read()
        if len(data) > MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"{upload.filename} is larger than 25 MB")
        name = (upload.filename or "").lower()
        if dataset == "registries":
            if Path(name).name not in REGISTRY_FILES:
                raise HTTPException(status_code=422, detail=f"Registry files must be named {', '.join(REGISTRY_FILES)}")
        elif not name.endswith(TABLE_TYPES):
            raise HTTPException(status_code=422, detail="Upload a CSV or Excel (.xlsx) file")
        blobs.append((upload, data))
    if dataset != "registries" and len(blobs) != 1:
        raise HTTPException(status_code=422, detail="Upload one file for this dataset")
    info = importers.SourceInfo(version=version.strip(), source_name=source_name.strip(),
                                source_url=(source_url or "").strip() or None, effective_from=effective_from,
                                notes=(notes or "").strip() or None)
    savepoint = db.begin_nested()       # a refused file must not leave half an import behind
    try:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            paths = [_save(folder, u, d) for u, d in blobs]
            if dataset == "packages":
                done = [importers.import_packages(db, paths[0], info, sheet=sheet or None, user_id=user.id)]
            elif dataset == "tariffs":
                if effective_from is None:
                    raise HTTPException(status_code=422, detail="Give the date the rates take effect")
                done = [importers.import_tariffs(db, paths[0], tariff_source, info, sheet=sheet or None,
                                                 default_tier=city_tier, default_ward=ward, user_id=user.id)]
            elif dataset == "tariff_mappings":
                done = [importers.import_tariff_mappings(db, paths[0], info, sheet=sheet or None, user_id=user.id)]
            else:
                rec, _counts = import_registries(db, folder, source_name=info.source_name, version=info.version,
                                                 user_id=user.id)
                done = [rec]
    except (TableError, RegistryImportError, importers.ReferenceImportError, ValueError) as exc:
        savepoint.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        savepoint.rollback()
        raise
    savepoint.commit()
    for rec in done:
        log_event(db, "reference.imported", entity_type="reference_import", entity_id=str(rec.id), user_id=user.id,
                  request=request, details={"dataset": rec.dataset, "version": rec.version, "rows": rec.row_count,
                                            "file": rec.file_name, "sha256": rec.file_sha256})
    db.commit()
    return done


@router.put("/policy-terms/{key}", response_model=PolicyTermOut)
def edit_policy_term(key: str, body: PolicyTermUpdate, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(can_manage)) -> PolicyTerm:
    """Change one policy term's value, e.g. the PED waiting period. The value keeps its shape."""
    term = db.scalar(select(PolicyTerm).where(PolicyTerm.product_code == body.product_code, PolicyTerm.key == key))
    if term is None:
        raise HTTPException(status_code=404, detail="No such policy term")
    reason = body.reason.strip()
    if len(reason) < 5:
        raise HTTPException(status_code=422, detail="Give the reason for this change")
    try:
        value = check_value(key, body.value, term.value)
    except RuleEditError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if value == term.value:
        raise HTTPException(status_code=422, detail="Nothing changed")
    old = term.value
    term.value = value  # type: ignore[assignment]
    log_event(db, "reference.policy_term_updated", entity_type="policy_term", entity_id=str(term.id), user_id=user.id,
              request=request, details={"product_code": term.product_code, "key": key, "old": old, "new": value,
                                        "reason": reason})
    db.commit()
    db.refresh(term)
    return term
