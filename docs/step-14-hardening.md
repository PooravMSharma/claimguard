# Step 14: hardening and production runbook

## What changed

### Refuses to run with unsafe settings
With any `APP_ENV` other than `development`, the API and the worker refuse to start, and list every problem
(`app/core/hardening.py`):

| Refused | Fix |
|---|---|
| `SECRET_KEY` default or under 32 characters | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| Database password default (`claimguard`, `postgres`…) or under 12 characters | Strong password in `POSTGRES_PASSWORD` and `DATABASE_URL` |
| `CORS_ORIGINS` with `*`, or `http://` for a non-local address | The `https://` address people open |
| `AI_PROVIDER=anthropic` without `ALLOW_EXTERNAL_AI=true` | Use `ollama`, or set the flag **only** with the client's written approval and a DPDP Act check |
| `BCRYPT_ROUNDS` < 12, `ACCESS_TOKEN_MINUTES` > 720 | Leave the defaults |

The external-AI rule is also enforced inside the AI client, so a CLI command cannot get around it. Development
is unchanged.

### Responses
- **Every API response** carries `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`,
  `Permissions-Policy` and `Cache-Control: no-store`, so claim data isn't kept in browser or proxy caches. Page
  images keep their own short private cache.
- **JSON responses** also carry a CSP that blocks scripts and framing. Outside development, responses add HSTS.
- **Request ID:** every request gets an `X-Request-ID`, or keeps a well-formed one sent by the caller. The app
  logs one line per request: method, path, status, time and ID. The query string is left out because it can
  contain a patient's name searched in the queue.
- **Unexpected errors** return *"Something went wrong on our side. Reference: <id>"*. The traceback is only in
  the server log, under the same ID.
- **CORS** allows only the methods and headers the dashboard uses.

### Claims left behind by a stopped worker
A worker that is killed (restart, out of memory) raises no error, so its claim stayed "processing" for ever. The
worker now checks at start, and `python -m app.cli recover-stuck` does the same on demand:
- **"Processing" claims** started more than `JOB_TIMEOUT_SECONDS` + 15 minutes ago are marked failed: "Processing
  stopped without finishing… Use Reprocess."
- **"Received" claims** with no job in the queue for 15 minutes are marked failed: "never started". A long queue
  waiting for a slow local model is never touched. If the queue can't be read, received claims are left alone.

Each one is audit-logged as `claim.processing_abandoned`.

### Run checks button
A claim whose checks never ran now shows **Run checks now** on the review screen, for reviewers. The claim
CG-20261019-31D62C was one of these.

### Access trail
Opening a claim is audit-logged as `claim.viewed`: who opened it, when, and from which IP. That's at most one
entry per user and claim every 30 minutes, because the screen re-reads a claim while it processes.

### Production setup (`docker-compose.prod.yml`)

| | Development (`docker-compose.yml`) | Production |
|---|---|---|
| Code | Mounted from the Mac, auto-reload | Baked into the image |
| Dashboard | Vite dev server :5174 | Built once, served by nginx on `HTTP_PORT` (8080) |
| API | :8001, docs at /docs | Only through nginx at `/api`; no /docs |
| Postgres / Redis | Open on 5433 / 6379 | Not reachable from outside Docker; Redis saves its queue to disk |
| Backend user | root | `app` (uid 10001) |
| Restarts | No | `unless-stopped`; backend has a health check |
| Access log | uvicorn's (with query strings) | The app's own line, without query strings |

nginx (`frontend/nginx.conf`) sets a strict CSP for the dashboard and allows 110 MB uploads. It passes the real
client IP to the audit log, overwriting any `X-Forwarded-For` a client sends.

### Backups (`scripts/backup.sh`, `scripts/restore.sh`)
- `backup.sh` writes `database.dump` (pg_dump), `storage.tar.gz` (the claim files) and `SHA256SUMS` into
  `backups/claimguard-<date-time>/`. That folder is ignored by git.
- `restore.sh` checks the checksums, asks you to type RESTORE, stops the API and worker, replaces the database and
  files, and starts them again.
- Both take `-f docker-compose.prod.yml` for the production stack.

### Tooling
- **Pyright:** 0 errors. Fixed `pipeline/classify.py`.
- **`backend/ruff.toml`, `backend/pyrightconfig.json`:** the editor, the command line and CI use the same settings
  (line length 120, migrations excluded).
- **`.github/workflows/ci.yml`:** every push runs ruff, pyright and pytest (with Postgres, Redis and Tesseract
  in CI) and the dashboard build. No secrets are needed.
- **`requirements.txt`:** grouped by purpose. pypdf, pypdfium2, Pillow and reportlab are needed at runtime, not
  only for tests. The versions are unchanged.
- **`.gitignore`:** the last line was `Thumbs.dbdemo/`, so neither `Thumbs.db` nor `demo/` was ignored. Now also
  ignored: `backups/`, `*.log`, `checks.txt`, `misses.txt`.

## Production checklist

1. **Server:** Linux with Docker, 4+ CPU and 16 GB RAM. Add more if Ollama runs on the same machine; the 7B vision
   model needs about 8 GB.
2. **HTTPS in front of port 8080** (a load balancer, Caddy or certbot + nginx). The login cookie is `Secure`
   outside development, so sign-in only works over `https://`.
3. **`.env` from `env.example`:**
   - `APP_ENV=production`
   - new `SECRET_KEY`
   - strong `POSTGRES_PASSWORD`, the same password in `DATABASE_URL`
   - `CORS_ORIGINS=["https://<your address>"]`
   - `AI_PROVIDER=ollama` with `AI_BASE_URL` (or `AI_PROVIDER=` blank for rules only)
4. `docker compose -f docker-compose.prod.yml up -d --build`. Startup errors list any unsafe setting.
5. Create the first admin:
   `docker compose -f docker-compose.prod.yml exec backend python -m app.cli create-admin --email … --name …`
6. Load the reference data: `load-reference-defaults`, then CGHS / PM-JAY (see `backend/reference_data/README.md`).
7. **Schedule a daily backup:** `scripts/backup.sh -f docker-compose.prod.yml /path/off/this/disk`. Then do a
   restore once on a spare machine.
8. Check `/api/health`: database, queue, storage and worker should all be ok.

## Not done here (needs the client or infrastructure)
- HTTPS certificates and the domain.
- Off-site, encrypted backup storage.
- Central log collection and alerting.
- The DPDP Act review itself: notice, consent and retention period. The system now records access and blocks
  external AI. How long claim data is kept is a decision for the client.

## Measured
- 311 tests (15 new in `tests/test_hardening.py`): 310 pass here and 1 is skipped (the real-CGHS test, which needs
  the CSV).
- Ruff and pyright are clean, including the exact versions CI pins. The dashboard builds.
- **Production mode, checked here without Docker:**
  - The API refused to start with the default secret, the default database password and `AI_PROVIDER=anthropic`,
    and listed all three.
  - With safe settings, behind the production nginx config:
    - The dashboard was served with its CSP and headers.
    - `/api` was proxied with the API's headers.
    - Login set an `HttpOnly; Secure; SameSite=strict` cookie.
    - The audit log recorded the real client IP, not a faked `X-Forwarded-For`.
- **Not run here (no Docker daemon in this environment):** building the production images and the backup /
  restore scripts. Both need a first run on the Mac.
