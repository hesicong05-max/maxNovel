"""initial schema — create all tables with owner_id

Creates all 9 database tables (users, projects, worldviews, outlines,
chapters, story_memories, community_novels, community_tags,
novel_tag_association) with owner_id columns and FK constraints.

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
    # 1. users
    op.create_table(
        'users',
        sa.Column('id', sa.String(length=32), primary_key=True),
        sa.Column('email', sa.String(length=255), nullable=False, unique=True, index=True),
        sa.Column('username', sa.String(length=100), nullable=False, unique=True, index=True),
        sa.Column('hashed_password', sa.String(length=255), nullable=False),
        sa.Column('is_admin', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )

    # 2. projects (FK to users.owner_id)
    op.create_table(
        'projects',
        sa.Column('id', sa.String(length=32), primary_key=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('genre', sa.Enum('玄幻', '都市', '科幻', '武侠', '仙侠', '悬疑', '言情', name='novelgenre'), nullable=False),
        sa.Column('status', sa.Enum('draft', 'worldview_set', 'outline_pending', 'outline_confirmed', 'writing', 'completed', name='projectstatus'), nullable=False),
        sa.Column('total_chapters', sa.Integer(), nullable=True),
        sa.Column('chapter_word_count', sa.Integer(), nullable=True),
        sa.Column('style_intensity', sa.String(length=20), nullable=True),
        sa.Column('owner_id', sa.String(length=32), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )

    # 3. worldviews (FK to projects)
    op.create_table(
        'worldviews',
        sa.Column('id', sa.String(length=32), primary_key=True),
        sa.Column('project_id', sa.String(length=32), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('characters', sa.Text(), nullable=True),
        sa.Column('geography', sa.Text(), nullable=True),
        sa.Column('factions', sa.Text(), nullable=True),
        sa.Column('power_system', sa.Text(), nullable=True),
        sa.Column('history', sa.Text(), nullable=True),
        sa.Column('conflicts', sa.Text(), nullable=True),
        sa.Column('special_settings', sa.Text(), nullable=True),
        sa.Column('raw_text', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=True),
        sa.Column('parsed_elements', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )

    # 4. outlines (FK to projects)
    op.create_table(
        'outlines',
        sa.Column('id', sa.String(length=32), primary_key=True),
        sa.Column('project_id', sa.String(length=32), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('reveal_plan', sa.Text(), nullable=True),
        sa.Column('chapters', sa.Text(), nullable=True),
        sa.Column('story_arc', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )

    # 5. chapters (FK to projects)
    op.create_table(
        'chapters',
        sa.Column('id', sa.String(length=32), primary_key=True),
        sa.Column('project_id', sa.String(length=32), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('chapter_num', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('word_count', sa.Integer(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('revealed_elements', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )

    # 6. story_memories (FK to projects)
    op.create_table(
        'story_memories',
        sa.Column('id', sa.String(length=32), primary_key=True),
        sa.Column('project_id', sa.String(length=32), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('revealed_elements', sa.Text(), nullable=True),
        sa.Column('character_states', sa.Text(), nullable=True),
        sa.Column('foreshadows', sa.Text(), nullable=True),
        sa.Column('timeline', sa.Text(), nullable=True),
        sa.Column('chapter_summaries', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )

    # 7. community_novels (FK to projects + users)
    op.create_table(
        'community_novels',
        sa.Column('id', sa.String(length=32), primary_key=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('author_name', sa.String(length=100), nullable=True),
        sa.Column('genre', sa.String(length=50), nullable=False),
        sa.Column('project_id', sa.String(length=32), sa.ForeignKey('projects.id'), nullable=True),
        sa.Column('synopsis', sa.Text(), nullable=True),
        sa.Column('story_outline', sa.Text(), nullable=True),
        sa.Column('chapter_notes', sa.Text(), nullable=True),
        sa.Column('allow_cocreation', sa.Boolean(), nullable=True),
        sa.Column('view_count', sa.Integer(), nullable=True),
        sa.Column('like_count', sa.Integer(), nullable=True),
        sa.Column('total_chapters', sa.Integer(), nullable=True),
        sa.Column('total_words', sa.Integer(), nullable=True),
        sa.Column('owner_id', sa.String(length=32), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )

    # 8. community_tags
    op.create_table(
        'community_tags',
        sa.Column('id', sa.String(length=32), primary_key=True),
        sa.Column('name', sa.String(length=50), nullable=False, unique=True, index=True),
        sa.Column('usage_count', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )

    # 9. novel_tag_association (M2M)
    op.create_table(
        'novel_tag_association',
        sa.Column('novel_id', sa.String(length=32), sa.ForeignKey('community_novels.id'), primary_key=True),
        sa.Column('tag_id', sa.String(length=32), sa.ForeignKey('community_tags.id'), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table('novel_tag_association')
    op.drop_table('community_tags')
    op.drop_table('community_novels')
    op.drop_table('story_memories')
    op.drop_table('chapters')
    op.drop_table('outlines')
    op.drop_table('worldviews')
    op.drop_table('projects')
    op.drop_table('users')
