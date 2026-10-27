"""seed roles

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = [
    (1, "adjudicator", "Reviews claims, confirms or dismisses findings, records recommendations"),
    (2, "senior_reviewer", "Senior doctor: reviews escalated, high-value and medical-necessity cases"),
    (3, "fraud_investigator", "Fraud monitoring unit: works claims referred for investigation"),
    (4, "team_lead", "Claims manager: watches the queue, turnaround and flag rates"),
    (5, "admin", "Manages users, rules, tariffs and settings"),
]


def upgrade() -> None:
    roles = sa.table(
        "roles",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
    )
    op.bulk_insert(roles, [{"id": i, "name": n, "description": d} for i, n, d in ROLES])
    # Keep the id sequence ahead of the rows we inserted by hand.
    op.execute("SELECT setval(pg_get_serial_sequence('roles', 'id'), (SELECT MAX(id) FROM roles))")


def downgrade() -> None:
    op.execute("DELETE FROM roles WHERE id BETWEEN 1 AND 5")
