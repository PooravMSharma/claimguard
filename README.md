# ClaimGuard

AI review assistant for hospital insurance claims: reads the claim file, runs medical,
billing and policy checks, and gives the adjudicator a risk score with evidence.

## Run locally

1. Copy `env.example` to `.env`.
2. `docker compose up --build`
3. Open http://localhost:5174 (dashboard) and http://localhost:8001/docs (API).

## Reference data

Load the bundled datasets once (IRDAI non-payables, standard policy terms, ICD-10):

`docker compose exec backend python -m app.cli load-reference-defaults`

CGHS rates and PM-JAY packages are loaded from the official files. See
`backend/reference_data/README.md`.

## Services

| Service  | Port | Purpose                              |
|----------|------|--------------------------------------|
| frontend | 5174 | React dashboard (Vite dev server)    |
| backend  | 8001 | FastAPI API                          |
| worker   | -    | Background jobs (OCR, checks)        |
| postgres | 5433 | Database                             |
| redis    | 6379 | Job queue                            |

## Tests

`docker compose exec backend pytest`

## Production

`docker compose -f docker-compose.prod.yml up -d --build`, behind HTTPS. Settings, backups and the
checklist: `docs/step-14-hardening.md`.
