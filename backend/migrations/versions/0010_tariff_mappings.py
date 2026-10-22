"""benchmark matching: confirmed bill wording -> tariff item

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-22 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('tariff_mappings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('bill_text', sa.String(length=300), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('import_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['import_id'], ['reference_imports.id'],
                            name=op.f('fk_tariff_mappings_import_id_reference_imports'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tariff_mappings')),
    sa.UniqueConstraint('source', 'bill_text', name='uq_tariff_mappings_source_text')
    )
    op.create_index(op.f('ix_tariff_mappings_import_id'), 'tariff_mappings', ['import_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_tariff_mappings_import_id'), table_name='tariff_mappings')
    op.drop_table('tariff_mappings')
