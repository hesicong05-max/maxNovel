import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.json_preflight import (
    BLOCKED,
    PASS,
    REVIEW_REQUIRED,
    _begin_readonly_transaction,
    run_preflight,
)
from tests.conftest import TEST_DATABASE_BACKEND, TEST_DATABASE_URL, test_engine


pytestmark = pytest.mark.skipif(
    TEST_DATABASE_BACKEND != "postgresql",
    reason="PostgreSQL 16 specialty test",
)

TEST_KEY = "postgres-preflight-test-key-with-32-bytes"


async def _insert_project(project_id: str):
    async with test_engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO projects (id, title, genre, status)
                VALUES (:id, 'Preflight fixture', '玄幻', 'draft')
                """
            ),
            {"id": project_id},
        )


async def test_postgres_clean_preflight_is_read_only_and_complete(clean_db):
    await _insert_project("preflight-clean")
    async with test_engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO worldviews (
                    id, project_id, characters, geography, factions, power_system,
                    history, conflicts, special_settings, parsed_elements
                ) VALUES (
                    'wv-clean', 'preflight-clean', '[]', '[]', '[]', '[]',
                    '[]', '[]', '[]', '[]'
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO outlines (id, project_id, reveal_plan, chapters)
                VALUES ('outline-clean', 'preflight-clean', '[]', '[]')
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO chapters (
                    id, project_id, chapter_num, revealed_elements
                ) VALUES ('chapter-clean', 'preflight-clean', 1, '[]')
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO story_memories (
                    id, project_id, revealed_elements, character_states,
                    foreshadows, timeline, chapter_summaries
                ) VALUES (
                    'memory-clean', 'preflight-clean', '[]', '{}',
                    '[]', '[]', '[]'
                )
                """
            )
        )
        before = (
            await connection.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM worldviews) AS worldviews,
                        (SELECT count(*) FROM outlines) AS outlines,
                        (SELECT count(*) FROM chapters) AS chapters,
                        (SELECT count(*) FROM story_memories) AS memories
                    """
                )
            )
        ).mappings().one()

    report = await run_preflight(
        database_url=TEST_DATABASE_URL,
        environment_label="CI PostgreSQL",
        hmac_key=TEST_KEY,
        code_revision="test",
    )

    async with test_engine.begin() as connection:
        after = (
            await connection.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*) FROM worldviews) AS worldviews,
                        (SELECT count(*) FROM outlines) AS outlines,
                        (SELECT count(*) FROM chapters) AS chapters,
                        (SELECT count(*) FROM story_memories) AS memories
                    """
                )
            )
        ).mappings().one()

    assert dict(before) == dict(after)
    assert report["data_shape_status"] == PASS
    assert report["overall_status"] == REVIEW_REQUIRED
    assert report["coverage"]["checked_fields"] == 16
    assert report["coverage"]["cells_checked"] == 16
    assert report["write_performed"] is False


async def test_postgres_blockers_are_classified_and_redacted(clean_db):
    await _insert_project("preflight-blocked")
    double_encoded = json.dumps(
        json.dumps([{"name": "不能泄露的角色"}], ensure_ascii=False),
        ensure_ascii=False,
    )
    async with test_engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO worldviews (
                    id, project_id, characters, geography, factions, power_system,
                    history, conflicts, special_settings, parsed_elements
                ) VALUES (
                    :id, :project_id, :characters, :geography, :factions,
                    :power_system, :history, :conflicts, :special_settings,
                    :parsed_elements
                )
                """
            ),
            {
                "id": "raw-sensitive-id",
                "project_id": "preflight-blocked",
                "characters": double_encoded,
                "geography": "not-json-secret",
                "factions": "null",
                "power_system": json.dumps("plain-secret"),
                "history": "7",
                "conflicts": "true",
                "special_settings": "{}",
                "parsed_elements": '{"legacy": true}',
            },
        )

    first = await run_preflight(
        database_url=TEST_DATABASE_URL,
        environment_label="CI PostgreSQL",
        hmac_key=TEST_KEY,
        sample_limit=2,
        code_revision="test",
    )
    second = await run_preflight(
        database_url=TEST_DATABASE_URL,
        environment_label="CI PostgreSQL",
        hmac_key=TEST_KEY,
        sample_limit=2,
        code_revision="test",
    )
    serialized = json.dumps(first, ensure_ascii=False)

    assert first["overall_status"] == BLOCKED
    assert first["summary_counts"]["double_encoded_array"] == 1
    assert first["summary_counts"]["invalid_json"] == 1
    assert first["summary_counts"]["json_null"] == 1
    assert first["summary_counts"]["json_string"] == 1
    assert first["summary_counts"]["json_number"] == 1
    assert first["summary_counts"]["json_boolean"] == 1
    assert first["summary_counts"]["wrong_top_level"] == 1
    assert first["summary_counts"]["parsed_elements_object"] == 1
    assert "不能泄露的角色" not in serialized
    assert "not-json-secret" not in serialized
    assert "raw-sensitive-id" not in serialized
    assert "novel_test_password" not in serialized
    assert [
        (issue["case_id"], issue["samples"])
        for issue in first["issue_index"]
    ] == [
        (issue["case_id"], issue["samples"])
        for issue in second["issue_index"]
    ]


async def test_postgres_readonly_transaction_rejects_dml(clean_db):
    await _insert_project("preflight-readonly")
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await _begin_readonly_transaction(connection)
            try:
                with pytest.raises(DBAPIError):
                    await connection.execute(
                        text(
                            """
                            UPDATE projects
                            SET title = 'must not be written'
                            WHERE id = 'preflight-readonly'
                            """
                        )
                    )
            finally:
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()

    async with test_engine.connect() as connection:
        title = (
            await connection.execute(
                text("SELECT title FROM projects WHERE id = 'preflight-readonly'")
            )
        ).scalar_one()
    assert title == "Preflight fixture"
