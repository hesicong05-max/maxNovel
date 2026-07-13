"""add foreign key constraints for owner_id

Adds FK constraints from projects.owner_id and community_novels.owner_id
to users.id. Uses batch mode for SQLite compatibility.

Revision ID: 4e6e60586e70
Revises: f65673b6a290
Create Date: 2026-07-13 09:00:46.579231+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4e6e60586e70'
down_revision: Union[str, None] = 'f65673b6a290'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite doesn't support ALTER TABLE ADD CONSTRAINT, use batch mode
    with op.batch_alter_table('projects') as batch_op:
        batch_op.create_foreign_key(
            'fk_projects_owner', 'users', ['owner_id'], ['id']
        )

    with op.batch_alter_table('community_novels') as batch_op:
        batch_op.create_foreign_key(
            'fk_community_novels_owner', 'users', ['owner_id'], ['id']
        )


def downgrade() -> None:
    with op.batch_alter_table('community_novels') as batch_op:
        batch_op.drop_constraint('fk_community_novels_owner', type_='foreignkey')

    with op.batch_alter_table('projects') as batch_op:
        batch_op.drop_constraint('fk_projects_owner', type_='foreignkey')
