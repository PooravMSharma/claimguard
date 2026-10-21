# Step 9: Rule engine (brief for Claude Code)

> Built 21 Oct 2026. Notes on the build: rules are seeded from the catalogue at run time (`rules/store.py
> sync_rules`, idempotent, never overwrites tuned rows) rather than inside migration 0007; F-03 uses the
> 64-bit page hash the pipeline already stores; on scans, OCR values below 0.85 confidence are not used to
> raise findings.

Read `CLAUDE.md` first. It covers the stack, commands, conventions, guardrails and how the owner wants work
delivered. This brief is the spec for Step 9 only.

## Goal

Run the v1 checks from `backend/app/rules/catalogue.py` over each claim's structured record (built in Step 8)
and store **findings with evidence**. Every finding needs:
- severity
- a plain-English explanation
- the amount at stake
- links to the exact page and box that prove it

This step builds the deterministic checks: `rule`, `benchmark`, `lookup` and `image_hash`, plus the rule
half of the mixed checks. Checks that need the AI's judgement come in Step 10, but the engine must already
know about them and report them as "not run yet".

**Out of scope:** AI judgement checks, the risk score / band and final review screen (Step 11), and the rule
admin UI (Step 12).

## What already exists (use it, don't rebuild it)

- **`app/rules/catalogue.py`:** the 45 checks with id, layer, name, method and `max_severity`. This is the
  single source of truth for check IDs.
- **`app/models/rules.py`:**
  - `Rule`: id, name, layer, description, method, max_severity, weight, fraud_type, source_title/url,
    status, version, `config` JSONB for thresholds.
  - `Finding`: claim, rule, rule_version, severity, title, explanation, `evidence` JSONB, amount_at_stake,
    suggested_deduction, confidence, needs_medical_review, model_version.
  - `FindingReview`: confirm/dismiss history, never overwritten.
  - The tables exist, but nothing writes to them yet.
- **The claim record from Step 8:**
  - `ExtractedDocument` (per document, with `data` JSON holding fields + tables, each value with `page_id`,
    `bbox`, `confidence` and `method`).
  - `ExtractedField`, which holds reviewer corrections. **A corrected value always wins.**
  - `BillLineItem`, `Diagnosis`, `Procedure`, `Investigation` (billed / has_report), and `Medication` (mrp,
    unit_rate, in_medication_chart).
  - Summary columns on `Claim`.
- **Reference data:**
  - `Package` / `TariffRate` (PM-JAY / CGHS; the official files aren't loaded yet).
  - `NonPayableItem` (IRDAI Lists I–IV).
  - `Icd10Code`.
  - `PolicyTerm` with standard IRDAI keys: `initial_waiting_days`, `ped_waiting_months`,
    `specific_disease_waiting_months`, `moratorium_months`, `room_rent_limit`, `proportionate_deduction`,
    `standard_exclusions`, ….
  - **Read waiting periods and limits from `PolicyTerm`. Never hard-code them.**
- **Pipeline:** `app/pipeline/runner.py` `STEPS = [pages, read, extract]`.
- **Synthetic generator:**
  - `ground_truth.json` per claim, with `expected_checks` and `issues[]` (check_id, description,
    amount_at_stake, pages).
  - `coverage.json`.
  - `registries/` CSVs: `doctors.csv`, `hospitals.csv`, `policies.csv`, `watchlist.csv`.
  - Synthetic scenarios carry package codes (`SYN-…`), package rates, LOS norms, day-care flags and
    package inclusions in `app/synthetic/scenarios.py`.

## Build

### 1. Data and migration `0007_rule_engine`

- **Seed `rules` from the catalogue:**
  - status `active` for deterministic checks.
  - status `draft` for medical checks (they stay amber until clinician review; see guardrails).
  - Default weights by severity: red 30, amber 15, info 5.
  - Thresholds go in `config`, for example `{"tolerance_rs": 1}`, `{"rate_over_benchmark_pct": 25}`,
    `{"report_window_days_before_admission": 7}`.
- **New table `check_runs`** (claim_id, rule_id, rule_version, status, detail, created_at). `status` is one of:
  - `passed`
  - `flagged`
  - `not_applicable` (e.g. no implant billed, so C-04 doesn't apply)
  - `not_evaluable`: the data needed is missing or unreadable; `detail` says what
  - `pending_ai`: an AI check, done in Step 10

  This is how a reviewer sees that a check *ran and passed*, not just that it didn't fire.
- **Registry tables:** `registry_doctors`, `registry_hospitals`, `registry_watchlist` and `policy_records`
  (policy_no, member_id, member_name, product_code, sum_insured, used_si, policy_start/end, first_inception,
  room_eligibility, room_rent_cap_pct, sub_limits JSON, declared_peds JSON).
  - Add CLI `import-registries --folder <path>` that loads the four CSVs and records a `ReferenceImport` row.
  - These stand in for real NMC / HFR / insurer feeds later, so keep the import format documented.
- **Synthetic benchmarks:**
  - Have the generator also write `reference/packages.csv` and `reference/tariffs.csv` from its scenarios.
  - Codes are `SYN-…`, marked synthetic in the source name.
  - They must load through the existing `import-packages` / `import-tariffs`, so B-03 / B-04 / M-05 / M-07
    work end-to-end now. The official PM-JAY / CGHS files replace them later, with no code change.

### 2. Engine: `app/rules/engine.py` + `app/rules/checks/`

- **`ClaimContext`:**
  - Built once per claim from the DB.
  - Gives each check typed, corrected values with their evidence: `ctx.value("final_bill", "bill_total")`
    returns value + page_id + bbox + confidence.
  - Also gives lists of bill lines, medications, investigations, procedures and documents present, plus
    reference lookups (policy terms for the product, package by code, tariff by code/tier/ward,
    non-payables, registries).
- **Checks:**
  - One function per check, registered with a decorator, e.g. `@check("B-01")`.
  - Group them in files by layer: `completeness.py`, `forensics.py`, `consistency.py`, `medical.py`,
    `billing.py`, `policy.py`, `registry.py`.
  - A check returns a `CheckResult(status, findings=[...])`.
- **Each finding** carries:
  - title and explanation (plain language, with the numbers, e.g. "CT abdomen billed ₹6,500; benchmark
    ₹2,800 (+132%)")
  - `evidence`: a list of `{page_id, bbox, quote, document}` for every value used
  - `amount_at_stake` and `suggested_deduction` where money is involved
  - confidence
- **Severity:**
  - Never above the rule's `max_severity`.
  - **Downgrade red to amber when any value the finding rests on has confidence < 0.8 or came from the AI
    reader on a scan.** A misread must not create a red flag.
  - Medical-layer checks are always amber at most and set `needs_medical_review=True`.
- **Missing data:** return `not_evaluable`, not a finding. A missing document is C-01's job, not every other
  check's.
- **Isolation:** one check crashing must not stop the others. Log it, store `check_runs.status='error'` with
  the message, and carry on.
- **Pipeline step:**
  - Add `("checks", step_checks)` after `extract` in `runner.py`.
  - Reprocessing replaces findings and check runs, **but findings that have a `FindingReview` are kept**,
    matched by rule_id + a stable fingerprint (e.g. rule + evidence values). A reviewer's decision is never
    lost.
  - `Claim.status` still ends at `ready_for_review`.
- **Audit:** log `claim.checks_run` with counts.

### 3. The checks in this step

Each check below defines what it looks at and when it flags. Severity caps come from the catalogue.
The `Data used` column is the main input; the evidence links come from the same values.

| ID | Logic | Data used |
|----|-------|-----------|
| C-01 | Mandatory documents present for stage + package. Define the required set per stage in config: claim form, discharge summary, final bill, ID proof, policy copy; pre-auth for cashless; OT notes for surgical; implant invoice + sticker if implant billed. List the missing ones. | documents present |
| C-02 | *Rule part:* discharge summary has doctor registration no. and the signature flag. (Stamp / visual check comes in Step 10.) | discharge_summary.doctor_reg_no, signed |
| C-03 | Every billed investigation has a report (`Investigation.billed and not has_report`). Amount at stake = billed amount. | investigations, bill lines |
| C-04 | Implant billed → implant invoice and sticker present. | bill lines (category implant), documents |
| C-05 | Accident / MLC case (diagnosis mentions injury/fracture/RTA/fall, or the claim form says accident) → MLC/FIR present. | diagnosis, claim form, documents |
| F-01 | PDF metadata: creation/modification date after the claim's stated document date, or producer / creator naming editing software (Photoshop, ilovepdf, Canva, …). Read from the stored originals with pypdf. | original files |
| F-03 | Same page image repeated inside one claim with different patient details: perceptual hash (pHash / dHash, implement with Pillow; no new heavy dependency without asking) over page images, plus near-duplicate pairs whose patient name / UHID differ. | page images, fields |
| X-01 | Patient name (fuzzy, tolerant to initials / OCR), age (±1), gender and UHID consistent across documents. Each document that disagrees is its own evidence. | all documents |
| X-02 | Dates in order: admission ≤ procedure ≤ discharge ≤ claim submitted; test dates not after discharge. | dates |
| X-03 | Report dates inside the admission window, allowing N days before admission (config, default 7) for pre-admission tests. | report_date, admission/discharge |
| X-04 | Billed medicines appear in the nursing / medication chart (`Medication.in_medication_chart is False`). Amount at stake = those lines. | medications, nursing chart |
| X-05 | ICU days billed vs ICU days in nursing notes (location = ICU). | bill lines (ICU), nursing days |
| X-06 | *Rule part:* procedure billed vs procedure in OT notes vs discharge summary (normalised text / package code match). Semantic comparison comes in Step 10. | procedures by source document |
| X-07 | Implant billed = implant in OT notes = sticker (type / brand / model / serial). | implant rows, sticker fields |
| X-09 | Hospital name and Rohini ID the same on all documents. | per-document hospital fields |
| M-03 | *Rule part:* the same test repeated within 24 h (config) without a reason in the notes. AI judgement comes in Step 10. | investigations |
| M-05 | Length of stay vs the package's LOS norm (`Package` / synthetic scenario norm). | admission, discharge, package |
| M-07 | Day-care package billed with an overnight stay. | package day-care flag, LOS |
| B-01 | Bill arithmetic: qty × rate = amount per line (tolerance in config), category subtotals, grand total. | bill lines, bill_total |
| B-02 | Bill total = claimed amount on the claim form. | bill_total, claimed_amount |
| B-03 | Rate vs benchmark: package / procedure / test rate vs `TariffRate` / `Package` for the hospital's city tier and ward. Flag above a config %. Amount at stake = excess. | bill lines, tariffs |
| B-04 | Unbundling: items the package includes billed separately. Suggested deduction = those lines. | package inclusions, bill lines |
| B-05 | IRDAI List I non-payables billed (info). Suggested deduction = their amount. | `NonPayableItem` list I |
| B-06 | List II–IV items billed separately (amber). | `NonPayableItem` lists II–IV |
| B-07 | Consumables per day above norm (config per item / category). | bill lines, LOS |
| B-08 | Room category billed vs eligibility: compute the proportionate deduction using `PolicyTerm.proportionate_deduction` (which charge types it applies to is read from that term). | room lines, policy record |
| B-09 | Multiple procedures in one admission each billed at 100%. Apply the multiple-surgery rule from config (default 100/50/25 by descending rate). | procedures, bill lines |
| B-10 | Pharmacy above MRP: unit_rate > mrp. Amount at stake = (rate − mrp) × qty. | medications |
| P-01 | Policy active on the admission date and the patient is a covered member (policy record + member ID + name). | policy record, claim form |
| P-02 | Waiting periods: initial (days), PED and specific-disease (months) from `PolicyTerm`, against the first inception date. Use the specific-disease list from the policy term or scenario flag; PED from declared_peds vs diagnosis history. | policy, diagnosis, dates |
| P-03 | Standard exclusions (`PolicyTerm.standard_exclusions`) and sub-limits (policy sub_limits vs package, e.g. cataract per eye). | diagnosis / procedure, policy |
| P-04 | Claim within N days (config) of policy start, or after a lapse (inception ≠ start without continuity). | policy dates, admission |
| P-05 | Moratorium (info): continuous cover ≥ `moratorium_months` means non-disclosure can't be the ground. Note it so the reviewer doesn't reject on non-disclosure. | inception, admission |
| P-06 | Claimed amount ≤ remaining sum insured (sum_insured − used_si). | policy record, claimed amount |
| R-01 | Doctor's registration number exists in the registry and the name matches (fuzzy). | doctor fields, registry_doctors |
| R-02 | Hospital Rohini / HFR registered, with matching name / city. | hospital fields, registry_hospitals |
| R-03 | Hospital on the watchlist / de-empanelled list. | registry_watchlist |

**Report as `pending_ai` in this step:** F-02, F-04, X-08, M-01, M-02, M-04, M-06, B-11. C-02, X-06 and M-03
get their AI half in Step 10.

### 4. API

- `GET /api/claims/{id}/findings`: findings grouped by layer, with evidence, amounts and review history.
- `GET /api/claims/{id}/checks`: every check with its run status (passed / flagged / not_applicable /
  not_evaluable / pending_ai / error).
- `POST /api/findings/{id}/review`:
  - body `{action: confirmed|dismissed, reason}`; a reason is required when dismissing
  - audited
  - adjudicator, senior reviewer and fraud investigator roles
- `GET /api/rules`: read-only list with status, version, weight and config. Admin editing comes in Step 12.
- `POST /api/claims/{id}/run-checks`: re-run checks only, without re-reading the pages.

### 5. Frontend

On `ClaimDetail`, add a **Findings** tab next to "Claim record" and "Documents":
- Group findings by layer, red first. Each finding shows its severity chip, title, explanation and ₹ amount
  at stake / suggested deduction.
- **Show on page** for each evidence item, reusing `PageViewer`'s focus highlight from Step 8.
- Confirm / Dismiss buttons with a reason box, and the review history under each finding.
- A collapsible **Checks** list with all 45 and their run status, so "passed" is visible as well as
  "flagged".
- A "Not run yet (AI, Step 10)" group for the pending checks.

Keep the existing style (`styles.css`). No new UI library.

### 6. Evaluation: `python -m app.cli evaluate-checks [--misses N]`

Compare findings to `ground_truth.json` → `expected_checks` for every synthetic claim that is ready for review.

**Report:**
- Per-check recall (seeded issue found) and precision (flags that match a seeded issue).
- False reds on genuine claims.
- Band agreement using `catalogue.expected_band`.
- Clean and scanned reported separately.
- `--misses` prints example missed and extra flags with the values used.

**Targets** (proposed; confirm with the owner):

| Measure | Target |
|---------|--------|
| Recall on deterministic checks, clean claims | ≥ 90% |
| Precision on deterministic checks, clean claims | ≥ 85% |
| False reds on genuine clean claims | ≤ 5% |

Report the actual numbers honestly, including any check below target and why.

### 7. Tests (`tests/test_rules_*.py`)

- **Every check, both cases:** a unit test that flags and one that passes, using small hand-built
  contexts; no PDFs needed.
- **Downgrade:** a finding resting on a low-confidence or AI-read value is amber, not red.
- **Corrections:** a reviewer-corrected value is the one the checks use.
- **Reprocessing:** reviewed findings survive it.
- **Isolation:** one check raising an exception doesn't stop the rest (`error` run status).
- **End-to-end on synthetic claims:** generate a few with known seeded issues → run the pipeline → the
  expected check IDs are flagged and genuine claims have no red.
- **API:** permissions (who can review) and audit entries.
- **No real AI calls** in tests.

## Done when

- Migration `0007` applies cleanly on the existing database, and the rules are seeded from the catalogue.
- All checks above run in the pipeline, and `check_runs` shows a status for all 45 on every claim.
- The Findings tab shows findings with working "show on page", and the review is saved and audited.
- `import-registries` and the synthetic `packages.csv` / `tariffs.csv` load, and the benchmark checks run.
- `docker compose exec backend pytest`: all pass. Ruff is clean (line length 120), pyright reports 0 errors,
  and the frontend `tsc` / `vite build` succeed.
- `evaluate-checks` output is reported, with the numbers against the targets.
- `CLAUDE.md` "Current state" is updated, and the commit message is `Step 9: rule engine with evidence-linked
  findings`.

## Guardrails (repeat from CLAUDE.md)

- **Flags only.** The system never rejects or approves. Findings are for a human adjudicator.
- **Medical-layer findings** are amber at most with `needs_medical_review`, until clinician review changes
  the rule's status.
- **Nothing about real patients** goes to an external AI. This step uses no AI at all.
- **No secrets in code.** Thresholds live in `Rule.config`; policy values live in `PolicyTerm` and the
  policy record.
- **Ask before adding a dependency** or changing an applied migration.
