"""DEV-017A1 migration compatibility and rollback checks."""

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
            "JWT_SECRET": "planning-migration-test-secret",
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


def _tables(connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def test_planning_migration_is_additive_and_preserves_legacy_rows(tmp_path):
    backend_dir = os.path.dirname(os.path.dirname(__file__))
    database_path = tmp_path / "planning-read-model.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    timestamp = "2026-08-10 14:10:00"
    user_id = "1" * 32
    project_id = "2" * 32
    outline_id = "3" * 32

    _run_alembic(backend_dir, database_url, "upgrade", "b4e6f8a0c013")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO users (
                id, email, username, hashed_password, is_admin,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (user_id, "planner@example.com", "planner", "hash", timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO projects (
                id, title, genre, status, total_chapters,
                chapter_word_count, style_intensity, owner_id,
                lore_storage_mode, lore_migration_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 20, 1500, ?, ?, ?, NULL, ?, ?)
            """,
            (
                project_id,
                "历史小说",
                "玄幻",
                "draft",
                "standard",
                user_id,
                "relational",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO outlines (
                id, project_id, reveal_plan, chapters, story_arc,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outline_id,
                project_id,
                "[]",
                "[]",
                "历史故事弧不得被升级脚本改写",
                timestamp,
                timestamp,
            ),
        )
        connection.commit()

    _run_alembic(backend_dir, database_url, "upgrade", "head")
    planning_tables = {
        "novel_plans",
        "planning_parts",
        "planning_chapters",
        "planning_lore_assignments",
        "planning_lore_assignment_events",
        "planning_mutation_operations",
    }
    with sqlite3.connect(database_path) as connection:
        assert planning_tables.issubset(_tables(connection))
        assert connection.execute(
            "SELECT story_arc FROM outlines WHERE id = ?", (outline_id,)
        ).fetchone() == ("历史故事弧不得被升级脚本改写",)
        assert connection.execute("SELECT COUNT(*) FROM novel_plans").fetchone() == (0,)
        table_sql = {
            name: sql or ""
            for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table'"
            )
        }
        assert "fk_planning_chapter_part" in table_sql["planning_chapters"]
        assert (
            "uq_planning_lore_assignment_target_element"
            in table_sql["planning_lore_assignments"]
        )
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "ix_planning_parts_plan_status_position" in indexes
        assert "ix_planning_chapters_part_status_position" in indexes
        assert "uq_planning_parts_active_position" in indexes
        assert "uq_planning_chapters_active_position" in indexes
        assignment_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(planning_lore_assignments)"
            )
        }
        assert {"scope_target_id", "element_content_version"}.issubset(
            assignment_columns
        )
        assert "target_key" not in assignment_columns

    _run_alembic(backend_dir, database_url, "downgrade", "b4e6f8a0c013")
    with sqlite3.connect(database_path) as connection:
        assert planning_tables.isdisjoint(_tables(connection))
        assert connection.execute(
            "SELECT story_arc FROM outlines WHERE id = ?", (outline_id,)
        ).fetchone() == ("历史故事弧不得被升级脚本改写",)

    _run_alembic(backend_dir, database_url, "upgrade", "head")
    with sqlite3.connect(database_path) as connection:
        assert planning_tables.issubset(_tables(connection))
        assert connection.execute(
            "SELECT story_arc FROM outlines WHERE id = ?", (outline_id,)
        ).fetchone() == ("历史故事弧不得被升级脚本改写",)


def test_generation_preflight_migration_is_additive_and_reversible(tmp_path):
    backend_dir = os.path.dirname(os.path.dirname(__file__))
    database_path = tmp_path / "generation-preflight.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    timestamp = "2026-08-11 14:30:00"
    user_id = "a" * 32
    project_id = "b" * 32
    outline_id = "c" * 32
    memory_id = "d" * 32
    plan_id = "e" * 32
    part_id = "f" * 32
    chapter_id = "1" * 32
    run_id = "2" * 32

    _run_alembic(backend_dir, database_url, "upgrade", "c5f7a9b1d014")
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO users (
                id, email, username, hashed_password, is_admin,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (user_id, "writer@example.com", "writer", "hash", timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO projects (
                id, title, genre, status, total_chapters,
                chapter_word_count, style_intensity, owner_id,
                lore_storage_mode, lore_migration_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 20, 1500, ?, ?, ?, NULL, ?, ?)
            """,
            (
                project_id,
                "生成准备迁移验证",
                "玄幻",
                "draft",
                "standard",
                user_id,
                "relational",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO outlines (
                id, project_id, reveal_plan, chapters, story_arc,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outline_id,
                project_id,
                "[]",
                "[]",
                "旧大纲不得被 B1a 改写",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO story_memories (
                id, project_id, revealed_elements, character_states,
                foreshadows, timeline, chapter_summaries, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                project_id,
                "[]",
                '{"hero":{"status":"ready"}}',
                "[]",
                "[]",
                "[]",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO novel_plans (
                id, project_id, status, structure_version,
                assignment_version, created_at, updated_at
            ) VALUES (?, ?, 'active', 3, 1, ?, ?)
            """,
            (plan_id, project_id, timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO planning_parts (
                id, project_id, plan_id, title, description, position,
                status, lock_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 1, 'active', 1, ?, ?)
            """,
            (part_id, project_id, plan_id, "第一篇", "旧篇章", timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO planning_chapters (
                id, project_id, plan_id, part_id, title, summary,
                target_word_count, position, status, lock_version,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1800, 1, 'active', 1, ?, ?)
            """,
            (
                chapter_id,
                project_id,
                plan_id,
                part_id,
                "第一章",
                "原有章节摘要",
                timestamp,
                timestamp,
            ),
        )
        connection.commit()

    _run_alembic(backend_dir, database_url, "upgrade", "d6a8c0e2f015")
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert "chapter_generation_runs" in _tables(connection)
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            ("chapter_generation_runs",),
        ).fetchone()[0]
        for constraint in (
            "uq_generation_run_operation_key",
            "fk_generation_run_plan",
            "fk_generation_run_chapter",
            "ck_generation_run_preflight_only",
            "ck_generation_run_context_size",
        ):
            assert constraint in table_sql
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert {
            "ix_chapter_generation_runs_project_id",
            "ix_chapter_generation_runs_plan_id",
            "ix_chapter_generation_runs_planning_chapter_id",
            "ix_chapter_generation_runs_requested_by",
            "ix_generation_runs_chapter_created",
        }.issubset(indexes)
        assert connection.execute(
            "SELECT story_arc FROM outlines WHERE id=?", (outline_id,)
        ).fetchone() == ("旧大纲不得被 B1a 改写",)
        assert connection.execute(
            "SELECT character_states FROM story_memories WHERE id=?", (memory_id,)
        ).fetchone() == ('{"hero":{"status":"ready"}}',)
        assert connection.execute(
            "SELECT title, summary FROM planning_chapters WHERE id=?", (chapter_id,)
        ).fetchone() == ("第一章", "原有章节摘要")
        connection.execute(
            """
            INSERT INTO chapter_generation_runs (
                id, project_id, plan_id, planning_chapter_id, requested_by,
                operation_key, request_fingerprint, status, execution_mode,
                ai_invoked, billing_effect, structure_version,
                assignment_version, chapter_lock_version,
                context_schema_version, context_manifest, context_checksum,
                context_size_bytes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared', 'preflight_only',
                      0, 'none', 3, 1, 1, 1, ?, ?, 19, ?, ?)
            """,
            (
                run_id,
                project_id,
                plan_id,
                chapter_id,
                user_id,
                "migration-run-0001",
                "3" * 64,
                '{"seed":"preserved"}',
                "4" * 64,
                timestamp,
                timestamp,
            ),
        )
        connection.commit()

    _run_alembic(backend_dir, database_url, "downgrade", "c5f7a9b1d014")
    with sqlite3.connect(database_path) as connection:
        assert "chapter_generation_runs" not in _tables(connection)
        assert connection.execute(
            "SELECT story_arc FROM outlines WHERE id=?", (outline_id,)
        ).fetchone() == ("旧大纲不得被 B1a 改写",)
        assert connection.execute(
            "SELECT character_states FROM story_memories WHERE id=?", (memory_id,)
        ).fetchone() == ('{"hero":{"status":"ready"}}',)
        assert connection.execute(
            "SELECT title, summary FROM planning_chapters WHERE id=?", (chapter_id,)
        ).fetchone() == ("第一章", "原有章节摘要")

    _run_alembic(backend_dir, database_url, "upgrade", "d6a8c0e2f015")
    with sqlite3.connect(database_path) as connection:
        assert "chapter_generation_runs" in _tables(connection)
        assert connection.execute(
            "SELECT COUNT(*) FROM chapter_generation_runs"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT title, summary FROM planning_chapters WHERE id=?", (chapter_id,)
        ).fetchone() == ("第一章", "原有章节摘要")
