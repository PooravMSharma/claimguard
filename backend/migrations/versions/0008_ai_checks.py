"""ai checks: cache of AI reviewer answers

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-21 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('ai_check_cache',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('purpose', sa.String(length=80), nullable=False),
    sa.Column('model', sa.String(length=120), nullable=False),
    sa.Column('response', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_ai_check_cache'))
    )


def downgrade() -> None:
    op.drop_table('ai_check_cache')
