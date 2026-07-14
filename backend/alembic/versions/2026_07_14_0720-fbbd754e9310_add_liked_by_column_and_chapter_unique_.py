"""add liked_by column and chapter unique constraint

Revision ID: fbbd754e9310
Revises: 3ad434bca6cb
Create Date: 2026-07-14 07:20:30.592444+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = 'fbbd754e9310'
down_revision: Union[str, None] = '3ad434bca6cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite doesn't support ALTER CONSTRAINT — use batch mode (copy-and-move)
    with op.batch_alter_table('chapters', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_chapter_project_num', ['project_id', 'chapter_num'])

    op.add_column('community_novels', sa.Column('liked_by', sqlite.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('community_novels', 'liked_by')

    with op.batch_alter_table('chapters', schema=None) as batch_op:
        batch_op.drop_constraint('uq_chapter_project_num', type_='unique')
