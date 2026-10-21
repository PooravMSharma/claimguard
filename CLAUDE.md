# ClaimGuard

AI review assistant for Indian hospital insurance claims, built for insurers and TPAs. It reads a claim file
(PDFs, scans, photos), builds a structured claim record with evidence links to the page, runs medical, billing
and policy checks, and gives the adjudicator a risk score with evidence. **Humans decide; the system flags.**

v1 scope: final claim review (final cashless and reimbursement). Benchmarks: PM-JAY (HBP) packages and CGHS
rates. Policy terms: standard IRDAI terms until a client supplies policy data. Medical rules come only from
published guidelines and are capped at **amber** until a clinician has reviewed them.

## Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2 (psycopg 3), Alembic, pydantic-settings, RQ worker on Redis.
- **Reading:** pypdfium2 (render + text layer), Tesseract via pytesseract (`eng+hin`), Pillow, reportlab
  (synthetic documents).
- **AI:** pluggable provider in `app/ai/client.py`: `ollama` (local, default for development: `qwen2.5vl:7b`)
  or `anthropic`. Every call is logged to `ai_calls` with tokens, time and cost.
- **Frontend:** React 18 + TypeScript + Vite 5.
- **Everything runs in Docker Compose.**

| Service  | Host port | Notes |
|----------|-----------|-------|
| frontend | 5174 | Vite dev server (container 5173) |
| backend  | 8001 | FastAPI, API docs at `/docs`; runs `alembic upgrade head` on start |
| worker   | -    | RQ worker: claim processing pipeline |
| postgres | 5433 | |
| redis    | 6379 | |
| Ollama   | 11434 on the host Mac | reached from containers at `http://host.docker.internal:11434` |

## Layout

```
backend/
  app/
    ai/            provider interface + Ollama/Anthropic clients (structured JSON answers)
    api/routes/    auth, users, audit, health, reference, claims (queue), record, findings, review (summary,
                   assign, decisions, query letter)
    core/          config (Settings from .env), security, logging
    db/            session
    extraction/    claim record: values (parsing), layout (lines/labels/tables), documents (per-type
                   rules), ai_extract (AI reader + merge), store (save/keep corrections), evaluate
    models/        SQLAlchemy models (claim, extraction, reference, rules, decision, user, enums)
    pipeline/      pages -> read (OCR/classify) -> extract; runner.py holds STEPS
    rules/         catalogue.py: the 45 check IDs (single source of truth); engine.py (runs checks, severity
                   policy); context.py (claim as checks see it); checks/ (one function per check, by layer;
                   judgement.py = the AI checks); ai_review.py (AI reviewer: facts pack, questions, cache);
                   pdf_fonts.py (fonts in digital PDFs, F-02/F-04); reference.py (lookups); defaults.py
                   (thresholds); store.py (rules table, save findings, AI cache/log); evaluate.py (score vs truth)
    review/        Step 11: scoring.py (risk score / band / suggested payable), sla.py (IRDAI deadlines),
                   letters.py (query letter draft)
    schemas/       pydantic API schemas
    services/      intake, storage (local/S3), queue, audit, users, login_guard, reference importers
    synthetic/     synthetic claim generator (ground truth for every check)
    worker/        RQ entry point and jobs
    cli.py         admin commands (see below)
  migrations/versions/   0001..0008 (Alembic)
  reference_data/        bundled defaults; raw/ holds official files (CGHS, HBP) when supplied
  tests/
frontend/src/
  api/ (client.ts, types.ts)  auth/  components/
  pages/claims/ (ClaimQueue, UploadClaim, ClaimDetail = review screen, ReviewHeader, FindingsView,
                 DecisionPanel, ClaimRecordView, PageViewer)
  pages/admin/ (Users, AuditLog)  pages/reference/ReferenceData.tsx
```

Two files are named `record.py` (`api/routes/record.py` and `schemas/record.py`), and the same goes for
`claims.py`. Don't mix them up.

## Commands

Run everything from the repo root.

```bash
docker compose up --build -d                 # start (rebuild after requirements/Dockerfile changes)
docker compose down                          # stop cleanly (avoids "Postgres not properly shut down")
docker compose restart backend worker        # pick up code/.env changes
docker compose logs -f worker | grep -E "claim CG|HTTP Request|failed"   # follow processing

docker compose exec backend pytest           # full test suite (expected: all pass)

# CLI: docker compose exec backend python -m app.cli <command>
create-admin --email you@company.in --name "Your Name"
load-reference-defaults                      # IRDAI non-payables, standard policy terms, ICD-10
import-tariffs / import-packages             # official CGHS / PM-JAY files (see reference_data/README.md)
reference-status
import-registries --folder <dir> --version V --source-name S   # doctors/hospitals/watchlist/policies CSVs
load-synthetic-reference                     # synthetic benchmarks (SYN- codes) + registries from the generator
import-synthetic --limit 20 [--scanned]      # load generated claims (scans become CG-SYN-xxxx-S)
reprocess-synthetic [--scanned|--clean] [--limit N]
evaluate-reading                             # page types + OCR vs ground truth
evaluate-extraction [--misses N]             # claim record vs ground truth, AI calls and time
ai-check                                     # one test request to the configured AI model (run in worker)
run-checks --synthetic | --claim CG-... [--no-ai] [--limit N]   # re-run checks on stored records (no re-reading)
evaluate-checks [--misses N]                 # findings vs ground truth: recall, precision, false reds, band
rescore                                      # risk score / band / payable from stored findings (no re-run)

# Synthetic data (deterministic, seed 42): writes backend/synthetic_data/ (gitignored)
docker compose exec backend python -m app.synthetic --count 300
```

## Configuration (.env)

`.env` is never committed. `.env.example` holds the base keys, and the defaults for the rest live in
`backend/app/core/config.py`. AI settings used in development:

```
AI_PROVIDER=ollama            # or anthropic (needs AI_API_KEY and credit); blank = rules only
AI_MODEL=qwen2.5vl:7b
AI_EXTRACTION=gaps            # off | gaps (all scanned docs + docs with missing items) | all
AI_CHECKS=true                # AI judgement checks (default true when AI_PROVIDER is set)
AI_PARALLEL_CALLS=1           # 1 for a local model
AI_TIMEOUT_SECONDS=600
AI_IMAGE_MAX_SIDE=1280
SLA_CASHLESS_HOURS=3          # review deadlines (defaults shown); SLA_PREAUTH_HOURS=1, SLA_REIMBURSEMENT_DAYS=15
```

## Current state

Build plan: 14 steps.

- Done:
  1–5: foundation, auth/roles/audit, reference data, synthetic generator.
  6: intake/queue. 7: page reading. 8: structured claim record + AI reader (Ollama).
  9: rule engine: 37 deterministic checks run as the pipeline's `checks` step.
  10: F-02/F-04 from fonts in digital PDFs (rules); AI reviewer for M-01, M-02, M-04, M-06, X-08, B-11 and the
  AI halves of C-02 (signature on scans, vision), X-06 (same procedure in other words -> amber) and M-03
  (documented reason for a repeat -> info). Without a model the AI checks report `pending_ai`.
- Step 8 results: clean 100% key fields; scans with Ollama 87.1% key fields, 98.8% table rows.
- Step 9 results (synthetic, deterministic checks):
  - Clean claims (300): recall 100%, precision 100%, no red on any genuine claim.
  - Scans read by rules only (82): precision 83%, recall 25%. Values OCR'd with confidence < 0.85 are not
    used to raise findings (the check reports "could not check" instead); the AI reader lifts this.
- Step 10 results:
  - Offline (300 clean): F-02 4/4, F-04 4/4; each AI question is asked only on its 4 seeded claims (gates on
    vitals, lab markers, test cost >= Rs. 3,000); genuine claims need no AI call.
  - Mac with Ollama qwen2.5vl:7b (29 claims): clean 100% recall / 100% precision; AI checks found 1/1 on clean
    and 1/1 on scans, 0 false flags. A 7B model asked open clinical questions over-flagged badly (4 false
    flags in 8 claims), hence the gates. Sample is small: re-measure on more claims in Step 13.
- Step 11: risk score and band per claim (PRD rules: any red finding or score >= 70 -> red; any amber or
  score 30-69 -> amber; else green; score = sum of rule weights, max 100; medical findings alone never red;
  dismissed findings stop counting), IRDAI review deadlines, queue sorted by priority with band / assignee
  filters, review screen (summary header, ranked findings beside the page, keyboard shortcuts, notes),
  decision panel (approve / with deductions / query with letter draft / escalate / SIU) and assignment.
  Offline (300 clean, rules only): stored band matches the seeded band on 286/300; all 14 differences are
  claims whose only seeded issue is an AI check (no model offline).
- **Next: Step 12, admin and rules management.**
- Then: 13 evaluation on freshly regenerated
  synthetic data, 14 hardening.
- Still pending from the client: the official CGHS OM (03.10.2025, Annexure I) and the PM-JAY HBP 2022
  package list (backend/reference_data/raw/). Until then benchmarks are the synthetic SYN- set.

## Conventions

- **Evidence or it didn't happen.** Every extracted value and every finding keeps `page_id` + `bbox` so the UI
  can show it on the page.
- **Rules first, AI for gaps.**
  - Digital (text-layer) values from rules win over AI values.
  - On scans the AI's reading wins, then `documents.tidy()` cleans OCR slips.
  - Values from either source go through `documents.normalize()` so they have the same shape.
- **Reviewer corrections survive reprocessing** (`extraction/store.py` keeps them). Never overwrite them.
- **Audit everything that changes state:** `services/audit.log_event(...)`.
- **Schema changes = a new Alembic migration** in `migrations/versions/` (next is `0009_...`). Never edit an
  applied migration.
- **Checks:** one function per check in `app/rules/checks/<layer>.py`, registered with `@check("B-01")`.
  Thresholds live in `rules.config` (defaults in `rules/defaults.py`), never in the check. A check returns
  passed / flagged / not_applicable / not_evaluable; the engine applies the severity policy (cap at the
  rule's max; medical amber until clinician-reviewed; red downgraded to amber when evidence could be a misread).
- **Findings that a reviewer has confirmed or dismissed are never deleted**; re-runs match them by fingerprint.
- **Review workflow:** a note never settles a finding; medical-necessity findings are confirmed or dismissed only
  by a senior / medical reviewer (`claims.review_escalated`); approval needs every red and amber finding
  reviewed; after "escalate" only a senior reviewer decides; approve / approve with deductions close the claim.
  Every review, assignment and decision is audit-logged and re-scores the claim.
- **Fatal AI errors stop further calls for that claim.** Fatal means no server, no model or no credit.
  The claim is still processed with rules only.
- **Tests never call a real AI.** Use `FakeAI` / the fake Ollama server in `tests/test_ai_providers.py`, or
  `ScriptedAI` in `tests/test_ai_review.py`. `tests/conftest.py` forces `AI_CHECKS=false`.
- **AI findings:** only an explicit negative verdict that cites real facts raises one; "unclear", no answer or
  invented fact ids raise nothing. Questions are gated by the claim data (no ICU stay -> no ICU question).
  AI findings carry `model_version` and are amber at most.
- **Style:**
  - Ruff, line length 120.
  - Pyright: 0 errors expected.
  - Plain, specific docstrings that explain *why*.
- **Synthetic data is fictional.** Names, hospitals and brands are invented (brands marked "(fictional)").

## Guardrails

- **Real patient data:** none is to be sent to an external AI provider without the client's written
  approval and a DPDP Act check. The local Ollama path keeps data on the machine.
- **Secrets:**
  - Never put API keys or passwords in code, chat or commits.
  - The user sets them in `.env` themselves.
  - The GitHub repo is private.
- **Severity cap:** medical-necessity checks stay **amber**, never red, until clinician review.
- **The system recommends; an adjudicator decides.** No auto-rejection.

## Working style (how the owner wants changes delivered)

- **No code until he says "build it" / "write it".** Discussion and planning are fine before that.
- **Builds:** deliver complete files plus their exact target paths, with no zips. He places the files himself.
- **Small fixes:** give targeted edit instructions (file, what to replace, with what), not whole rewritten
  files.
- **Each step ends with:** files and locations, any `requirements.txt`/`.env` lines, run/test commands with
  expected results, and a commit message. Report measured results honestly, including misses.
