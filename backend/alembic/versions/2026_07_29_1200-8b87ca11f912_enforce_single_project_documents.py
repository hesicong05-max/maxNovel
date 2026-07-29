"""Enforce one worldview, outline, and story memory per project.

Revision ID: 8b87ca11f912
Revises: fbbd754e9310
Create Date: 2026-07-29 12:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8b87ca11f912"
down_revision: Union[str, None] = "fbbd754e9310"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    for table in ("worldviews", "outlines", "story_memories"):
        duplicate = connection.execute(
            sa.text(
                f"SELECT project_id, COUNT(*) AS row_count FROM {table} "
                "GROUP BY project_id HAVING COUNT(*) > 1 LIMIT 1"
            )
        ).first()
        if duplicate:
            raise RuntimeError(
                f"Cannot add unique constraint: {table} contains duplicate "
                f"rows for project_id={duplicate.project_id}. Back up and "
                "merge the duplicate rows before rerunning the migration."
            )

    with op.batch_alter_table("worldviews") as batch_op:
        batch_op.create_unique_constraint(
            "uq_worldview_project",
            ["project_id"],
        )
    with op.batch_alter_table("outlines") as batch_op:
        batch_op.create_unique_constraint(
            "uq_outline_project",
            ["project_id"],
        )
    with op.batch_alter_table("story_memories") as batch_op:
        batch_op.create_unique_constraint(
            "uq_story_memory_project",
            ["project_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("story_memories") as batch_op:
        batch_op.drop_constraint(
            "uq_story_memory_project",
            type_="unique",
        )
    with op.batch_alter_table("outlines") as batch_op:
        batch_op.drop_constraint(
            "uq_outline_project",
            type_="unique",
        )
    with op.batch_alter_table("worldviews") as batch_op:
        batch_op.drop_constraint(
            "uq_worldview_project",
            type_="unique",
        )
