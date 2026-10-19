"""Background jobs run by the worker."""

import uuid
from datetime import datetime, timezone

from app.db.session import SessionLocal


def ping(message: str) -> dict:
    """Test job: proves the API -> queue -> worker path works."""
    return {"message": message, "processed_at": datetime.now(timezone.utc).isoformat()}


def process_claim(claim_id: str) -> dict:
    """Run the processing pipeline for one claim (queued after upload or reprocess)."""
    from app.pipeline.runner import run_pipeline

    with SessionLocal() as db:
        claim = run_pipeline(db, uuid.UUID(claim_id))
        return {"claim_id": claim_id, "status": claim.status.value, "error": claim.processing_error}