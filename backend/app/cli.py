"""Admin commands, run inside the backend container.

Create the first admin (asks for the password, never shows it):
    docker compose exec backend python -m app.cli create-admin --email you@company.in --name "Your Name"

Reference data:
    docker compose exec backend python -m app.cli load-reference-defaults
    docker compose exec backend python -m app.cli import-tariffs --file reference_data/raw/cghs.csv \
        --source cghs --version CGHS-2025-10 --effective-from 2025-10-13 --source-name "CGHS OM 03.10.2025"
    docker compose exec backend python -m app.cli import-packages --file reference_data/raw/hbp.xlsx \
        --version HBP2022 --effective-from 2022-04-01 --source-name "NHA HBP 2022"
    docker compose exec backend python -m app.cli reference-status

Synthetic claims (after python -m app.synthetic):
    docker compose exec backend python -m app.cli import-synthetic --limit 50
"""

import argparse
import getpass
import sys
from datetime import date
from pathlib import Path

from app.db.session import SessionLocal
from app.models.enums import CityTier, RoleName, TariffSource, WardType
from app.models.reference import ReferenceImport
from app.services.audit import log_event
from app.services.reference import importers
from app.services.reference.tables import TableError
from app.services.users import UserError, create_user


def _ask_password() -> str:
    first = getpass.getpass("Password (min 10 characters, letters + numbers/symbols): ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        print("Passwords do not match.", file=sys.stderr)
        sys.exit(1)
    return first


def create_admin(email: str, name: str, password: str) -> None:
    with SessionLocal() as db:
        user = create_user(db, email=email, full_name=name, role=RoleName.ADMIN, password=password)
        log_event(db, "user.created", entity_type="user", entity_id=user.id,
                  details={"email": user.email, "role": "admin", "via": "cli"})
        db.commit()
        print(f"Admin created: {user.email}")


def _report(db, records: list[ReferenceImport]) -> None:
    for rec in records:
        log_event(db, "reference.imported", entity_type="reference_import", entity_id=str(rec.id),
                  details={"dataset": rec.dataset, "version": rec.version, "rows": rec.row_count,
                           "file": rec.file_name, "via": "cli"})
        print(f"  {rec.dataset:<13} {rec.version:<36} {rec.row_count:>7} rows  (import #{rec.id})")


def _run_import(fn) -> None:
    """Run one import in a transaction; on any error nothing is saved."""
    with SessionLocal() as db:
        try:
            records = fn(db)
        except (importers.ReferenceImportError, TableError, FileNotFoundError, ValueError) as exc:
            db.rollback()
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        _report(db, records)
        db.commit()
    print("Done.")


def _source_info(args) -> importers.SourceInfo:
    return importers.SourceInfo(version=args.version, source_name=args.source_name, source_url=args.source_url,
                                effective_from=args.effective_from, notes=args.notes)


def reference_status() -> None:
    from sqlalchemy import func, select

    from app.api.routes.reference import DATASETS

    with SessionLocal() as db:
        latest = importers.latest_imports(db)
        for key, label, model in DATASETS:
            rows = db.scalar(select(func.count()).select_from(model)) or 0
            rec = latest.get(key)
            when = f"{rec.version}, loaded {rec.imported_at:%d %b %Y}" if rec else "never loaded"
            print(f"  {label:<26} {rows:>7} rows   {when}")


def import_synthetic(folder: Path, limit: int | None, scanned: bool, now: bool) -> None:
    """Load generated claim files through the normal intake path (same checks, storage and queue)."""
    import csv
    import json

    from sqlalchemy import select

    from app.models.claim import Claim
    from app.models.enums import ClaimStage
    from app.pipeline.runner import run_pipeline
    from app.services import intake

    manifest = folder / "manifest.csv"
    if not manifest.exists():
        print(f"Error: {manifest} not found. Run: python -m app.synthetic", file=sys.stderr)
        sys.exit(1)
    rows = list(csv.DictReader(open(manifest)))[: limit or None]
    created = skipped = 0
    with SessionLocal() as db:
        for row in rows:
            claim_id = row["claim_id"]
            if db.scalar(select(Claim.id).where(Claim.claim_number == claim_id)):
                skipped += 1
                continue
            claim_dir = folder / "claims" / claim_id
            truth = json.loads((claim_dir / "ground_truth.json").read_text())
            pdf = claim_dir / ("scanned.pdf" if scanned and (claim_dir / "scanned.pdf").exists() else "claim.pdf")
            result = intake.create_claim(
                db, [intake.IncomingFile(f"{claim_id}.pdf", pdf.read_bytes())], stage=ClaimStage(truth["stage"]),
                claim_number=claim_id, source="synthetic",
            )
            log_event(db, "claim.created", entity_type="claim", entity_id=result.claim.id,
                      details={"claim_number": claim_id, "via": "cli import-synthetic", "file": pdf.name})
            db.commit()
            if now:
                run_pipeline(db, result.claim.id)
            else:
                intake.enqueue_processing(result.claim.id)
            created += 1
            if created % 25 == 0:
                print(f"  {created} imported", flush=True)
    action = "processed" if now else "queued for processing"
    print(f"Done: {created} claims imported and {action}, {skipped} already there.")


def _add_source_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--file", type=Path, required=True, help="CSV or Excel file")
    p.add_argument("--sheet", help="Excel sheet name (default: first sheet)")
    p.add_argument("--version", required=True, help="e.g. HBP2022 or CGHS-2025-10")
    p.add_argument("--source-name", required=True, help="Official document this came from")
    p.add_argument("--source-url")
    p.add_argument("--effective-from", type=date.fromisoformat, help="YYYY-MM-DD")
    p.add_argument("--notes")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("create-admin", help="Create an admin account")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    admin.add_argument("--password-stdin", action="store_true", help="Read the password from standard input")

    defaults = sub.add_parser("load-reference-defaults",
                              help="Load bundled data: IRDAI non-payables, standard policy terms, ICD-10")
    defaults.add_argument("--skip-icd", action="store_true", help="Skip the ICD-10 code set (98k rows)")

    pk = sub.add_parser("import-packages", help="Import PM-JAY (HBP) packages from CSV/Excel")
    _add_source_args(pk)

    tf = sub.add_parser("import-tariffs", help="Import a tariff (CGHS or a client's) from CSV/Excel")
    _add_source_args(tf)
    tf.add_argument("--source", choices=[s.value for s in TariffSource], required=True)
    tf.add_argument("--city-tier", choices=[t.value for t in CityTier if t != CityTier.ANY], default="tier_1",
                    help="Tier for rows that do not say (default tier_1)")
    tf.add_argument("--ward", choices=[w.value for w in WardType if w != WardType.ANY], default="semi_private",
                    help="Ward for rows that do not say (default semi_private)")

    sub.add_parser("reference-status", help="Show what reference data is loaded")

    syn = sub.add_parser("import-synthetic", help="Import generated synthetic claims as claims to process")
    syn.add_argument("--folder", type=Path, default=Path(__file__).resolve().parents[1] / "synthetic_data")
    syn.add_argument("--limit", type=int, help="Import only the first N claims")
    syn.add_argument("--scanned", action="store_true", help="Use the scanned copy where one exists")
    syn.add_argument("--now", action="store_true", help="Process here instead of in the worker")
    args = parser.parse_args(argv)

    if args.command == "load-reference-defaults":
        _run_import(lambda db: importers.load_defaults(db, include_icd=not args.skip_icd))
    elif args.command == "import-packages":
        _run_import(lambda db: [importers.import_packages(db, args.file, _source_info(args), sheet=args.sheet)])
    elif args.command == "import-tariffs":
        _run_import(lambda db: [importers.import_tariffs(
            db, args.file, TariffSource(args.source), _source_info(args), sheet=args.sheet,
            default_tier=CityTier(args.city_tier), default_ward=WardType(args.ward))])
    elif args.command == "reference-status":
        reference_status()
    elif args.command == "import-synthetic":
        import_synthetic(args.folder, args.limit, args.scanned, args.now)

    if args.command == "create-admin":
        password = sys.stdin.readline().rstrip("\n") if args.password_stdin else _ask_password()
        try:
            create_admin(args.email, args.name, password)
        except UserError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()