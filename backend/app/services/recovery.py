"""Claims left behind when the worker stopped mid-job (restart, crash, out of memory).

The pipeline marks a claim failed when a step raises, but a killed process raises nothing: the claim would show
"processing" for ever. Run at worker start and by `python -m app.cli recover-stuck`. A claim is only touched when
it has been waiting far longer than any job may run, so a claim another worker is still processing is safe.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.claim import Claim
from app.models.enums import ClaimStatus
from app.services.audit import log_event

log = logging.getLogger(__name__)
GRACE = timedelta(minutes=15)
STOPPED = ("Processing stopped without finishing (the worker was restarted or ran out of memory). "
           "Use Reprocess to read the claim again.")
NEVER_STARTED = "Processing never started (the queued job was lost). Use Reprocess to read the claim again."


def queued_claim_ids() -> set[str] | None:
    """Claims with a processing job waiting or running in the queue; None when the queue cannot be read."""
    try:
        from rq.registry import StartedJobRegistry

        from app.services.queue import job_queue

        jobs = job_queue.get_jobs() + [j for j in (job_queue.fetch_job(i) for i in
                                                   StartedJobRegistry(queue=job_queue).get_job_ids()) if j]
        return {str(j.args[0]) for j in jobs if str(j.func_name).endswith("process_claim") and j.args}
    except Exception:
        log.exception("Could not read the job queue; queued claims are left alone")
        return None


def recover_stuck(db: Session, now: datetime | None = None, queued: set[str] | None = None,
                  check_queue: bool = True) -> list[str]:
    """Mark abandoned claims failed with a reason; return their claim numbers. The caller commits.

    processing: started longer ago than any job may run (JOB_TIMEOUT_SECONDS + 15 minutes).
    received: no job for it in the queue, 15 minutes after it was queued. A long queue of claims waiting for a
    slow local model is not touched, however long it waits.
    """
    now = now or datetime.now(timezone.utc)
    limit = now - timedelta(seconds=get_settings().job_timeout_seconds) - GRACE
    stuck = list(db.scalars(select(Claim).where(
        (Claim.status == ClaimStatus.PROCESSING) & (Claim.processing_started_at < limit))))
    if queued is None and check_queue:
        queued = queued_claim_ids()
    if queued is not None:
        stuck += [c for c in db.scalars(select(Claim).where(
            (Claim.status == ClaimStatus.RECEIVED) & (Claim.updated_at < now - GRACE))) if str(c.id) not in queued]
    for claim in stuck:
        was = claim.status.value
        claim.status = ClaimStatus.FAILED
        claim.processing_error = STOPPED if was == "processing" else NEVER_STARTED
        claim.processing_finished_at = now
        log_event(db, "claim.processing_abandoned", entity_type="claim", entity_id=claim.id,
                  details={"was": was, "error": claim.processing_error})
        log.warning("Claim %s was left %s; marked failed", claim.claim_number, was)
    return [c.claim_number or str(c.id) for c in stuck]
