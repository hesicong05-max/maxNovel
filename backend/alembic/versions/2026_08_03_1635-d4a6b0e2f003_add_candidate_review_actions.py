"""Add candidate review audit and action metadata.

Revision ID: d4a6b0e2f003
Revises: c3f5a9d1e002
Create Date: 2026-08-03 16:35:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4a6b0e2f003"
down_revision: Union[str, None] = "c3f5a9d1e002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("lore_extraction_candidates") as batch_op:
        batch_op.add_column(
            sa.Column(
                "suggestion_resolutions",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "user_overrides",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch_op.create_unique_constraint(
            "uq_lore_extraction_candidate_accepted_element",
            ["project_id", "accepted_element_id"],
        )

    op.create_table(
        "lore_candidate_revisions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.String(length=32),
            sa.ForeignKey("lore_extraction_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("type_key", sa.String(length=50), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("field_states", sa.JSON(), nullable=False),
        sa.Column("suggestion_resolutions", sa.JSON(), nullable=False),
        sa.Column("user_overrides", sa.JSON(), nullable=False),
        sa.Column("change_kind", sa.String(length=30), nullable=False),
        sa.Column(
            "created_by",
            sa.String(length=32),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "candidate_id",
            "revision",
            name="uq_lore_candidate_revision",
        ),
    )
    op.create_index(
        "ix_lore_candidate_revisions_candidate_id",
        "lore_candidate_revisions",
        ["candidate_id"],
    )
    # Candidates created before this migration already represent revision 1.
    # Preserve that extracted baseline so later edits never start with a gap in
    # the immutable audit trail. Candidate ids are unique within their own
    # table, so reusing them as the initial revision ids is deterministic and
    # portable across SQLite and PostgreSQL.
    op.execute(
        sa.text(
            """
            INSERT INTO lore_candidate_revisions (
                id,
                candidate_id,
                revision,
                type_key,
                name,
                summary,
                payload,
                field_states,
                suggestion_resolutions,
                user_overrides,
                change_kind,
                created_by,
                created_at
            )
            SELECT
                candidate.id,
                candidate.id,
                1,
                candidate.type_key,
                candidate.name,
                candidate.summary,
                candidate.payload,
                candidate.field_states,
                candidate.suggestion_resolutions,
                candidate.user_overrides,
                'extracted',
                batch.requested_by,
                candidate.created_at
            FROM lore_extraction_candidates AS candidate
            JOIN lore_extraction_batches AS batch
              ON batch.id = candidate.batch_id
             AND batch.project_id = candidate.project_id
            WHERE NOT EXISTS (
                SELECT 1
                FROM lore_candidate_revisions AS revision
                WHERE revision.candidate_id = candidate.id
                  AND revision.revision = 1
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_table("lore_candidate_revisions")
    with op.batch_alter_table("lore_extraction_candidates") as batch_op:
        batch_op.drop_constraint(
            "uq_lore_extraction_candidate_accepted_element",
            type_="unique",
        )
        batch_op.drop_column("user_overrides")
        batch_op.drop_column("suggestion_resolutions")
