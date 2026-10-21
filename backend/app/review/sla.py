"""Review deadlines (IRDAI Master Circular on Health Insurance, 2024).

- Final cashless: the insurer decides on the discharge authorisation within 3 hours of the request.
- Reimbursement: settle within 15 days of receiving the last necessary document.
- Pre-authorisation: decide within 1 hour.

The clock starts when the claim reaches ClaimGuard: the hospital's own request time is not in the file in a
form we can trust yet. Synthetic test claims get no deadline (they were generated, not received).
The windows are settings so a client's stricter internal targets can replace them.
"""

from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.models.enums import ClaimStage


def window(stage: ClaimStage | str) -> timedelta:
    s = get_settings()
    value = getattr(stage, "value", stage)
    if value == ClaimStage.FINAL_CASHLESS.value:
        return timedelta(hours=s.sla_cashless_hours)
    if value == ClaimStage.PREAUTH.value:
        return timedelta(hours=s.sla_preauth_hours)
    return timedelta(days=s.sla_reimbursement_days)


def due_at(stage: ClaimStage | str, received: datetime | None = None) -> datetime:
    return (received or datetime.now(timezone.utc)) + window(stage)
