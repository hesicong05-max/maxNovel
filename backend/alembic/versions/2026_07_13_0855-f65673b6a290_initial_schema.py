"""initial schema — add owner_id columns for JWT auth

Adds owner_id column to projects and community_novels tables,
linking them to the users table for access control.

Revision ID: f65673b6a290
Revises:
Create Date: 2026-07-13 08:55:13.814782+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f65673b6a290'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add owner_id columns for access control (nullable for backward compat)
    op.add_column('community_novels', sa.Column('owner_id', sa.String(length=32), nullable=True))
    op.add_column('projects', sa.Column('owner_id', sa.String(length=32), nullable=True))

    # SQLite doesn't support ALTER TABLE ADD CONSTRAINT, use batch mode
    with op.batch_alter_table('community_novels') as batch_op:
        batch_op.create_foreign_key(
            'fk_community_novels_owner', 'users', ['owner_id'], ['id']
        )

    with op.batch_alter_table('projects') as batch_op:
        batch_op.create_foreign_key(
            'fk_projects_owner', 'users', ['owner_id'], ['id']
        )


def downgrade() -> None:
    with op.batch_alter_table('projects') as batch_op:
        batch_op.drop_constraint('fk_projects_owner', type_='foreignkey')

    with op.batch_alter_table('community_novels') as batch_op:
        batch_op.drop_constraint('fk_community_novels_owner', type_='foreignkey')

    op.drop_column('projects', 'owner_id')
    op.drop_column('community_novels', 'owner_id')
