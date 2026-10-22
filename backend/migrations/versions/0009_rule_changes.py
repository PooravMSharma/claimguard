"""rule library: change history

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-22 04:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('rule_changes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('rule_id', sa.String(length=16), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('changed_by_id', sa.Uuid(), nullable=True),
    sa.Column('changes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('clinician', sa.String(length=200), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['changed_by_id'], ['users.id'], name=op.f('fk_rule_changes_changed_by_id_users'),
                            ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['rule_id'], ['rules.id'], name=op.f('fk_rule_changes_rule_id_rules'),
                            ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rule_changes'))
    )
    op.create_index(op.f('ix_rule_changes_rule_id'), 'rule_changes', ['rule_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_rule_changes_rule_id'), table_name='rule_changes')
    op.drop_table('rule_changes')
