"""Generate the synthetic claim set.

Output (default backend/synthetic_data/):
    claims/<claim id>/claim.pdf          the clean claim file
    claims/<claim id>/scanned.pdf        a degraded scan of it (for a share of claims)
    claims/<claim id>/ground_truth.json  seeded issues, expected band, evidence pages
    claims/<claim id>/record.json        every value each document prints (extraction target)
    registries/*.csv                     doctor and hospital registers, watchlist, insurer policy master
    manifest.csv, coverage.json          one row per claim; seeded cases per check

Same seed, same output.
"""

import csv
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from random import Random

from app.rules.catalogue import CHECKS, expected_band
from app.synthetic.case import (
    ICU_RATE,
    QUALIFICATION,
    ROOM_RATE,
    TIER_FACTOR,
    Case,
    Doctor,
    Hospital,
    build_genuine,
    make_doctor,
    make_hospital,
)
from app.synthetic.render import render_case
from app.synthetic.scan import scan_pdf
from app.synthetic.scenarios import NORMAL_CONSUMABLES, SCENARIOS
from app.synthetic.seeders import SEEDERS, Context, Seeder

GENERATOR_VERSION = "1.0"


@dataclass
class Options:
    out_dir: Path
    count: int = 300
    seed: int = 42
    genuine_share: float = 0.4
    second_issue_share: float = 0.25
    scanned_share: float = 0.3
    hospitals: int = 24


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Not serialisable: {type(value)}")


class Generator:
    def __init__(self, opts: Options):
        self.opts = opts
        self.rng = Random(opts.seed)
        self.ctx = Context()
        self.hospitals: list[Hospital] = [make_hospital(self.rng) for _ in range(opts.hospitals)]
        self.doctors: dict[tuple[str, str], Doctor] = {}
        self.cases: list[Case] = []

    def doctor_for(self, hospital: Hospital, specialty: str, rng: Random) -> Doctor:
        key = (hospital.rohini_id, specialty)
        if key not in self.doctors:
            self.doctors[key] = make_doctor(rng, QUALIFICATION[specialty])
        return self.doctors[key]

    # ---------------------------------------------------------------- planning

    def plan(self) -> list[list[str]]:
        """Check IDs to seed into each claim (empty list = genuine)."""
        n = self.opts.count
        seeded = n - round(n * self.opts.genuine_share)
        order = list(CHECKS)
        plans: list[list[str]] = []
        combinable = [c for c, s in SEEDERS.items() if s.combinable]
        for i in range(seeded):
            primary = order[i % len(order)]
            checks = [primary]
            if self.rng.random() < self.opts.second_issue_share:
                options = [c for c in combinable if c != primary and c[0] != primary[0]]
                checks.append(self.rng.choice(options))
            plans.append(checks)
        plans += [[] for _ in range(n - seeded)]
        self.rng.shuffle(plans)
        return plans

    # ---------------------------------------------------------------- one claim

    def build(self, index: int, checks: list[str]) -> Case:
        rng = Random(self.opts.seed * 100003 + index)
        seeders: list[Seeder] = [SEEDERS[c] for c in checks]
        fitting = [s for s in SCENARIOS.values() if all(sd.fits(s) for sd in seeders)]
        if not fitting:  # the second issue doesn't fit any scenario the first needs: drop it
            seeders = seeders[:1]
            fitting = [s for s in SCENARIOS.values() if seeders[0].fits(s)]
        scenario = rng.choice(fitting)
        stage = next((s.stage for s in seeders if s.stage), None)
        hospital = rng.choice(self.hospitals)
        doctor = self.doctor_for(hospital, scenario.specialty, rng)
        case = build_genuine(f"CG-SYN-{index + 1:04d}", scenario, rng, hospital, doctor, stage=stage)

        # Second issues first, then the main one, and a claim-amount change (B-02) always last,
        # because other seeders recompute the claimed amount from the bill.
        ordered = seeders[1:] + seeders[:1]
        ordered.sort(key=lambda s: s.check_id == "B-02")
        for s in ordered:
            case.issues.append(s.apply(case, rng, self.ctx))

        # An unrelated seeded issue can push a cataract claim over its sub-limit; raise the limit so that only
        # claims seeded with P-03 break it.
        limit = case.policy.sub_limits.get("cataract_per_eye")
        if case.scenario.key == "cataract" and "P-03" not in checks and limit and case.claimed_amount > limit:
            case.policy.sub_limits["cataract_per_eye"] = int(-(-case.claimed_amount // 10000) * 10000)
        return case

    # ---------------------------------------------------------------- output

    def run(self, progress=None) -> dict:
        out = self.opts.out_dir
        (out / "claims").mkdir(parents=True, exist_ok=True)
        plans = self.plan()
        manifest = []
        coverage: Counter = Counter()
        scan_rng = Random(self.opts.seed + 7)
        for i, checks in enumerate(plans):
            case = self.build(i, checks)
            self.cases.append(case)
            rendered = render_case(case)
            folder = out / "claims" / case.claim_id
            folder.mkdir(exist_ok=True)
            (folder / "claim.pdf").write_bytes(rendered.pdf)
            scanned = scan_rng.random() < self.opts.scanned_share
            if scanned:
                (folder / "scanned.pdf").write_bytes(scan_pdf(rendered.pdf, scan_rng))
            elif (folder / "scanned.pdf").exists():
                (folder / "scanned.pdf").unlink()

            truth = self.ground_truth(case, rendered.pages, scanned)
            (folder / "ground_truth.json").write_text(json.dumps(truth, indent=2, default=_json_default))
            record = {"claim_id": case.claim_id, "documents": rendered.fields}
            (folder / "record.json").write_text(json.dumps(record, indent=2, default=_json_default))
            for issue in case.issues:
                coverage[issue.check_id] += 1
            manifest.append({
                "claim_id": case.claim_id, "scenario": case.scenario.key, "stage": case.stage,
                "genuine": not case.issues, "checks": " ".join(x.check_id for x in case.issues),
                "expected_band": truth["expected_band"], "claimed_amount": case.claimed_amount,
                "pages": rendered.pages[-1]["last_page"], "scanned": scanned,
            })
            if progress:
                progress(i + 1, len(plans))

        self.write_manifest(manifest)
        self.write_registries()
        write_reference(self.opts.out_dir / "reference")
        missing = [c for c in CHECKS if coverage[c] == 0]
        summary = {
            "generator_version": GENERATOR_VERSION, "seed": self.opts.seed, "claims": len(manifest),
            "genuine": sum(1 for m in manifest if m["genuine"]), "scanned": sum(1 for m in manifest if m["scanned"]),
            "seeded_per_check": dict(sorted(coverage.items())), "checks_without_cases": missing,
            "bands": dict(Counter(m["expected_band"] for m in manifest)),
            "scenarios": dict(Counter(m["scenario"] for m in manifest)),
        }
        (out / "coverage.json").write_text(json.dumps(summary, indent=2))
        return summary

    def ground_truth(self, case: Case, pages: list[dict], scanned: bool) -> dict:
        def pages_of(types: list[str]) -> list[int]:
            found = []
            for p in pages:
                if p["type"] in types:
                    found += list(range(p["first_page"], p["last_page"] + 1))
            return found

        issues = []
        for issue in case.issues:
            item = asdict(issue)
            item["severity"] = CHECKS[issue.check_id].max_severity
            item["pages"] = pages_of(issue.documents)
            issues.append(item)
        fired = [i.check_id for i in case.issues] + [a for i in case.issues for a in i.also]
        return {
            "claim_id": case.claim_id, "generator_version": GENERATOR_VERSION, "seed": self.opts.seed,
            "scenario": case.scenario.key, "package_code": case.scenario.package_code, "stage": case.stage,
            "genuine": not case.issues, "expected_band": expected_band(fired),
            "expected_checks": sorted(set(fired)), "issues": issues,
            "claimed_amount": case.claimed_amount, "bill_total": case.bill_total,
            "documents": pages, "files": {"clean": "claim.pdf", "scanned": "scanned.pdf" if scanned else None},
        }

    def write_manifest(self, rows: list[dict]) -> None:
        with open(self.opts.out_dir / "manifest.csv", "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def write_registries(self) -> None:
        folder = self.opts.out_dir / "registries"
        folder.mkdir(exist_ok=True)
        hospitals = {h.rohini_id: h for h in self.hospitals}
        hospitals.update({c.hospital.rohini_id: c.hospital for c in self.cases})
        doctors = {d.reg_no: d for d in self.doctors.values()}
        doctors.update({c.doctor.reg_no: c.doctor for c in self.cases})

        with open(folder / "hospitals.csv", "w", newline="") as handle:
            w = csv.writer(handle)
            w.writerow(["rohini_id", "hfr_id", "name", "address", "city", "state", "city_tier", "nabh"])
            for h in hospitals.values():
                if h.rohini_id not in self.ctx.unregistered_hospitals:
                    w.writerow([h.rohini_id, h.hfr_id, h.name, h.address, h.city, h.state, h.tier, h.nabh])
        with open(folder / "doctors.csv", "w", newline="") as handle:
            w = csv.writer(handle)
            w.writerow(["registration_no", "name", "qualification", "council"])
            for d in doctors.values():
                if d.reg_no not in self.ctx.unregistered_doctors:
                    w.writerow([d.reg_no, d.name, d.qualification, d.council])
        with open(folder / "watchlist.csv", "w", newline="") as handle:
            w = csv.writer(handle)
            w.writerow(["rohini_id", "name", "reason"])
            for rohini, reason in self.ctx.watchlist.items():
                w.writerow([rohini, hospitals[rohini].name, reason])
        with open(folder / "policies.csv", "w", newline="") as handle:
            w = csv.writer(handle)
            w.writerow(["policy_no", "member_id", "member_name", "product_code", "sum_insured", "used_si",
                        "policy_start", "policy_end", "first_inception", "room_eligibility", "room_rent_cap_pct",
                        "sub_limits", "declared_peds"])
            for c in self.cases:
                p = c.policy
                w.writerow([p.policy_no, p.member_id, c.patient.name, p.product_code, p.sum_insured, p.used_si,
                            p.start, p.end, p.inception, p.room_eligibility, p.room_rent_cap_pct or "",
                            json.dumps(p.sub_limits), json.dumps(p.declared_peds)])


# What a package includes, as bill categories (app.models.enums.BillCategory). Investigations, medicines and
# implants are billed on top of the package rate.
SURGICAL_INCLUSIONS = ["room", "nursing", "consultation", "surgeon_fee", "anaesthesia", "ot_charges", "consumables"]
MEDICAL_INCLUSIONS = ["room", "nursing", "consultation", "consumables"]


def write_reference(folder: Path) -> None:
    """Synthetic benchmarks in the same CSV format as the official files (load with import-packages and
    import-tariffs). They let the benchmark checks run end-to-end on synthetic claims; the official PM-JAY and
    CGHS files replace them for real claims. Every code starts with SYN- so they can never be mistaken for real.
    """
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "packages.csv", "w", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["code", "name", "specialty", "rate", "expected_los_days", "is_day_care", "mandatory_documents",
                    "inclusions", "keywords"])
        for s in SCENARIOS.values():
            words = [s.icd_code.split(".")[0][:3], s.diagnosis.lower()] + ([s.procedure.lower()] if s.procedure else [])
            w.writerow([s.package_code, s.procedure or s.name, s.specialty, s.package_rate, s.los_norm, s.day_care,
                        "", "|".join(SURGICAL_INCLUSIONS if s.surgical else MEDICAL_INCLUSIONS), "|".join(words)])
    with open(folder / "tariffs.csv", "w", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["code", "name", "category", "specialty", "rate", "city_tier", "ward_type"])
        tests: dict[str, float] = {}
        for s in SCENARIOS.values():
            for t in s.tests:
                tests[t.name] = t.rate
        for tier, factor in TIER_FACTOR.items():
            def row(code: str, name: str, category: str, rate: float, ward: str = "any", specialty: str = "") -> None:
                w.writerow([code, name, category, specialty, round(rate * factor), tier, ward])

            for name, rate in sorted(tests.items()):
                slug = "".join(ch for ch in name.upper() if ch.isalnum())[:20]
                row(f"SYN-T-{slug}", name, "Investigation", rate)
            for s in SCENARIOS.values():
                if s.surgical and s.procedure:
                    code = s.package_code.removeprefix("SYN-")
                    row(f"SYN-SF-{code}", f"Surgeon fee - {s.procedure}", "Professional fee", s.package_rate * 0.35,
                        specialty=s.specialty)
                    row(f"SYN-AF-{code}", f"Anaesthetist fee - {s.procedure}", "Professional fee",
                        s.package_rate * 0.12, specialty=s.specialty)
                    row(f"SYN-OT-{code}", f"Operation theatre charges - {s.procedure}", "OT charges",
                        s.package_rate * 0.15, specialty=s.specialty)
            for ward, rate in ROOM_RATE.items():
                row(f"SYN-RM-{ward.upper()}", f"Room rent - {ward.replace('_', '-')} ward", "Room", rate, ward)
            row("SYN-ICU", "ICU charges", "Room", ICU_RATE)
            row("SYN-NURSING", "Nursing charges", "Nursing", 400)
            row("SYN-VISIT", "Consultant visit charges", "Consultation", 700)
            for name, _per_day, rate in NORMAL_CONSUMABLES:
                slug = "".join(ch for ch in name.upper() if ch.isalnum())[:20]
                row(f"SYN-C-{slug}", name, "Consumables", rate)


def generate(opts: Options, progress=None) -> dict:
    return Generator(opts).run(progress)
