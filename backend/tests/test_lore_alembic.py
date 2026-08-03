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
            "element_state_events",
            "element_relations",
            "element_relation_versions",
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
