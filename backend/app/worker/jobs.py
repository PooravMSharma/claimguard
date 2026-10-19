"""Background jobs run by the worker. Claim processing jobs are added from Step 6."""

from datetime import datetime, timezone


def ping(message: str) -> dict:
    """Test job: proves the API -> queue -> worker path works."""
    return {"message": message, "processed_at": datetime.now(timezone.utc).isoformat()}