import os
import sqlite3
import subprocess
import sys


def _run_alembic(backend_dir, database_url, *args):
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": database_url,
            "DEBUG": "true",
            "JWT_SECRET": "lore-migration-test-secret",
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=backend_dir,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_lore_migration_upgrade_and_downgrade_are_additive(tmp_path):
    backend_dir = os.path.dirname(os.path.dirname(__file__))
    database_path = tmp_path / "lore-migration.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"

    _run_alembic(backend_dir, database_url, "upgrade", "head")


def test_candidate_review_migration_backfills_existing_candidate_revision(tmp_path):
    backend_dir = os.path.dirname(os.path.dirname(__file__))
    database_path = tmp_path / "candidate-review-backfill.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    _run_alembic(backend_dir, database_url, "upgrade", "c3f5a9d1e002")

    user_id = "1" * 32
    project_id = "2" * 32
    batch_id = "3" * 32
    candidate_id = "4" * 32
    timestamp = "2026-08-03 16:00:00"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO users (
                id, email, username, hashed_password, is_admin,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (user_id, "legacy@example.com", "legacy-user", "hash", timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO projects (
                id, title, genre, status, total_chapters,
                chapter_word_count, style_intensity, owner_id,
                lore_storage_mode, lore_migration_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 10, 1000, ?, ?, ?, NULL, ?, ?)
            """,
            (
                project_id,
                "legacy-project",
                "玄幻",
                "draft",
                "standard",
                user_id,
                "legacy",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO lore_extraction_batches (
                id, project_id, requested_by, idempotency_key, source_kind,
                source_ref, source_text, source_hash, extractor_version,
                model_name, status, raw_response, error_code, error_message,
                retryable, candidate_count, lock_version, llm_started_at,
                llm_completed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, ?, NULL, NULL,
                      NULL, 0, 1, 1, NULL, NULL, ?, ?)
            """,
            (
                batch_id,
                project_id,
                user_id,
                "legacy-batch",
                "manual_text",
                "林远性格坚韧",
                "a" * 64,
                "v1",
                "completed",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO lore_extraction_candidates (
                id, project_id, batch_id, ordinal, deterministic_key,
                type_key, name, summary, payload, field_states,
                relation_suggestions, duplicate_conflict_suggestions,
                status, revision, accepted_element_id, error_code,
                created_at, updated_at
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, NULL, ?, ?)
            """,
            (
                candidate_id,
                project_id,
                batch_id,
                "b" * 64,
                "character",
                "林远",
                "旧候选",
                '{"personality": "坚韧"}',
                '{"personality": "needs_confirmation"}',
                "[]",
                "[]",
                "pending_review",
                timestamp,
                timestamp,
            ),
        )

    _run_alembic(backend_dir, database_url, "upgrade", "head")
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT revision, type_key, name, summary, payload, field_states,
                   change_kind, created_by, created_at
            FROM lore_candidate_revisions
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()
        assert row is not None
        assert row[:4] == (1, "character", "林远", "旧候选")
        assert row[4] == '{"personality": "坚韧"}'
        assert row[5] == '{"personality": "needs_confirmation"}'
        assert row[6:8] == ("extracted", user_id)
        assert row[8] == timestamp
        attention = connection.execute(
            """
            SELECT needs_attention
            FROM lore_extraction_candidates
            WHERE id = ?
            """,
            (candidate_id,),
        ).fetchone()
        assert attention == (1,)
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "worldviews",
            "setting_types",
            "setting_elements",
            "element_sources",
            "element_versions",
            "project_lore_migrations",
            "legacy_element_maps",
            "element_state_events",
            "element_relations",
            "element_relation_versions",
            "lore_extraction_batches",
            "lore_extraction_candidates",
            "lore_candidate_field_evidence",
            "lore_candidate_revisions",
            "lore_element_create_operations",
        }.issubset(tables)
        project_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(projects)")
        }
        assert {"lore_storage_mode", "lore_migration_version"}.issubset(project_columns)

        table_sql = {
            name: sql or ""
            for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table'"
            )
        }
        assert "uq_setting_type_project_id_id" in table_sql["setting_types"]
        assert "fk_setting_element_project_type" in table_sql["setting_elements"]
        assert "fk_element_source_project_element" in table_sql["element_sources"]
        assert (
            "fk_legacy_element_map_project_element"
            in table_sql["legacy_element_maps"]
        )
        assert "fk_element_relation_project_source" in table_sql["element_relations"]
        assert "fk_element_relation_project_target" in table_sql["element_relations"]
        assert "uq_element_relation_version" in table_sql["element_relation_versions"]
        assert (
            "uq_lore_extraction_project_idempotency"
            in table_sql["lore_extraction_batches"]
        )
        assert (
            "fk_lore_extraction_candidate_project_batch"
            in table_sql["lore_extraction_candidates"]
        )
        assert (
            "fk_lore_extraction_candidate_project_element"
            in table_sql["lore_extraction_candidates"]
        )
        assert (
            "uq_lore_candidate_field_evidence"
            in table_sql["lore_candidate_field_evidence"]
        )
        candidate_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(lore_extraction_candidates)"
            )
        }
        assert {
            "suggestion_resolutions",
            "user_overrides",
            "needs_attention",
        }.issubset(
            candidate_columns
        )
        assert (
            "uq_lore_extraction_candidate_accepted_element"
            in table_sql["lore_extraction_candidates"]
        )
        assert "uq_lore_candidate_revision" in table_sql["lore_candidate_revisions"]
        assert (
            "uq_lore_element_create_operation_key"
            in table_sql["lore_element_create_operations"]
        )
        assert (
            "fk_lore_element_create_operation_element"
            in table_sql["lore_element_create_operations"]
        )
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "ix_lore_candidate_project_attention_updated" in indexes
        assert "ix_lore_element_create_operations_project_created" in indexes

    _run_alembic(backend_dir, database_url, "downgrade", "a1d3c7e9f002")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "element_state_events" not in tables
        assert "element_relations" not in tables
        assert "element_relation_versions" not in tables
        assert "lore_element_create_operations" not in tables
        table_sql = {
            name: sql or ""
            for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table'"
            )
        }
        assert "fk_setting_element_project_type" not in table_sql["setting_elements"]
        assert "fk_element_source_project_element" not in table_sql["element_sources"]
        assert (
            "fk_legacy_element_map_project_element"
            not in table_sql["legacy_element_maps"]
        )

    _run_alembic(backend_dir, database_url, "upgrade", "head")

    _run_alembic(backend_dir, database_url, "downgrade", "8b87ca11f912")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "worldviews" in tables
        assert "setting_types" not in tables
        project_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(projects)")
        }
        assert "lore_storage_mode" not in project_columns

    _run_alembic(backend_dir, database_url, "upgrade", "head")
