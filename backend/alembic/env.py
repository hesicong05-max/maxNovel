"""Alembic environment configuration.

Supports both online (direct DB connection) and offline (SQL generation) modes.
Imports all models from app.models to ensure they are registered on Base.metadata.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

# Add the backend directory to sys.path so we can import app modules
backend_dir = str(Path(__file__).resolve().parent.parent)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from sqlalchemy import engine_from_config, pool

from alembic import context

# ─── Import application models ──────────────────────────────
# This ensures all tables are registered on Base.metadata
from app.config import settings
from app.database import Base

# Import all models so Alembic can detect them
from app.models import community, generation, lore, planning, project, user  # noqa: F401

# ─── Alembic config ────────────────────────────────────────
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set the target metadata for autogenerate
target_metadata = Base.metadata

# Convert async DB URL to sync for Alembic migrations
# e.g. "sqlite+aiosqlite:///./data/novel_agent.db" → "sqlite:///./data/novel_agent.db"
db_url = settings.DATABASE_URL
db_url = db_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")

config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This generates SQL scripts without connecting to the database.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Creates an Engine and associates a connection with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
