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
    docker compose exec backend python -m app.cli tariff-match-report --out storage/tariff_matches.csv
    docker compose exec backend python -m app.cli import-tariff-mappings --file storage/tariff_matches.csv \
        --version links-2026-10 --source-name "Bill wording links confirmed by <name>"

Synthetic claims (after python -m app.synthetic):
    docker compose exec backend python -m app.cli import-synthetic --limit 50
    docker compose exec backend python -m app.cli import-synthetic --limit 20 --scanned
    docker compose exec backend python -m app.cli evaluate-reading
    docker compose exec backend python -m app.cli reprocess-synthetic

Checks (Steps 9-10; the AI reviewer runs when AI_PROVIDER is set and AI_CHECKS is not false):
    docker compose exec backend python -m app.cli load-synthetic-reference      # synthetic benchmarks + registries
    docker compose exec backend python -m app.cli import-registries --folder reference_data/raw/registries \
        --version 2026-10 --source-name "Insurer extract 21 Oct 2026"
    docker compose exec backend python -m app.cli run-checks --synthetic        # or --claim CG-SYN-0001
    docker compose exec backend python -m app.cli run-checks --synthetic --no-ai --limit 50   # rules only
    docker compose exec backend python -m app.cli evaluate-checks --misses 3
    docker compose exec backend python -m app.cli rescore          # risk score / band from stored findings

AI model (after setting AI_PROVIDER / AI_MODEL in .env):
    docker compose exec worker python -m app.cli ai-check
    docker compose exec backend python -m app.cli evaluate-extraction
"""

import argparse
import getpass
import re
import sys
from collections import Counter
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
    rows = list(csv.DictReader(open(manifest)))
    if scanned:  # only claims that have a scanned copy; stored as e.g. CG-SYN-0001-S next to the clean one
        rows = [r for r in rows if r.get("scanned") == "True"]
    rows = rows[: limit or None]
    created = skipped = 0
    with SessionLocal() as db:
        for row in rows:
            claim_id = row["claim_id"]
            number = f"{claim_id}-S" if scanned else claim_id
            if db.scalar(select(Claim.id).where(Claim.claim_number == number)):
                skipped += 1
                continue
            claim_dir = folder / "claims" / claim_id
            truth = json.loads((claim_dir / "ground_truth.json").read_text())
            pdf = claim_dir / ("scanned.pdf" if scanned else "claim.pdf")
            result = intake.create_claim(
                db, [intake.IncomingFile(f"{number}.pdf", pdf.read_bytes())], stage=ClaimStage(truth["stage"]),
                claim_number=number, source="synthetic",
            )
            log_event(db, "claim.created", entity_type="claim", entity_id=result.claim.id,
                      details={"claim_number": number, "via": "cli import-synthetic", "file": pdf.name})
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


def reprocess_synthetic(scanned_only: bool, clean_only: bool, limit: int | None = None) -> None:
    """Queue every synthetic claim for processing again (e.g. after a new pipeline step was added)."""
    from sqlalchemy import select

    from app.models.claim import Claim
    from app.models.enums import ClaimStatus
    from app.services import intake

    queued = 0
    with SessionLocal() as db:
        waiting = [ClaimStatus.RECEIVED, ClaimStatus.READY_FOR_REVIEW, ClaimStatus.FAILED]
        claims = db.scalars(select(Claim).where(Claim.source == "synthetic", Claim.status.in_(waiting))
                            .order_by(Claim.claim_number)).all()
        for claim in claims:
            is_scan = claim.claim_number.endswith("-S")
            if (scanned_only and not is_scan) or (clean_only and is_scan):
                continue
            if limit is not None and queued >= limit:
                break
            claim.status = ClaimStatus.RECEIVED
            claim.processing_error = None
            log_event(db, "claim.reprocess", entity_type="claim", entity_id=claim.id,
                      details={"via": "cli reprocess-synthetic"})
            queued += 1
        db.commit()
        for claim in claims:
            if claim.status == ClaimStatus.RECEIVED:
                intake.enqueue_processing(claim.id)
    print(f"Done: {queued} synthetic claims queued for processing.")


def ai_check() -> None:
    """Send one small test request to the configured AI model and report what happened."""
    import io
    import time

    from PIL import Image, ImageDraw, ImageFont

    from app.ai.client import AIError, get_provider, image_block, text_block
    from app.core.config import get_settings

    s = get_settings()
    provider = get_provider()
    if provider is None:
        print(f"AI is off (AI_PROVIDER={s.ai_provider!r}, AI_EXTRACTION={s.ai_extraction!r}"
              f"{', no AI_API_KEY' if s.ai_provider.strip().lower() == 'anthropic' and not s.ai_api_key else ''}).")
        sys.exit(1)
    print(f"Provider {provider.name}, model {provider.model}. Sending a test page (the first call loads the model)...")
    img = Image.new("L", (900, 300), 255)
    ImageDraw.Draw(img).text((40, 100), "BILL NO: IPB/2026/4417", fill=0, font=ImageFont.load_default(size=56))
    png = io.BytesIO()
    img.save(png, "PNG")
    started = time.monotonic()
    try:
        result = provider.structured(
            system="Copy values exactly as printed on the image.",
            content=[text_block("Read the bill number printed on this image."), text_block("Page 1 image:"),
                     image_block(png.getvalue())],
            tool_name="record", tool_description="Record the bill number.",
            schema={"type": "object", "properties": {"bill_no": {"type": ["string", "null"]}},
                    "required": ["bill_no"]}, max_tokens=200)
    except AIError as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)
    seconds = time.monotonic() - started
    answer = result.data.get("bill_no")
    ok = str(answer or "").replace(" ", "").upper() == "IPB/2026/4417"
    print(f"Answer: {answer!r} in {seconds:.1f}s ({result.input_tokens} tokens in, {result.output_tokens} out)")
    print("OK: the model can read page images." if ok else
          "Answered, but not the expected 'IPB/2026/4417'. Text-only model? Set AI_VISION=false or use a vision model.")


def import_registries_cmd(folder: Path, version: str, source_name: str) -> None:
    from app.services.reference.registries import RegistryImportError, import_registries

    with SessionLocal() as db:
        try:
            rec, counts = import_registries(db, folder, source_name=source_name, version=version)
        except (RegistryImportError, FileNotFoundError) as exc:
            db.rollback()
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        _report(db, [rec])
        db.commit()
    shown = {k: v for k, v in vars(counts).items() if k != "files" and v is not None}
    print("Done: " + ", ".join(f"{v} {k}" for k, v in shown.items()) + ".")


SYNTHETIC_FROM = date(2025, 1, 1)


def load_synthetic_reference(folder: Path) -> None:
    """Synthetic package / tariff benchmarks and registries written by the generator (codes start SYN-)."""
    ref, reg = folder / "reference", folder / "registries"
    if not (ref / "packages.csv").exists():
        print(f"Error: {ref / 'packages.csv'} not found. Re-run: python -m app.synthetic", file=sys.stderr)
        sys.exit(1)
    info = importers.SourceInfo(version="SYN-1", source_name="Synthetic benchmark (ClaimGuard generator)",
                                source_url=None, effective_from=SYNTHETIC_FROM,
                                notes="Synthetic test values, not official rates")
    _run_import(lambda db: [importers.import_packages(db, ref / "packages.csv", info),
                            importers.import_tariffs(db, ref / "tariffs.csv", TariffSource.CLIENT, info)])
    import_registries_cmd(reg, "SYN-1", "Synthetic registries (ClaimGuard generator)")


def run_checks_cmd(claim_number: str | None, synthetic: bool, use_ai: bool = True, limit: int | None = None) -> None:
    from sqlalchemy import select

    from app.models.claim import Claim
    from app.models.enums import ClaimStatus
    from app.rules.reference import Reference
    from app.rules.store import run_for_claim

    with SessionLocal() as db:
        stmt = select(Claim).where(Claim.status.in_([ClaimStatus.READY_FOR_REVIEW, ClaimStatus.IN_REVIEW]))
        if claim_number:
            stmt = select(Claim).where(Claim.claim_number == claim_number)
        elif synthetic:
            stmt = stmt.where(Claim.source == "synthetic")
        claims = db.scalars(stmt.order_by(Claim.claim_number)).all()
        if limit:
            claims = claims[:limit]
        if not claims:
            print("No matching claims ready for review.")
            return
        from app.ai.client import get_check_provider

        provider = get_check_provider() if use_ai else None
        if provider is not None:
            print(f"AI reviewer: {provider.name} {provider.model} (cached answers are reused; a local model takes "
                  "about a minute per claim the first time)", flush=True)
        else:
            print("AI reviewer off: the AI checks will report pending_ai.", flush=True)
        ref = Reference.from_db(db)       # load reference data once for all claims
        totals: Counter = Counter()
        every = 1 if provider is not None else 25
        for n, claim in enumerate(claims, 1):
            counts = run_for_claim(db, claim, ref, use_ai=use_ai)
            log_event(db, "claim.checks_run", entity_type="claim", entity_id=claim.id,
                      details={**counts, "via": "cli run-checks"})
            db.commit()
            totals.update(counts)
            if n % every == 0:
                print(f"  {n}/{len(claims)} {claim.claim_number}: {counts['findings']} finding(s)"
                      + (" (AI unavailable)" if counts.get("ai_unavailable") else ""), flush=True)
    print(f"Done: {len(claims)} claim(s) checked, {totals['findings']} findings "
          f"({totals['red']} red, {totals['amber']} amber, {totals['info']} info). "
          f"AI checks still pending: {totals['pending_ai']}.")


def rescore_cmd() -> None:
    """Recompute risk score, band and suggested payable from the stored findings (no checks are run)."""
    from sqlalchemy import select

    from app.models.claim import Claim
    from app.models.rules import CheckRun
    from app.review.scoring import score_claim

    with SessionLocal() as db:
        checked = select(CheckRun.claim_id).distinct()
        claims = db.scalars(select(Claim).where(Claim.id.in_(checked))).all()
        bands: Counter = Counter()
        for claim in claims:
            bands[score_claim(db, claim).band] += 1
        db.commit()
    print(f"Scored {len(claims)} claim(s): {bands['red']} red, {bands['amber']} amber, {bands['green']} green.")


def tariff_match_report_cmd(out: Path, limit: int | None) -> None:
    """Every distinct bill wording on the claims in review and the tariff item B-03 compares it with."""
    from sqlalchemy import select

    from app.models.claim import Claim
    from app.models.enums import ClaimStatus
    from app.rules.reference import Reference
    from app.rules.store import load_context, sync_rules
    from app.rules.tariff_report import collect, write_csv

    with SessionLocal() as db:
        claims = db.scalars(select(Claim).where(Claim.status.in_([ClaimStatus.READY_FOR_REVIEW,
                                                                   ClaimStatus.IN_REVIEW]))
                            .order_by(Claim.claim_number)).all()
        if limit:
            claims = claims[:limit]
        ref = Reference.from_db(db)
        if not ref.tariffs:
            print("No tariffs are loaded: import CGHS (or a client tariff) first.")
            return
        configs = {rid: view.config for rid, view in sync_rules(db).items()}

        def contexts():
            for claim in claims:
                ctx = load_context(db, claim, ref, read_file=lambda _key: b"")
                ctx.config.update(configs)
                yield ctx

        rows = collect(contexts())
        db.rollback()
    write_csv(rows, out)
    matched = sum(1 for r in rows if r.code)
    confirmed = sum(1 for r in rows if r.how == "confirmed mapping")
    print(f"{len(rows)} distinct bill wording(s) on {len(claims)} claim(s): {matched} matched to a tariff item "
          f"({confirmed} by a confirmed link), {len(rows) - matched} not matched.")
    print(f"Wrote {out}. Correct the Tariff code column (blank it to leave a wording unmatched), then import it on "
          "Reference data as 'Bill wording links'.")


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
    syn.add_argument("--scanned", action="store_true",
                     help="Import the scanned copies instead (as CG-SYN-xxxx-S), for testing OCR")
    syn.add_argument("--now", action="store_true", help="Process here instead of in the worker")
    ev = sub.add_parser("evaluate-reading", help="Score page types and OCR on imported synthetic claims")
    ev.add_argument("--folder", type=Path, default=Path(__file__).resolve().parents[1] / "synthetic_data")
    ex = sub.add_parser("evaluate-extraction", help="Score the claim record against the synthetic answers")
    ex.add_argument("--folder", type=Path, default=Path(__file__).resolve().parents[1] / "synthetic_data")
    ex.add_argument("--misses", type=int, default=0, help="Show up to N wrong values per weak field")
    sub.add_parser("ai-check", help="Send one test request to the configured AI model")
    rp = sub.add_parser("reprocess-synthetic", help="Queue imported synthetic claims for processing again")
    rp.add_argument("--scanned", action="store_true", help="Only the scanned copies (CG-SYN-xxxx-S)")
    rp.add_argument("--clean", action="store_true", help="Only the clean copies")
    rp.add_argument("--limit", type=int, help="Only the first N (by claim number)")
    ir = sub.add_parser("import-registries", help="Load doctor / hospital / watchlist / policy registries (CSV)")
    ir.add_argument("--folder", type=Path, required=True, help="Folder with doctors.csv, hospitals.csv, ...")
    ir.add_argument("--version", required=True)
    ir.add_argument("--source-name", required=True)
    ls = sub.add_parser("load-synthetic-reference", help="Load the synthetic set's benchmarks and registries")
    ls.add_argument("--folder", type=Path, default=Path(__file__).resolve().parents[1] / "synthetic_data")
    sub.add_parser("rescore", help="Recompute risk scores and bands from stored findings")
    rc = sub.add_parser("run-checks", help="Run the checks again on claims that are ready for review")
    rc.add_argument("--claim", help="One claim number")
    rc.add_argument("--synthetic", action="store_true", help="All synthetic claims")
    rc.add_argument("--no-ai", action="store_true", help="Rules only: skip the AI reviewer")
    rc.add_argument("--limit", type=int, help="Only the first N claims (by claim number)")
    ec = sub.add_parser("evaluate-checks", help="Score the checks against the synthetic answers")
    ec.add_argument("--folder", type=Path, default=Path(__file__).resolve().parents[1] / "synthetic_data")
    ec.add_argument("--misses", type=int, default=0, help="Show up to N examples of missed and extra flags")
    tm = sub.add_parser("tariff-match-report",
                        help="CSV of bill wordings and the tariff item each is compared with, to confirm")
    tm.add_argument("--out", type=Path, default=Path("tariff_matches.csv"))
    tm.add_argument("--limit", type=int, help="Only the first N claims (by claim number)")
    mp = sub.add_parser("import-tariff-mappings", help="Import confirmed bill wording -> tariff code links (CSV)")
    _add_source_args(mp)
    args = parser.parse_args(argv)

    if args.command == "load-reference-defaults":
        _run_import(lambda db: importers.load_defaults(db, include_icd=not args.skip_icd))
    elif args.command == "import-packages":
        _run_import(lambda db: [importers.import_packages(db, args.file, _source_info(args), sheet=args.sheet)])
    elif args.command == "import-tariffs":
        _run_import(lambda db: [importers.import_tariffs(
            db, args.file, TariffSource(args.source), _source_info(args), sheet=args.sheet,
            default_tier=CityTier(args.city_tier), default_ward=WardType(args.ward))])
    elif args.command == "import-tariff-mappings":
        _run_import(lambda db: [importers.import_tariff_mappings(db, args.file, _source_info(args), sheet=args.sheet)])
    elif args.command == "tariff-match-report":
        tariff_match_report_cmd(args.out, args.limit)
    elif args.command == "reference-status":
        reference_status()
    elif args.command == "import-synthetic":
        import_synthetic(args.folder, args.limit, args.scanned, args.now)
    elif args.command == "evaluate-reading":
        from app.pipeline.evaluate import evaluate, report

        with SessionLocal() as db:
            print(report(evaluate(db, args.folder)))
    elif args.command == "evaluate-extraction":
        from sqlalchemy import func, select

        from app.extraction.evaluate import evaluate_db
        from app.extraction.evaluate import report as extraction_report
        from app.models.extraction import AiCall

        with SessionLocal() as db:
            for label, tally in evaluate_db(db, args.folder).items():
                n = tally.claims
                print(extraction_report(f"{label} ({n} claims)", tally) if n else f"{label}: no claims")
                if args.misses:
                    shown: Counter = Counter()
                    for name, detail in tally.misses:
                        if shown[name] < args.misses:
                            shown[name] += 1
                            if isinstance(detail, tuple) and len(detail) == 3:
                                doc_type, want, got = detail
                                print(f"      {name} ({doc_type}): expected {want!r} | got {got!r}")
                            else:
                                want, got = detail
                                print(f"      {name}: expected {want!r}\n         got {got!r}")
                if tally.not_extracted:
                    print(f"   {tally.not_extracted} {label} claims not extracted yet (run reprocess-synthetic)")
            calls, cost, failed = db.execute(select(func.count(), func.coalesce(func.sum(AiCall.cost_usd), 0),
                                                    func.count().filter(AiCall.status == "error"))).one()
            print(f"AI calls so far: {calls} ({failed} failed), estimated cost USD {float(cost):.4f}")
            messages = db.scalars(select(AiCall.error).where(AiCall.status == "error")).all()
            grouped = Counter(re.sub(r"'request_id': '[^']*'", "", m or "")[:300] for m in messages)
            for error, count in grouped.most_common(3):
                print(f"   {count}x error: {error}")
            took = db.execute(select(AiCall.provider, func.count(), func.avg(AiCall.duration_ms))
                              .where(AiCall.status == "ok").group_by(AiCall.provider)).all()
            for provider_name, count, avg_ms in took:
                print(f"   {provider_name}: {count} successful calls, average {float(avg_ms) / 1000:.1f}s each")
    elif args.command == "ai-check":
        ai_check()
    elif args.command == "reprocess-synthetic":
        reprocess_synthetic(args.scanned, args.clean, args.limit)
    elif args.command == "import-registries":
        import_registries_cmd(args.folder, args.version, args.source_name)
    elif args.command == "load-synthetic-reference":
        load_synthetic_reference(args.folder)
    elif args.command == "rescore":
        rescore_cmd()
    elif args.command == "run-checks":
        if not args.claim and not args.synthetic:
            parser.error("give --claim CG-... or --synthetic")
        run_checks_cmd(args.claim, args.synthetic, use_ai=not args.no_ai, limit=args.limit)
    elif args.command == "evaluate-checks":
        from app.rules.evaluate import evaluate as evaluate_checks

        with SessionLocal() as db:
            for label, score in evaluate_checks(db, args.folder).items():
                print(score.report(label, args.misses) if score.claims else f"{label}: no claims")

    if args.command == "create-admin":
        password = sys.stdin.readline().rstrip("\n") if args.password_stdin else _ask_password()
        try:
            create_admin(args.email, args.name, password)
        except UserError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
