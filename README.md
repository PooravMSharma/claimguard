# ClaimGuard

AI review assistant for hospital insurance claims: reads the claim file, runs medical,
billing and policy checks, and gives the adjudicator a risk score with evidence.

## Run locally

1. Copy `.env.example` to `.env`.
2. `docker compose up --build`
3. Open http://localhost:5173 (dashboard) and http://localhost:8000/docs (API).

## Services

| Service  | Port | Purpose                              |
|----------|------|--------------------------------------|
| frontend | 5173 | React dashboard (Vite dev server)    |
| backend  | 8000 | FastAPI API                          |
| worker   | -    | Background jobs (OCR, checks)        |
| postgres | 5432 | Database                             |
| redis    | 6379 | Job queue                            |

## Tests

`docker compose exec backend pytest`