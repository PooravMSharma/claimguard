from fastapi import APIRouter, Depends, HTTPException
from rq import Worker
from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy import text

from app import __version__
from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.session import engine
from app.services.queue import job_queue, redis_conn
from app.services.storage import get_storage

router = APIRouter(prefix="/health", tags=["health"])
settings = get_settings()


def _error(exc: Exception) -> str:
    # Show error details only in development; never leak internals in production.
    return str(exc)[:300] if settings.is_development else "unavailable"


@router.get("")
def health() -> dict:
    checks: dict[str, dict] = {}

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            try:
                revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            except Exception:
                revision = None
        checks["database"] = {"ok": revision is not None, "schema": revision}
        if revision is None:
            checks["database"]["error"] = "Migrations not applied (run: alembic upgrade head)"
    except Exception as exc:
        checks["database"] = {"ok": False, "error": _error(exc)}

    try:
        redis_conn.ping()
        checks["queue"] = {"ok": True}
    except Exception as exc:
        checks["queue"] = {"ok": False, "error": _error(exc)}

    try:
        get_storage().check()
        checks["storage"] = {"ok": True, "backend": settings.storage_backend}
    except Exception as exc:
        checks["storage"] = {"ok": False, "error": _error(exc)}

    try:
        workers = Worker.count(connection=redis_conn, queue=job_queue)
        checks["worker"] = {"ok": workers > 0, "count": workers}
        if workers == 0:
            checks["worker"]["error"] = "No worker running"
    except Exception as exc:
        checks["worker"] = {"ok": False, "error": _error(exc)}

    all_ok = all(check["ok"] for check in checks.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "app": settings.app_name,
        "version": __version__,
        "env": settings.app_env,
        "checks": checks,
    }


@router.post("/test-job", dependencies=[Depends(get_current_user)])
def start_test_job() -> dict:
    job = job_queue.enqueue("app.worker.jobs.ping", "hello from ClaimGuard", result_ttl=600)
    return {"job_id": job.id, "status": job.get_status()}


@router.get("/test-job/{job_id}", dependencies=[Depends(get_current_user)])
def get_test_job(job_id: str) -> dict:
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except NoSuchJobError:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job.id, "status": job.get_status(), "result": job.return_value()}