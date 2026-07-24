"""Shared test fixtures — database, HTTP client, auth tokens.

All API integration tests use these shared fixtures to avoid
conflicting database overrides between test modules.
"""

import os
import sys
from pathlib import Path

# Ensure DEBUG mode for tests (avoids JWT_SECRET validation error)
os.environ.setdefault("DEBUG", "true")

# Ensure backend is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app


# ─── Test database (shared across all test modules) ──────────

test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with TestSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope="session", autouse=True)
async def setup_database():
    """Create tables once for the entire test session."""
    # Import all models to ensure they are registered
    from app.models import project, community, user  # noqa: F401

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest.fixture
async def client():
    """HTTP client for API testing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def clean_db():
    """Clean all tables and reset rate limiter before each test."""
    from sqlalchemy import text
    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f"DELETE FROM {table.name}"))

    from app.core.rate_limiter import limiter
    limiter.reset()

    from app.core.settings_store import SETTINGS_FILE
    if SETTINGS_FILE.exists():
        SETTINGS_FILE.unlink()

    from app.core.llm_client import llm_client
    llm_client._client = None


@pytest.fixture
async def auth_token(client):
    """Register a test user and return a Bearer token."""
    resp = await client.post("/api/auth/register", json={
        "email": "testuser@example.com",
        "username": "testuser",
        "password": "testpass123",
    })
    assert resp.status_code == 200
    return resp.json()["token"]


@pytest.fixture
async def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
async def second_auth_token(client):
    """Register a second test user and return a Bearer token (for ownership tests)."""
    resp = await client.post("/api/auth/register", json={
        "email": "user2@example.com",
        "username": "user2",
        "password": "pass123456",
    })
    assert resp.status_code == 200
    return resp.json()["token"]


@pytest.fixture
async def second_auth_headers(second_auth_token):
    return {"Authorization": f"Bearer {second_auth_token}"}


@pytest.fixture
async def admin_token(client):
    """Register a test user, promote to admin, and return a Bearer token."""
    resp = await client.post("/api/auth/register", json={
        "email": "admin@example.com",
        "username": "admin",
        "password": "adminpass123",
    })
    assert resp.status_code == 200
    token = resp.json()["token"]

    from sqlalchemy import update
    from app.models.user import User

    async with test_engine.begin() as conn:
        await conn.execute(
            update(User).where(User.email == "admin@example.com").values(is_admin=True)
        )

    return token


@pytest.fixture
async def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}
