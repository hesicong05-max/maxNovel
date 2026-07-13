from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


# SQLite doesn't support pool_size/max_overflow; PostgreSQL/MySQL do
_engine_kwargs: dict = {"echo": settings.DEBUG}
if "sqlite" not in settings.DATABASE_URL:
    _engine_kwargs.update(
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


async def init_db():
    """Initialize database tables.

    In development (DEBUG=True), use create_all for convenience.
    In production, rely on Alembic migrations (run `alembic upgrade head` during deployment).
    """
    if settings.DEBUG:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    # Production: tables are created by Alembic migrations
