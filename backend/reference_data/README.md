# Reference data

Official datasets ClaimGuard checks claims against. Every load is recorded in
the `reference_imports` table with its source document, version, in-force date
and file checksum, and every row points back to that import.

## Bundled (load with one command)

```
docker compose exec backend python -m app.cli load-reference-defaults
```

| File | What | Source |
|---|---|---|
| `irdai_non_payables.csv` | IRDAI Lists I-IV (146 items) + bill-text synonyms | IRDAI Guidelines on Standardization in Health Insurance (2019), Annexure. Synonyms added by ClaimGuard. |
| `policy_terms_standard.json` | Waiting periods, moratorium, timelines, standard exclusions | IRDAI Master Circular on Health Insurance (29 May 2024), Insurance Products Regulations 2024, standard exclusions. Values marked `typical` are market defaults, replace with the client's policy. |
| `icd10cm.csv.gz` | 98,147 ICD-10-CM codes | US NCHS/CDC ICD-10-CM (public domain), via the `simple-icd-10-cm` 1.5.0 package. |
| `cghs_2025_adjustments.json` | City tier / NABH / super speciality / ward percentages | CGHS OM dated 03.10.2025, effective 13.10.2025. Used by the rate calculator. |

## Loaded from official files (put them in `raw/`)

| Dataset | Command |
|---|---|
| CGHS rates (Annexure I) | `python -m app.cli import-tariffs --source cghs --file reference_data/raw/<file> --version CGHS-2025-10 --effective-from 2025-10-13 --source-name "CGHS OM 03.10.2025 Annexure I" --source-url <link>` |
| PM-JAY packages (HBP 2022) | `python -m app.cli import-packages --file reference_data/raw/<file> --version HBP2022 --effective-from 2022-04-01 --source-name "NHA HBP 2022" --source-url <link>` |

Files can be CSV or Excel. Column headings are recognised by name (see
`templates/`); title rows above the table are skipped. Loading the same version
again replaces it. Loading a newer tariff closes the old rates the day before
the new ones start, so older claims are still checked against the rates in
force on their admission date.

Check what is loaded: `python -m app.cli reference-status`

The CGHS download from cghs.mohfw.gov.in (one row per item: `Procedure Name`, `Speciality Classification`,
`Tier`, `Facility`, `Ward`, `Cghs Code No.`, `Rate (₹)`) loads as is. Rates written "38740+GST" load as the base
rate, "(Inclusive of GST)" as printed, and "Unlisted" rows are skipped; the import notes give the counts.

### Bill wording links

Hospitals name items their own way ("Hb%", "KFT"). The rate check matches wording to tariff items itself (see
`docs/step-13-benchmarks.md`); a confirmed link always wins. To confirm links: run
`python -m app.cli tariff-match-report --out storage/tariff_matches.csv`, correct the `Tariff code` column
(blank = leave unmatched), and load the file as **Bill wording links** on Reference data (or
`python -m app.cli import-tariff-mappings --file ... --version ... --source-name ...`).

## Limits to know

- ICD-10-CM is the US clinical modification. Indian hospitals code in WHO
  ICD-10; when a code is not in the set, the lookup falls back to its
  3-character category and says so.
- CGHS ward percentages are not applied to consultations, investigations,
  radiotherapy, day care and minor procedures. Which rows those are is decided
  from the section/name text at import time (`ward_uniform`).
