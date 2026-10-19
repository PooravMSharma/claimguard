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

    if args.command == "create-admin":
        password = sys.stdin.readline().rstrip("\n") if args.password_stdin else _ask_password()
        try:
            create_admin(args.email, args.name, password)
        except UserError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
