"""add is_admin column to users

Revision ID: 3ad434bca6cb
Revises: 4e6e60586e70
Create Date: 2026-07-14 06:44:46.653225+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3ad434bca6cb'
down_revision: Union[str, None] = '4e6e60586e70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # is_admin column already exists in the initial schema (f65673b6a290)
    # This migration is a no-op to prevent DuplicateColumn error
    pass


def downgrade() -> None:
    # Cannot drop column that was created by initial schema
    pass
