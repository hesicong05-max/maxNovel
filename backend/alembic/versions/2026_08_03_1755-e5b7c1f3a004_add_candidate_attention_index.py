"""Add indexed candidate attention state.

Revision ID: e5b7c1f3a004
Revises: d4a6b0e2f003
Create Date: 2026-08-03 17:55:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5b7c1f3a004"
down_revision: Union[str, None] = "d4a6b0e2f003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BUILTIN_TYPES = {
    "world",
    "character",
    "location",
    "scene",
    "faction",
    "item",
    "conflict",
    "event",
    "foreshadow",
    "rule",
    "ability_system",
    "race",
    "historical_event",
    "social_institution",
    "other",
}


def _needs_attention(row) -> bool:
    if row.status != "pending_review":
        return False
    if not row.name or not row.type_key or row.type_key not in _BUILTIN_TYPES:
        return True
    if "needs_confirmation" in (row.field_states or {}).values():
        return True
    resolutions = row.suggestion_resolutions or {}
    return any(
        suggestion.get("suggestion_id")
        and resolutions.get(str(suggestion["suggestion_id"]))
        not in ("accept_as_new", "dismissed")
        for suggestion in (row.duplicate_conflict_suggestions or [])
    )


def upgrade() -> None:
    op.add_column(
        "lore_extraction_candidates",
        sa.Column(
            "needs_attention",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    candidate = sa.table(
        "lore_extraction_candidates",
        sa.column("id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("type_key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("field_states", sa.JSON()),
        sa.column("duplicate_conflict_suggestions", sa.JSON()),
        sa.column("suggestion_resolutions", sa.JSON()),
        sa.column("needs_attention", sa.Boolean()),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(
            candidate.c.id,
            candidate.c.status,
            candidate.c.type_key,
            candidate.c.name,
            candidate.c.field_states,
            candidate.c.duplicate_conflict_suggestions,
            candidate.c.suggestion_resolutions,
        )
    ).all()
    for row in rows:
        connection.execute(
            candidate.update()
            .where(candidate.c.id == row.id)
            .values(needs_attention=_needs_attention(row))
        )
    op.create_index(
        "ix_lore_candidate_project_attention_updated",
        "lore_extraction_candidates",
        ["project_id", "status", "needs_attention", "updated_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_lore_candidate_project_attention_updated",
        table_name="lore_extraction_candidates",
    )
    op.drop_column("lore_extraction_candidates", "needs_attention")
