"""Add database guards for committed lore merge state.

Revision ID: e1b3c7d9f010
Revises: d0a2b6c8e009
Create Date: 2026-08-06 18:24:00
"""

from typing import Sequence, Union

from alembic import op


revision: str = "e1b3c7d9f010"
down_revision: Union[str, None] = "d0a2b6c8e009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("setting_elements") as batch_op:
        batch_op.create_check_constraint(
            "ck_setting_element_no_self_merge",
            "merged_into_element_id IS NULL OR merged_into_element_id <> id",
        )
        batch_op.create_check_constraint(
            "ck_setting_element_merge_state",
            "(lifecycle_status = 'merged' AND enabled IS FALSE "
            "AND merged_into_element_id IS NOT NULL) OR "
            "(lifecycle_status <> 'merged' AND merged_into_element_id IS NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("setting_elements") as batch_op:
        batch_op.drop_constraint(
            "ck_setting_element_merge_state", type_="check"
        )
        batch_op.drop_constraint(
            "ck_setting_element_no_self_merge", type_="check"
        )
