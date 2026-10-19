"""Step 5: the synthetic claim generator.

An independent fact checker below re-derives the deterministic checks from each
claim's facts. Genuine claims must be clean; every seeded claim must break the
check it was seeded for. This is what makes the ground truth trustworthy.
"""

import csv
import json
from datetime import timedelta
from random import Random

import pytest
from pypdf import PdfReader

from app.rules.catalogue import CHECKS, expected_band
from app.synthetic.case import TIER_FACTOR, Case
from app.synthetic.generate import Generator, Options, generate
from app.synthetic.scenarios import SCENARIOS
from app.synthetic.seeders import SEEDERS, Context

LIST_ONE = ("attendant", "diaper", "crepe", "mineral water", "thermometer")
SUBSUMED = ("admission kit", "registration", "documentation", "housekeeping")


def violations(case: Case, ctx: Context, registered_doctors: set[str]) -> set[str]:
    """Deterministic checks computed straight from the facts (not from the seeders)."""
    found: set[str] = set()
    types = [d.type for d in case.documents]
    pol, s = case.policy, case.scenario
    adm, dis = case.admission, case.discharge

    if "id_proof" not in types or "policy_ecard" not in types:
        found.add("C-01")
    if not case.signed_summary:
        found.add("C-02")
    reported = {r.test for r in case.reports}
    if any(x.category == "Investigations" and x.description not in reported for x in case.bill):
        found.add("C-03")
    if case.implants and not {"implant_invoice", "implant_sticker"} <= set(types):
        found.add("C-04")
    if s.accident and "mlc" not in types:
        found.add("C-05")
    if case.pdf_meta.get("modified") and case.pdf_meta["modified"] > case.claim_submitted:
        found.add("F-01")
    if any(x.font for x in case.bill):
        found.add("F-02")
    if any(d.overrides.get("patient_name") for d in case.documents):
        found.add("X-01")
    if case.ot and not (adm <= case.ot.start <= dis):
        found.add("X-02")
    if any(not (adm - timedelta(days=30) <= r.report_dt <= dis) for r in case.reports):
        found.add("X-03")
    charted = {m for n in case.nursing for m in n.meds}
    if any(p.drug not in charted for p in case.pharmacy):
        found.add("X-04")
    icu_billed = sum(x.qty for x in case.bill if x.category == "ICU")
    icu_charted = sum(1 for n in case.nursing if n.location == "ICU")
    if icu_billed != icu_charted:
        found.add("X-05")
    if case.ot and (case.procedures != case.ot.procedures or case.discharge_procedures != case.ot.procedures):
        found.add("X-06")
    if case.implants and case.sticker_model != case.implants[0].model:
        found.add("X-07")
    if case.preauth and case.preauth.diagnosis != case.final_diagnosis:
        found.add("X-08")
    if any(d.overrides.get("rohini_id") for d in case.documents):
        found.add("X-09")
    if s.los_norm and case.los_days > s.los_norm:
        found.add("M-05")
    if s.day_care and case.los_days > 0:
        found.add("M-07")
    if any(round(x.qty * x.rate, 2) != x.amount for x in case.bill):
        found.add("B-01")
    if round(case.claimed_amount, 2) != case.bill_total:
        found.add("B-02")
    if any(x.category == "Package" for x in case.bill) and any(x.category == "OT Charges" for x in case.bill):
        found.add("B-04")
    if any(any(k in x.description.lower() for k in LIST_ONE) for x in case.bill):
        found.add("B-05")
    if any(any(k in x.description.lower() for k in SUBSUMED) for x in case.bill):
        found.add("B-06")
    for x in case.bill:
        if x.description.startswith("Disposable Syringe") and x.qty / max(1, icu_charted + case.los_days) > 6:
            found.add("B-07")
    room = next((x for x in case.bill if x.category == "Room Rent"), None)
    if room and "private room" in room.description:
        found.add("B-08")
    if len([x for x in case.bill if x.description.startswith("Surgeon fee")]) > 1:
        found.add("B-09")
    if any(p.rate > p.mrp for p in case.pharmacy):
        found.add("B-10")
    if not (pol.start <= adm.date() <= pol.end):
        found.add("P-01")
    if s.specific_waiting and (adm.date() - pol.inception).days < 730:
        found.add("P-02")
    limit = pol.sub_limits.get("cataract_per_eye")
    if (s.key == "cataract" and limit and case.claimed_amount > limit) or \
            (s.key == "normal_delivery" and "maternity" not in pol.sub_limits):
        found.add("P-03")
    if (adm.date() - pol.inception).days < 90:
        found.add("P-04")
    if case.claimed_amount > pol.sum_insured - pol.used_si:
        found.add("P-06")
    if case.doctor.reg_no not in registered_doctors:
        found.add("R-01")
    if case.hospital.rohini_id in ctx.unregistered_hospitals:
        found.add("R-02")
    if case.hospital.rohini_id in ctx.watchlist:
        found.add("R-03")
    if pol.room_rent_cap_pct and room and room.rate > pol.sum_insured * pol.room_rent_cap_pct / 100 \
            and "private room" not in room.description:
        found.add("ROOM-CAP")  # never expected: genuine rooms stay within the cap
    return found


DETERMINISTIC = {"C-01", "C-02", "C-03", "C-04", "C-05", "F-01", "F-02", "X-01", "X-02", "X-03", "X-04", "X-05",
                 "X-06", "X-07", "X-08", "X-09", "M-05", "M-07", "B-01", "B-02", "B-04", "B-05", "B-06", "B-07",
                 "B-08", "B-09", "B-10", "P-01", "P-02", "P-03", "P-04", "P-06", "R-01", "R-02", "R-03"}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    gen = Generator(Options(out_dir=tmp_path_factory.mktemp("unused"), count=300, seed=7))
    cases = [gen.build(i, checks) for i, checks in enumerate(gen.plan())]
    registered = {d.reg_no for d in gen.doctors.values()} - gen.ctx.unregistered_doctors
    return gen, cases, registered


def test_catalogue_and_seeders_cover_the_same_45_checks():
    assert len(CHECKS) == 45 and set(SEEDERS) == set(CHECKS)
    for check_id, seeder in SEEDERS.items():
        assert any(seeder.fits(s) for s in SCENARIOS.values()), f"{check_id} fits no scenario"


def test_plan_covers_every_check_and_keeps_genuine_share(built):
    gen, cases, _ = built
    seeded = [c for c in cases if c.issues]
    assert len(cases) - len(seeded) == 120
    counts = {c: sum(1 for x in cases for i in x.issues if i.check_id == c) for c in CHECKS}
    assert min(counts.values()) >= 4, counts
    assert {c.scenario.key for c in cases} == set(SCENARIOS)


def test_genuine_claims_are_clean(built):
    gen, cases, registered = built
    for case in cases:
        if not case.issues:
            assert violations(case, gen.ctx, registered) == set(), case.claim_id


def test_seeded_claims_break_their_check_and_nothing_unexpected(built):
    gen, cases, registered = built
    for case in cases:
        if not case.issues:
            continue
        found = violations(case, gen.ctx, registered)
        expected = {i.check_id for i in case.issues} | {a for i in case.issues for a in i.also}
        for check in expected & DETERMINISTIC:
            assert check in found, (case.claim_id, check, found)
        unexpected = (found - expected) & DETERMINISTIC
        assert not unexpected, (case.claim_id, case.scenario.key, [i.check_id for i in case.issues], unexpected)


def test_bills_stay_consistent_with_their_parts(built):
    _, cases, _ = built
    for case in cases:
        pharmacy_line = next(x for x in case.bill if x.category == "Pharmacy")
        assert pharmacy_line.amount == case.pharmacy_total
        rate_factor = TIER_FACTOR[case.hospital.tier]
        assert 0.79 <= rate_factor <= 1.0
        if case.preauth:
            assert case.stage == "final_cashless"


def test_expected_band_rules():
    assert expected_band([]) == "green"
    assert expected_band(["B-05"]) == "green"          # info only
    assert expected_band(["M-05"]) == "amber"
    assert expected_band(["B-05", "B-01"]) == "red"


def test_generate_writes_files_and_is_reproducible(tmp_path):
    opts = Options(out_dir=tmp_path / "a", count=12, seed=3, scanned_share=0.5)
    summary = generate(opts)
    assert summary["claims"] == 12
    rows = list(csv.DictReader(open(tmp_path / "a" / "manifest.csv")))
    assert len(rows) == 12
    for row in rows:
        folder = tmp_path / "a" / "claims" / row["claim_id"]
        truth = json.loads((folder / "ground_truth.json").read_text())
        record = json.loads((folder / "record.json").read_text())
        pdf = PdfReader(folder / "claim.pdf")
        assert len(pdf.pages) == int(row["pages"]) == truth["documents"][-1]["last_page"]
        assert pdf.metadata["/Title"] == f"Claim file {row['claim_id']}"
        assert [d["type"] for d in record["documents"]] == [d["type"] for d in truth["documents"]]
        for issue in truth["issues"]:
            assert all(1 <= p <= len(pdf.pages) for p in issue["pages"])
            if issue["documents"]:
                assert issue["pages"], issue
        assert (folder / "scanned.pdf").exists() == (row["scanned"] == "True")
        bill = next(d for d in record["documents"] if d["type"] == "final_bill")["fields"]
        assert bill["bill_total"] == truth["bill_total"]
    for name in ("hospitals.csv", "doctors.csv", "watchlist.csv", "policies.csv"):
        assert (tmp_path / "a" / "registries" / name).exists()

    generate(Options(out_dir=tmp_path / "b", count=12, seed=3, scanned_share=0.5))
    for row in rows:
        a = (tmp_path / "a" / "claims" / row["claim_id"] / "ground_truth.json").read_text()
        b = (tmp_path / "b" / "claims" / row["claim_id"] / "ground_truth.json").read_text()
        assert a == b


def test_scan_is_a_valid_image_pdf_with_same_page_count(tmp_path):
    from app.synthetic.scan import scan_pdf

    gen = Generator(Options(out_dir=tmp_path, count=1, seed=11))
    from app.synthetic.render import render_case

    rendered = render_case(gen.build(0, []))
    scanned = scan_pdf(rendered.pdf, Random(1))
    from io import BytesIO

    reader = PdfReader(BytesIO(scanned))
    assert len(reader.pages) == len(PdfReader(BytesIO(rendered.pdf)).pages)
    assert reader.pages[0].extract_text().strip() == ""   # pure image: OCR is needed