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
