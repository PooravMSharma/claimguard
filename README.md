# ClaimGuard

**AI review assistant for Indian hospital insurance claims.** ClaimGuard reads a claim file (PDFs, scans,
phone photos), builds a structured claim record, runs 45 fraud, billing, medical and policy checks, and gives the
adjudicator a risk score. Every finding points to the exact spot on the page that supports it.

**The system flags; a human decides.** It never rejects a claim on its own.

Built for insurers and TPAs reviewing final cashless and reimbursement claims.

## What it does

1. **Upload.** A reviewer uploads the claim documents. The claim gets a review deadline based on IRDAI
   timelines.
2. **Reading.** Every page is rendered and read: the text layer of digital PDFs, Tesseract OCR (English + Hindi)
   for scans. Each page is classified (claim form, discharge summary, final bill, lab report, OT notes, …).
3. **Claim record.** Rules extract patient details, dates, diagnosis, procedures, every bill line, lab values and
   policy details. Where rules can't (poor scans, missing values), a **local vision model** fills the gaps. Every
   value keeps its page and position, and reviewer corrections survive reprocessing.
4. **45 checks in 7 groups:**

   | Group | Examples |
   |---|---|
   | Completeness | Missing documents, unsigned discharge summary, implant billed without invoice or sticker |
   | Forensics | PDF edited after its stated date, pasted digits in a different font, a page reused from another patient |
   | Consistency | Name / age / UHID differ across documents, dates out of order, ICU days billed vs nursing notes |
   | Medical need | Admission not justified, ICU not supported by vitals, stay longer than the norm, severity not supported by labs |
   | Billing | Bill arithmetic, IRDAI non-payables, unbundled package items, rates far above CGHS, charges above MRP |
   | Policy | Policy not active, waiting periods, exclusions and sub-limits, sum insured exhausted |
   | Registry | Doctor's registration, hospital registration, watchlisted or de-empanelled hospitals |

5. **Risk score and band** (red / amber / green), with a suggested payable amount after confirmed deductions.
6. **Review screen.** Ranked findings beside the page with the evidence boxed, keyboard shortcuts, notes, and a
   decision panel: approve, approve with deductions, query letter (drafted for you), escalate, or refer to SIU.
7. **Rule library.** Admins tune each check's weight, severity and thresholds. Every change needs a reason, is
   versioned and audit-logged, and the library shows how often reviewers dismiss each rule.

## Results (measured on synthetic claims)

Tested on a fresh batch of 300 synthetic claims that was not used during development. Each claim has known,
planted issues, so every check can be scored.

| | Issues found | Findings correct | Genuine claims marked red |
|---|---|---|---|
| Clean digital claims (300) | 100% | 100% | 0 of 120 |
| Scanned claims, rules only (81) | 31% | 100% | 0 of 29 |

With the local AI reader, scanned claims reached 87% of key fields read correctly. **These are synthetic-data
numbers; accuracy on real claims still has to be measured in a pilot.**

## Challenges solved

- **No real claim data.** A synthetic claim generator produces realistic claim PDFs and scans with planted
  problems, so every check can be measured without touching patient data.
- **A small local model over-flagged.** Asked open clinical questions, a 7B vision model raised 4 false flags in 8
  claims. The AI is now asked only when the data makes the question relevant (for example "was ICU justified?"
  only if every ICU-day vital was normal), and its answer counts only if it cites real facts from the claim.
  Result: 0 false flags in testing.
- **OCR misreads.** A scan read "5,00,000" as "§00,000", which became Rs. 0 and raised a false "over the sum
  insured" flag. Values read with low confidence or garbled digits are now treated as unread, never as evidence.
- **Bill wording never matches the rate list.** "CBC" vs "Complete Haemogram/CBC, Hb, RBC count…". A matcher
  maps abbreviations, compares only like with like (a test with a test), and skips the line when it is unsure.
- **CGHS is a government rate.** CGHS rates are adjusted for city tier, hospital grade (NABH) and ward, and a bill
  is flagged against CGHS only above 2.5x, since private hospitals charge more.
- **Claims stuck after a crash.** Claims left "processing" by a killed worker are detected and recovered on start.

## Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, Redis + RQ (background processing)
- **Reading:** pypdfium2, pypdf, Tesseract OCR, Pillow
- **AI:** pluggable provider. [Ollama](https://ollama.com) running `qwen2.5vl:7b` locally by default; Anthropic
  Claude optional and blocked unless explicitly allowed. Every AI call is logged with time and cost.
- **Frontend:** React 18, TypeScript, Vite
- **Runs on:** Docker Compose; nginx in production
- **Quality:** 311 automated tests (pytest), ruff, pyright, GitHub Actions CI

## Security and privacy

- Passwords hashed with bcrypt; sessions in `HttpOnly`, `Secure`, `SameSite=strict` cookies; account lockout after
  repeated failed logins.
- Five roles (adjudicator, senior reviewer, fraud investigator, team lead, admin), each with its own permissions.
- Audit log of every change and every claim opened (who, when, from which IP).
- Patient data stays on the machine: the AI runs locally, and sending data to an external AI is blocked in code
  unless `ALLOW_EXTERNAL_AI=true`.
- Refuses to start in production with a weak secret key, a default database password, open CORS, or external AI
  without approval.
- Security headers on every response, no caching of claim data, generic error messages with a reference ID.
- Backup and restore scripts with checksums.
- Medical-necessity findings stay **amber** until a clinician has reviewed the rule.

## Run it locally

Needs Docker Desktop. Ollama is optional (without it, everything except the AI checks runs).

```bash
cp env.example .env
docker compose up --build -d
docker compose exec backend python -m app.cli create-admin --email you@example.com --name "Your Name"
docker compose exec backend python -m app.cli load-reference-defaults
```

`create-admin` asks for a password. Open the dashboard at http://localhost:5174 (API docs at
http://localhost:8001/docs).

**Try it with synthetic claims:**

```bash
docker compose exec backend python -m app.synthetic --count 300           # writes backend/synthetic_data/
docker compose exec backend python -m app.cli load-synthetic-reference    # matching hospitals, doctors, policies
docker compose exec backend python -m app.cli import-synthetic --limit 20
```

Or upload any `claim.pdf` from `backend/synthetic_data/claims/` through the dashboard. Each claim folder has a
`ground_truth.json` saying what it should flag.

**Local AI (optional):** install [Ollama](https://ollama.com), run `ollama pull qwen2.5vl:7b`, then set in `.env`:

```
AI_PROVIDER=ollama
AI_MODEL=qwen2.5vl:7b
```

and restart with `docker compose restart backend worker`.

**Real benchmark data:** CGHS rates and PM-JAY packages load from the official files. See
[`backend/reference_data/README.md`](backend/reference_data/README.md).

| Service | Port | Purpose |
|---|---|---|
| frontend | 5174 | React dashboard (Vite dev server) |
| backend | 8001 | FastAPI API |
| worker | - | Claim processing: reading, extraction, checks |
| postgres | 5433 | Database |
| redis | 6379 | Job queue |

## Tests

```bash
docker compose exec backend pytest
```

## Production

`docker compose -f docker-compose.prod.yml up -d --build` behind HTTPS. Settings, backups and the checklist:
[`docs/step-14-hardening.md`](docs/step-14-hardening.md).

## Reference data sources

| Data | Source |
|---|---|
| Non-payable items (Lists I–IV) | IRDAI Guidelines on Standardization in Health Insurance (2019) |
| Standard policy terms | IRDAI Master Circular on Health Insurance (May 2024) |
| CGHS rates and adjustment percentages | CGHS OM dated 03.10.2025 (cghs.mohfw.gov.in) |
| PM-JAY packages | National Health Authority, HBP 2022 (pmjay.gov.in) |
| ICD-10 codes | US CDC ICD-10-CM (public domain) |

All claims, patients, hospitals and doctors in the synthetic data are fictional.
