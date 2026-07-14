"""API integration tests — test full HTTP request/response cycle."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.database import Base, get_db
from app.main import app


# ─── Test database setup ──────────────────────────────────────

test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with TestSessionLocal() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope="module", autouse=True)
async def setup_database():
    """Create tables once for all tests in this module."""
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

    # Reset rate limiter state to prevent cross-test interference
    from app.core.rate_limiter import limiter
    limiter.reset()


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
    """Return Authorization headers for an authenticated test user."""
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
    """Return Authorization headers for a second test user."""
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

    # Promote the user to admin directly in the test database
    from sqlalchemy import update
    from app.models.user import User

    async with test_engine.begin() as conn:
        await conn.execute(
            update(User).where(User.email == "admin@example.com").values(is_admin=True)
        )

    return token


@pytest.fixture
async def admin_headers(admin_token):
    """Return Authorization headers for an admin test user."""
    return {"Authorization": f"Bearer {admin_token}"}


# ─── Health check ─────────────────────────────────────────────

class TestHealthCheck:
    @pytest.mark.usefixtures("clean_db")
    async def test_health_returns_ok(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    @pytest.mark.usefixtures("clean_db")
    async def test_security_headers_present(self, client):
        resp = await client.get("/api/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "X-Request-ID" in resp.headers


# ─── Projects CRUD ───────────────────────────────────────────

class TestProjectsAPI:
    @pytest.mark.usefixtures("clean_db")
    async def test_create_project(self, client, auth_headers):
        resp = await client.post("/api/projects", json={
            "title": "测试小说",
            "genre": "玄幻",
            "total_chapters": 30,
            "chapter_word_count": 3000,
            "style_intensity": "standard",
        }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "测试小说"
        assert data["genre"] == "玄幻"
        assert data["total_chapters"] == 30
        assert data["status"] == "draft"
        assert data["has_worldview"] is False
        assert data["has_outline"] is False
        assert data["chapter_count"] == 0

    @pytest.mark.usefixtures("clean_db")
    async def test_create_project_without_auth_401(self, client):
        resp = await client.post("/api/projects", json={
            "title": "无认证", "genre": "玄幻", "total_chapters": 30,
            "chapter_word_count": 3000, "style_intensity": "standard",
        })
        assert resp.status_code == 401

    @pytest.mark.usefixtures("clean_db")
    async def test_list_projects_empty(self, client, auth_headers):
        resp = await client.get("/api/projects", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.usefixtures("clean_db")
    async def test_list_projects_without_auth_401(self, client):
        resp = await client.get("/api/projects")
        assert resp.status_code == 401

    @pytest.mark.usefixtures("clean_db")
    async def test_list_projects_after_create(self, client, auth_headers):
        await client.post("/api/projects", json={
            "title": "小说A", "genre": "都市", "total_chapters": 20, "chapter_word_count": 2000, "style_intensity": "standard",
        }, headers=auth_headers)
        await client.post("/api/projects", json={
            "title": "小说B", "genre": "科幻", "total_chapters": 40, "chapter_word_count": 4000, "style_intensity": "intense",
        }, headers=auth_headers)
        resp = await client.get("/api/projects", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    @pytest.mark.usefixtures("clean_db")
    async def test_list_projects_only_own(self, client, auth_headers, second_auth_headers):
        """User A creates 2 projects, User B creates 1 — each should only see their own."""
        # User A creates 2 projects
        await client.post("/api/projects", json={
            "title": "A1", "genre": "都市", "total_chapters": 20, "chapter_word_count": 2000, "style_intensity": "standard",
        }, headers=auth_headers)
        await client.post("/api/projects", json={
            "title": "A2", "genre": "科幻", "total_chapters": 40, "chapter_word_count": 4000, "style_intensity": "intense",
        }, headers=auth_headers)
        # User B creates 1 project
        await client.post("/api/projects", json={
            "title": "B1", "genre": "玄幻", "total_chapters": 10, "chapter_word_count": 1000, "style_intensity": "mild",
        }, headers=second_auth_headers)

        # User A should see only their 2 projects
        resp_a = await client.get("/api/projects", headers=auth_headers)
        assert resp_a.status_code == 200
        assert len(resp_a.json()) == 2

        # User B should see only their 1 project
        resp_b = await client.get("/api/projects", headers=second_auth_headers)
        assert resp_b.status_code == 200
        assert len(resp_b.json()) == 1
        assert resp_b.json()[0]["title"] == "B1"

    @pytest.mark.usefixtures("clean_db")
    async def test_get_project_by_id(self, client, auth_headers):
        create_resp = await client.post("/api/projects", json={
            "title": "获取测试", "genre": "武侠", "total_chapters": 15, "chapter_word_count": 2500, "style_intensity": "mild",
        }, headers=auth_headers)
        pid = create_resp.json()["id"]
        resp = await client.get(f"/api/projects/{pid}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["title"] == "获取测试"

    @pytest.mark.usefixtures("clean_db")
    async def test_get_nonexistent_project_404(self, client, auth_headers):
        resp = await client.get("/api/projects/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.usefixtures("clean_db")
    async def test_get_other_user_project_403(self, client, auth_headers, second_auth_headers):
        """User A should not access User B's project."""
        create_resp = await client.post("/api/projects", json={
            "title": "A的项目", "genre": "玄幻", "total_chapters": 10, "chapter_word_count": 1000, "style_intensity": "standard",
        }, headers=auth_headers)
        pid = create_resp.json()["id"]
        # User B tries to access
        resp = await client.get(f"/api/projects/{pid}", headers=second_auth_headers)
        assert resp.status_code == 403

    @pytest.mark.usefixtures("clean_db")
    async def test_update_project(self, client, auth_headers):
        create_resp = await client.post("/api/projects", json={
            "title": "原标题", "genre": "玄幻", "total_chapters": 30, "chapter_word_count": 3000, "style_intensity": "standard",
        }, headers=auth_headers)
        pid = create_resp.json()["id"]
        resp = await client.put(f"/api/projects/{pid}", json={
            "title": "新标题", "genre": "都市", "total_chapters": 20, "chapter_word_count": 2000, "style_intensity": "intense",
        }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "新标题"
        assert data["genre"] == "都市"
        assert data["total_chapters"] == 20

    @pytest.mark.usefixtures("clean_db")
    async def test_delete_project(self, client, auth_headers):
        create_resp = await client.post("/api/projects", json={
            "title": "删除测试", "genre": "玄幻", "total_chapters": 10, "chapter_word_count": 1000, "style_intensity": "standard",
        }, headers=auth_headers)
        pid = create_resp.json()["id"]
        resp = await client.delete(f"/api/projects/{pid}", headers=auth_headers)
        assert resp.status_code == 200
        # Verify deleted
        resp = await client.get(f"/api/projects/{pid}", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.usefixtures("clean_db")
    async def test_delete_nonexistent_404(self, client, auth_headers):
        resp = await client.delete("/api/projects/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.usefixtures("clean_db")
    async def test_create_project_missing_title_422(self, client, auth_headers):
        resp = await client.post("/api/projects", json={
            "genre": "玄幻", "total_chapters": 30, "chapter_word_count": 3000, "style_intensity": "standard",
        }, headers=auth_headers)
        assert resp.status_code == 422

    @pytest.mark.usefixtures("clean_db")
    async def test_delete_other_user_project_403(self, client, auth_headers, second_auth_headers):
        """User B should not delete User A's project."""
        create_resp = await client.post("/api/projects", json={
            "title": "A的项目", "genre": "玄幻", "total_chapters": 10, "chapter_word_count": 1000, "style_intensity": "standard",
        }, headers=auth_headers)
        pid = create_resp.json()["id"]
        resp = await client.delete(f"/api/projects/{pid}", headers=second_auth_headers)
        assert resp.status_code == 403


# ─── Community API ───────────────────────────────────────────

class TestCommunityAPI:
    @pytest.mark.usefixtures("clean_db")
    async def test_list_novels_empty(self, client):
        """Public: list novels without auth."""
        resp = await client.get("/api/community/novels")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.usefixtures("clean_db")
    async def test_create_novel(self, client, auth_headers):
        resp = await client.post("/api/community/novels", json={
            "title": "星辰大海",
            "author_name": "测试作者",
            "genre": "科幻",
            "synopsis": "星际探险故事",
            "story_outline": "主角穿越虫洞",
            "chapter_notes": "第一章启航",
            "allow_cocreation": True,
            "tags": ["星际", "冒险"],
            "total_chapters": 30,
            "total_words": 90000,
        }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "星辰大海"
        assert data["author_name"] == "测试作者"
        assert data["genre"] == "科幻"
        assert data["allow_cocreation"] is True
        assert "星际" in data["tags"]
        assert data["view_count"] == 0
        assert data["like_count"] == 0

    @pytest.mark.usefixtures("clean_db")
    async def test_create_novel_without_auth_401(self, client):
        resp = await client.post("/api/community/novels", json={
            "title": "无认证", "author_name": "作者", "genre": "玄幻",
            "synopsis": "简介", "story_outline": "梗概", "chapter_notes": "说明",
            "allow_cocreation": False, "tags": [], "total_chapters": 10, "total_words": 30000,
        })
        assert resp.status_code == 401

    @pytest.mark.usefixtures("clean_db")
    async def test_get_novel_increments_views(self, client, auth_headers):
        create_resp = await client.post("/api/community/novels", json={
            "title": "测试", "author_name": "作者", "genre": "玄幻",
            "synopsis": "简介", "story_outline": "梗概", "chapter_notes": "说明",
            "allow_cocreation": False, "tags": ["tag1"], "total_chapters": 10, "total_words": 30000,
        }, headers=auth_headers)
        novel_id = create_resp.json()["id"]
        # Initial view (public, no auth needed)
        resp = await client.get(f"/api/community/novels/{novel_id}")
        assert resp.status_code == 200
        assert resp.json()["view_count"] == 1
        # Second view from same IP — dedup logic prevents increment within TTL
        resp = await client.get(f"/api/community/novels/{novel_id}")
        assert resp.json()["view_count"] == 1  # still 1 due to dedup
        # Clear dedup cache and verify second view increments
        from app.api.community import _view_cache
        _view_cache.clear()
        resp = await client.get(f"/api/community/novels/{novel_id}")
        assert resp.json()["view_count"] == 2

    @pytest.mark.usefixtures("clean_db")
    async def test_get_nonexistent_novel_404(self, client):
        resp = await client.get("/api/community/novels/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.usefixtures("clean_db")
    async def test_like_novel(self, client, auth_headers):
        create_resp = await client.post("/api/community/novels", json={
            "title": "点赞测试", "author_name": "作者", "genre": "玄幻",
            "synopsis": "简介", "story_outline": "梗概", "chapter_notes": "说明",
            "allow_cocreation": False, "tags": [], "total_chapters": 10, "total_words": 30000,
        }, headers=auth_headers)
        novel_id = create_resp.json()["id"]
        resp = await client.post(f"/api/community/novels/{novel_id}/like")
        assert resp.status_code == 200
        assert resp.json()["like_count"] == 1

    @pytest.mark.usefixtures("clean_db")
    async def test_update_novel(self, client, auth_headers):
        create_resp = await client.post("/api/community/novels", json={
            "title": "原标题", "author_name": "作者", "genre": "玄幻",
            "synopsis": "原简介", "story_outline": "原梗概", "chapter_notes": "原说明",
            "allow_cocreation": False, "tags": ["tag1"], "total_chapters": 10, "total_words": 30000,
        }, headers=auth_headers)
        novel_id = create_resp.json()["id"]
        resp = await client.put(f"/api/community/novels/{novel_id}", json={
            "synopsis": "新简介",
            "tags": ["tag1", "tag2", "tag3"],
            "allow_cocreation": True,
        }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["synopsis"] == "新简介"
        assert data["allow_cocreation"] is True
        assert len(data["tags"]) == 3

    @pytest.mark.usefixtures("clean_db")
    async def test_update_other_user_novel_403(self, client, auth_headers, second_auth_headers):
        """User B should not update User A's novel."""
        create_resp = await client.post("/api/community/novels", json={
            "title": "A的小说", "author_name": "作者", "genre": "玄幻",
            "synopsis": "简介", "story_outline": "梗概", "chapter_notes": "说明",
            "allow_cocreation": False, "tags": [], "total_chapters": 10, "total_words": 30000,
        }, headers=auth_headers)
        novel_id = create_resp.json()["id"]
        resp = await client.put(f"/api/community/novels/{novel_id}", json={
            "synopsis": "篡改内容",
        }, headers=second_auth_headers)
        assert resp.status_code == 403

    @pytest.mark.usefixtures("clean_db")
    async def test_delete_novel(self, client, auth_headers):
        create_resp = await client.post("/api/community/novels", json={
            "title": "删除", "author_name": "作者", "genre": "玄幻",
            "synopsis": "简介", "story_outline": "梗概", "chapter_notes": "说明",
            "allow_cocreation": False, "tags": [], "total_chapters": 10, "total_words": 30000,
        }, headers=auth_headers)
        novel_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/community/novels/{novel_id}", headers=auth_headers)
        assert resp.status_code == 200
        # Verify deleted
        resp = await client.get(f"/api/community/novels/{novel_id}")
        assert resp.status_code == 404

    @pytest.mark.usefixtures("clean_db")
    async def test_delete_other_user_novel_403(self, client, auth_headers, second_auth_headers):
        """User B should not delete User A's novel."""
        create_resp = await client.post("/api/community/novels", json={
            "title": "A的小说", "author_name": "作者", "genre": "玄幻",
            "synopsis": "简介", "story_outline": "梗概", "chapter_notes": "说明",
            "allow_cocreation": False, "tags": [], "total_chapters": 10, "total_words": 30000,
        }, headers=auth_headers)
        novel_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/community/novels/{novel_id}", headers=second_auth_headers)
        assert resp.status_code == 403

    @pytest.mark.usefixtures("clean_db")
    async def test_list_tags(self, client, auth_headers):
        await client.post("/api/community/novels", json={
            "title": "小说A", "author_name": "作者", "genre": "玄幻",
            "synopsis": "简介", "story_outline": "梗概", "chapter_notes": "说明",
            "allow_cocreation": False, "tags": ["修仙", "冒险"], "total_chapters": 10, "total_words": 30000,
        }, headers=auth_headers)
        await client.post("/api/community/novels", json={
            "title": "小说B", "author_name": "作者", "genre": "科幻",
            "synopsis": "简介", "story_outline": "梗概", "chapter_notes": "说明",
            "allow_cocreation": False, "tags": ["修仙", "星际"], "total_chapters": 10, "total_words": 30000,
        }, headers=auth_headers)
        resp = await client.get("/api/community/tags")
        assert resp.status_code == 200
        data = resp.json()
        tag_names = [t["name"] for t in data]
        assert "修仙" in tag_names
        assert "冒险" in tag_names
        assert "星际" in tag_names
        # 修仙 should have usage_count >= 2
        xiuxian_tag = next(t for t in data if t["name"] == "修仙")
        assert xiuxian_tag["usage_count"] >= 2

    @pytest.mark.usefixtures("clean_db")
    async def test_random_novels(self, client, auth_headers):
        for i in range(3):
            await client.post("/api/community/novels", json={
                "title": f"小说{i}", "author_name": "作者", "genre": "玄幻",
                "synopsis": "简介", "story_outline": "梗概", "chapter_notes": "说明",
                "allow_cocreation": False, "tags": [], "total_chapters": 10, "total_words": 30000,
            }, headers=auth_headers)
        resp = await client.get("/api/community/novels/random?limit=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    @pytest.mark.usefixtures("clean_db")
    async def test_filter_by_tag(self, client, auth_headers):
        await client.post("/api/community/novels", json={
            "title": "修仙文", "author_name": "作者", "genre": "仙侠",
            "synopsis": "简介", "story_outline": "梗概", "chapter_notes": "说明",
            "allow_cocreation": False, "tags": ["修仙"], "total_chapters": 10, "total_words": 30000,
        }, headers=auth_headers)
        await client.post("/api/community/novels", json={
            "title": "科幻文", "author_name": "作者", "genre": "科幻",
            "synopsis": "简介", "story_outline": "梗概", "chapter_notes": "说明",
            "allow_cocreation": False, "tags": ["星际"], "total_chapters": 10, "total_words": 30000,
        }, headers=auth_headers)
        resp = await client.get("/api/community/novels?tag=修仙")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "修仙文"


# ─── Settings API ────────────────────────────────────────────

class TestSettingsAPI:
    @pytest.mark.usefixtures("clean_db")
    async def test_get_llm_settings(self, client, auth_headers):
        resp = await client.get("/api/settings/llm", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        # API key should be masked or empty
        assert "api_key" in data or "masked_key" in data or "configured" in data

    @pytest.mark.usefixtures("clean_db")
    async def test_get_llm_settings_without_auth_401(self, client):
        resp = await client.get("/api/settings/llm")
        assert resp.status_code == 401

    @pytest.mark.usefixtures("clean_db")
    async def test_update_llm_settings(self, client, admin_headers):
        """Only admin users can update LLM settings."""
        resp = await client.post("/api/settings/llm", json={
            "api_key": "sk-test-key-12345",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o",
            "temperature": 0.8,
            "max_tokens": 4096,
        }, headers=admin_headers)
        assert resp.status_code == 200

    @pytest.mark.usefixtures("clean_db")
    async def test_update_llm_settings_non_admin_403(self, client, auth_headers):
        """Non-admin users cannot update LLM settings."""
        resp = await client.post("/api/settings/llm", json={
            "api_key": "sk-test-key-12345",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o",
            "temperature": 0.8,
            "max_tokens": 4096,
        }, headers=auth_headers)
        assert resp.status_code == 403

    @pytest.mark.usefixtures("clean_db")
    async def test_get_providers_public(self, client):
        """Providers endpoint is public (static data)."""
        resp = await client.get("/api/settings/llm/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert len(data["providers"]) > 0


# ─── Auth API ───────────────────────────────────────────────

class TestAuthAPI:
    @pytest.mark.usefixtures("clean_db")
    async def test_register(self, client):
        resp = await client.post("/api/auth/register", json={
            "email": "test@example.com",
            "username": "testuser",
            "password": "test123456",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["user"]["email"] == "test@example.com"
        assert data["user"]["username"] == "testuser"

    @pytest.mark.usefixtures("clean_db")
    async def test_register_duplicate_email_409(self, client):
        await client.post("/api/auth/register", json={
            "email": "dup@example.com", "username": "user1", "password": "pass123456",
        })
        resp = await client.post("/api/auth/register", json={
            "email": "dup@example.com", "username": "user2", "password": "pass123456",
        })
        assert resp.status_code == 409

    @pytest.mark.usefixtures("clean_db")
    async def test_register_short_password_422(self, client):
        resp = await client.post("/api/auth/register", json={
            "email": "short@example.com", "username": "user", "password": "123",
        })
        assert resp.status_code == 422

    @pytest.mark.usefixtures("clean_db")
    async def test_login_success(self, client):
        await client.post("/api/auth/register", json={
            "email": "login@example.com", "username": "loginuser", "password": "pass123456",
        })
        resp = await client.post("/api/auth/login", json={
            "email": "login@example.com", "password": "pass123456",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["user"]["email"] == "login@example.com"

    @pytest.mark.usefixtures("clean_db")
    async def test_login_wrong_password_401(self, client):
        await client.post("/api/auth/register", json={
            "email": "wrong@example.com", "username": "wronguser", "password": "pass123456",
        })
        resp = await client.post("/api/auth/login", json={
            "email": "wrong@example.com", "password": "wrongpassword",
        })
        assert resp.status_code == 401

    @pytest.mark.usefixtures("clean_db")
    async def test_login_nonexistent_user_401(self, client):
        resp = await client.post("/api/auth/login", json={
            "email": "nobody@example.com", "password": "pass123456",
        })
        assert resp.status_code == 401

    @pytest.mark.usefixtures("clean_db")
    async def test_get_me_with_token(self, client):
        register_resp = await client.post("/api/auth/register", json={
            "email": "me@example.com", "username": "meuser", "password": "pass123456",
        })
        token = register_resp.json()["token"]
        resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "me@example.com"
        assert data["username"] == "meuser"

    @pytest.mark.usefixtures("clean_db")
    async def test_get_me_without_token_401(self, client):
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401

    @pytest.mark.usefixtures("clean_db")
    async def test_get_me_with_invalid_token_401(self, client):
        resp = await client.get("/api/auth/me", headers={"Authorization": "Bearer invalid-token-here"})
        assert resp.status_code == 401
