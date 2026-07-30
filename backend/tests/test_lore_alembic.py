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
        }.issubset(tables)
        project_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(projects)")
        }
        assert {"lore_storage_mode", "lore_migration_version"}.issubset(project_columns)

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
