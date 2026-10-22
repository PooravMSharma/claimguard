# Step 13: real CGHS rates in the rate check, and a fresh evaluation

## What changed

### CGHS file import
The CGHS download (`Central_Government_Health_Scheme.csv`: one row per item with `Tier`, `Facility`, `Ward`,
`Cghs Code No.`, `Rate (₹)`) now loads with no edits:

- "NABH" / "Non-NABH" / "Super Speciality" in `Facility` set the grade of the row.
- "GENERAL WARD" and similar set the ward; "TIER I/II/III" the city tier.
- "38740+GST" loads as 38,740 (noted "plus GST"); "(Inclusive of GST)" as printed; "Unlisted" rows are skipped.
  The import notes give the counts.

Dry run of the October 2025 file: 2,460 rows loaded, all Tier I / NABH / general ward; 820 are ward-uniform
(tests, consultations); 26 rates plus GST, 17 including GST, 134 "Unlisted" skipped.

### Rate check (B-03): which tariff item a bill line is
Exact wording almost never matches ("CBC" vs "Complete Haemogram/CBC, Hb, RBC count ..."). For each bill line:

1. **Confirmed link** (Reference data → Bill wording links), if there is one.
2. **Same wording** as a tariff item.
3. **Close wording** (`app/rules/tariff_match.py`): both texts reduced to their core (part before the first
   comma, no brackets except "plain"/"contrast", abbreviations mapped to one word: CBC = haemogram = complete
   blood count). Words rarer in the tariff count more. Used only when the score is at least `min_match_score`
   (0.6) and no close rival has a price more than 10% different. "MRI Brain" (plain or contrast?) is not
   compared.
4. **Same kind of charge only**: a test line matches a test item, a consultation a consultation, a procedure a
   procedure or bedside item. A surgeon's fee never matches a CGHS procedure rate (that rate is the whole
   package). Cancer-surgery (TMH) rates only on claims with a cancer diagnosis (ICD C00–D49).

An agreed tariff (the client's own or PM-JAY) is searched before CGHS.

### CGHS rate for this claim
CGHS rates are adjusted with the percentages of the CGHS OM of 03.10.2025:

| | Factor |
|---|---|
| City tier | Tier I 1.0, Tier II 0.9, Tier III 0.8 (from the hospital registry; unknown → Tier I) |
| Hospital grade | NABH 1.0, Non-NABH 0.85, super speciality 1.15 (from the registry; unknown → super speciality for procedures, NABH for tests) |
| Ward | general 0.95, semi-private 1.0, private 1.05; not applied to tests and consultations; unknown → private |

Every unknown takes the highest rate that could apply, so missing data never makes a fair bill look high. The
finding names the item, how it was matched and each adjustment, e.g. *"the CGHS benchmark LB088 'Serum Amylase'
(matched by same wording, adjusted for tier 2 city ×0.9) is Rs. 270"*.

### CGHS is a reference, not the agreed rate
CGHS is what the government pays; private hospitals usually charge more. So against CGHS:

- the line is flagged only above **2.5× the CGHS rate** (`over_cghs_pct`: 150, editable in the rule library);
- the finding asks the reviewer to check the hospital's agreed rate and suggests **no deduction**.

Against an agreed tariff nothing changed: flagged above +25% (`over_benchmark_pct`), with the excess as the
suggested deduction. The right CGHS threshold is a business decision for the insurer.

### Confirming links
`python -m app.cli tariff-match-report --out storage/tariff_matches.csv` writes one row per distinct bill wording
on the claims in review: the item it was matched to, how, the benchmark, the typical billed rate, how many claims.
Correct the `Tariff code` column (blank = leave unmatched) and load the file on Reference data as **Bill wording
links** (or `import-tariff-mappings`). Links to codes that are not loaded are refused. Table `tariff_mappings`
(migration 0010).

### Fixes found by the fresh evaluation
- **Synthetic generator, M-05**: a seeded long stay could end at exactly norm + 2 days, which the rule allows
  (`extra_days_allowed` 2). The seeder now always goes past the allowance. Only one claim of seed 2026 changed.
- **Reading amounts**: OCR read "5,00,000" as "§00,000", which became Rs. 0 and raised a false "claim more than
  the sum insured left" (P-06). An amount whose digits start with "0" followed by more digits is now treated as
  not read.
- A bill line printed under "Procedure charges" (or nebulisation, dressing) is now the `procedure` category
  (it was "other" and never compared).

## Measured

Fresh synthetic batch, seed 2026 (never used during development), checks run offline, rules only:

| | Recall | Precision | Genuine claims with a red |
|---|---|---|---|
| Clean, 300 claims | 207/207 = 100% | 100% | 0/120 |
| Clean + the real CGHS list loaded beside the synthetic tariffs | 100% | 100% | 0/120 |
| Scanned, 81 claims, before the amount fix | 18/58 = 31% | 18/19 = 94.7% | 0/29 |
| Scanned, after | 18/58 = 31% | 18/18 = 100% | 0/29 |

- Risk band matches the seeded band on 282/300 clean claims (after the M-05 fix). All 18 differences are claims
  whose amber comes from an AI check, which has no model offline.
- Before the CGHS threshold, loading CGHS beside the synthetic tariffs added 8 flags on fair synthetic bills
  (e.g. MRI Brain billed Rs. 6,500 vs CGHS Rs. 2,750) — the reason for the separate threshold.
- Bill wording matching: 49/49 on a hand-labelled list of common bill wordings against the full CGHS list
  (21 of them are in `tests/test_tariff_match.py` and run when the CSV is at `reference_data/raw/cghs.csv`).
  The list was used while tuning, so real bills will do worse; the match report is how to check.
- Scans are read here by OCR rules only; on the Mac the AI reader lifts recall (Step 8).
- Tests: 296 pass (23 new in `tests/test_tariff_match.py`; 2 assertions added to `tests/test_extraction.py`).
