"""fix duplicate FK constraints

The previous migration (f65673b6a290) already created the FK constraints
for owner_id. This migration was originally meant to add them but they
already exist. Making it a no-op to avoid errors on PostgreSQL.

Revision ID: 4e6e60586e70
Revises: f65673b6a290
Create Date: 2026-07-13 09:00:46.579231+00:00

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '4e6e60586e70'
down_revision: Union[str, None] = 'f65673b6a290'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # FK constraints were already created in migration f65673b6a290
    # This is a no-op migration to maintain migration chain integrity
    pass


def downgrade() -> None:
    # FK constraints are dropped in f65673b6a290's downgrade
    pass
