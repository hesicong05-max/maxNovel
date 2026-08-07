"""API integration tests — test full HTTP request/response cycle.

Shared database, client, and auth fixtures are in conftest.py.
"""

import pytest

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
        resp = await client.post(
            "/api/projects",
            json={
                "title": "测试小说",
                "genre": "玄幻",
                "total_chapters": 30,
                "chapter_word_count": 3000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
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
        resp = await client.post(
            "/api/projects",
            json={
                "title": "无认证",
                "genre": "玄幻",
                "total_chapters": 30,
                "chapter_word_count": 3000,
                "style_intensity": "standard",
            },
        )
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
        await client.post(
            "/api/projects",
            json={
                "title": "小说A",
                "genre": "都市",
                "total_chapters": 20,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        await client.post(
            "/api/projects",
            json={
                "title": "小说B",
                "genre": "科幻",
                "total_chapters": 40,
                "chapter_word_count": 4000,
                "style_intensity": "intense",
            },
            headers=auth_headers,
        )
        resp = await client.get("/api/projects", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    @pytest.mark.usefixtures("clean_db")
    async def test_list_projects_only_own(
        self, client, auth_headers, second_auth_headers
    ):
        """User A creates 2 projects, User B creates 1 — each should only see their own."""
        # User A creates 2 projects
        await client.post(
            "/api/projects",
            json={
                "title": "A1",
                "genre": "都市",
                "total_chapters": 20,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        await client.post(
            "/api/projects",
            json={
                "title": "A2",
                "genre": "科幻",
                "total_chapters": 40,
                "chapter_word_count": 4000,
                "style_intensity": "intense",
            },
            headers=auth_headers,
        )
        # User B creates 1 project
        await client.post(
            "/api/projects",
            json={
                "title": "B1",
                "genre": "玄幻",
                "total_chapters": 10,
                "chapter_word_count": 1000,
                "style_intensity": "mild",
            },
            headers=second_auth_headers,
        )

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
        create_resp = await client.post(
            "/api/projects",
            json={
                "title": "获取测试",
                "genre": "武侠",
                "total_chapters": 15,
                "chapter_word_count": 2500,
                "style_intensity": "mild",
            },
            headers=auth_headers,
        )
        pid = create_resp.json()["id"]
        resp = await client.get(f"/api/projects/{pid}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["title"] == "获取测试"

    @pytest.mark.usefixtures("clean_db")
    async def test_get_nonexistent_project_404(self, client, auth_headers):
        resp = await client.get("/api/projects/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.usefixtures("clean_db")
    async def test_get_other_user_project_403(
        self, client, auth_headers, second_auth_headers
    ):
        """User A should not access User B's project."""
        create_resp = await client.post(
            "/api/projects",
            json={
                "title": "A的项目",
                "genre": "玄幻",
                "total_chapters": 10,
                "chapter_word_count": 1000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = create_resp.json()["id"]
        # User B tries to access
        resp = await client.get(f"/api/projects/{pid}", headers=second_auth_headers)
        assert resp.status_code == 403

    @pytest.mark.usefixtures("clean_db")
    async def test_update_project(self, client, auth_headers):
        create_resp = await client.post(
            "/api/projects",
            json={
                "title": "原标题",
                "genre": "玄幻",
                "total_chapters": 30,
                "chapter_word_count": 3000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = create_resp.json()["id"]
        resp = await client.put(
            f"/api/projects/{pid}",
            json={
                "title": "新标题",
                "genre": "都市",
                "total_chapters": 20,
                "chapter_word_count": 2000,
                "style_intensity": "intense",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "新标题"
        assert data["genre"] == "都市"
        assert data["total_chapters"] == 20

    @pytest.mark.usefixtures("clean_db")
    async def test_delete_project(self, client, auth_headers, tmp_path, monkeypatch):
        create_resp = await client.post(
            "/api/projects",
            json={
                "title": "删除测试",
                "genre": "玄幻",
                "total_chapters": 10,
                "chapter_word_count": 1000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = create_resp.json()["id"]
        projects_dir = tmp_path / "projects"
        staging_dir = tmp_path / "project-delete-staging"
        project_dir = projects_dir / pid
        project_dir.mkdir(parents=True)
        (project_dir / "worldview.json").write_text(
            '{"title": "删除测试"}', encoding="utf-8"
        )
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", projects_dir)
        monkeypatch.setattr(
            "app.core.project_files.PROJECT_DELETE_STAGING_DIR", staging_dir
        )

        resp = await client.delete(f"/api/projects/{pid}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["message"] == "项目已删除"
        # Verify deleted
        resp = await client.get(f"/api/projects/{pid}", headers=auth_headers)
        assert resp.status_code == 404
        assert not project_dir.exists()
        assert staging_dir.exists()
        assert list(staging_dir.iterdir()) == []

        # A repeated request has a clear, safe outcome and creates no extra archive.
        resp = await client.delete(f"/api/projects/{pid}", headers=auth_headers)
        assert resp.status_code == 404
        assert list(staging_dir.iterdir()) == []

    @pytest.mark.usefixtures("clean_db")
    async def test_delete_commit_failure_restores_project_files(
        self, client, auth_headers, tmp_path, monkeypatch
    ):
        create_resp = await client.post(
            "/api/projects",
            json={
                "title": "提交失败测试",
                "genre": "玄幻",
                "total_chapters": 10,
                "chapter_word_count": 1000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = create_resp.json()["id"]
        projects_dir = tmp_path / "projects"
        staging_dir = tmp_path / "project-delete-staging"
        project_dir = projects_dir / pid
        project_dir.mkdir(parents=True)
        (project_dir / "outline.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr("app.core.project_files.PROJECTS_DIR", projects_dir)
        monkeypatch.setattr(
            "app.core.project_files.PROJECT_DELETE_STAGING_DIR", staging_dir
        )

        from sqlalchemy.ext.asyncio import AsyncSession

        original_commit = AsyncSession.commit

        async def fail_commit(_session):
            raise RuntimeError("simulated database commit failure")

        monkeypatch.setattr(AsyncSession, "commit", fail_commit)
        resp = await client.delete(f"/api/projects/{pid}", headers=auth_headers)
        monkeypatch.setattr(AsyncSession, "commit", original_commit)

        assert resp.status_code == 500
        assert resp.json()["detail"] == "删除未完成，项目仍保留，请重试"
        assert (project_dir / "outline.json").exists()
        assert list(staging_dir.iterdir()) == []
        resp = await client.get(f"/api/projects/{pid}", headers=auth_headers)
        assert resp.status_code == 200

    @pytest.mark.usefixtures("clean_db")
    async def test_delete_archive_failure_keeps_project(
        self, client, auth_headers, monkeypatch
    ):
        create_resp = await client.post(
            "/api/projects",
            json={
                "title": "归档失败测试",
                "genre": "玄幻",
                "total_chapters": 10,
                "chapter_word_count": 1000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = create_resp.json()["id"]

        def fail_archive(_project_id):
            from app.core.project_files import ProjectFileArchiveError

            raise ProjectFileArchiveError("simulated archive failure")

        monkeypatch.setattr("app.api.projects.archive_project_files", fail_archive)
        resp = await client.delete(f"/api/projects/{pid}", headers=auth_headers)

        assert resp.status_code == 500
        assert resp.json()["detail"] == "删除未完成，项目仍保留，请重试"
        resp = await client.get(f"/api/projects/{pid}", headers=auth_headers)
        assert resp.status_code == 200

    @pytest.mark.usefixtures("clean_db")
    async def test_delete_nonexistent_404(self, client, auth_headers):
        resp = await client.delete("/api/projects/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.usefixtures("clean_db")
    async def test_create_project_missing_title_422(self, client, auth_headers):
        resp = await client.post(
            "/api/projects",
            json={
                "genre": "玄幻",
                "total_chapters": 30,
                "chapter_word_count": 3000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.usefixtures("clean_db")
    async def test_delete_other_user_project_403(
        self, client, auth_headers, second_auth_headers
    ):
        """User B should not delete User A's project."""
        create_resp = await client.post(
            "/api/projects",
            json={
                "title": "A的项目",
                "genre": "玄幻",
                "total_chapters": 10,
                "chapter_word_count": 1000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
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
        resp = await client.post(
            "/api/community/novels",
            json={
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
            },
            headers=auth_headers,
        )
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
        resp = await client.post(
            "/api/community/novels",
            json={
                "title": "无认证",
                "author_name": "作者",
                "genre": "玄幻",
                "synopsis": "简介",
                "story_outline": "梗概",
                "chapter_notes": "说明",
                "allow_cocreation": False,
                "tags": [],
                "total_chapters": 10,
                "total_words": 30000,
            },
        )
        assert resp.status_code == 401

    @pytest.mark.usefixtures("clean_db")
    async def test_get_novel_increments_views(self, client, auth_headers):
        create_resp = await client.post(
            "/api/community/novels",
            json={
                "title": "测试",
                "author_name": "作者",
                "genre": "玄幻",
                "synopsis": "简介",
                "story_outline": "梗概",
                "chapter_notes": "说明",
                "allow_cocreation": False,
                "tags": ["tag1"],
                "total_chapters": 10,
                "total_words": 30000,
            },
            headers=auth_headers,
        )
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
        create_resp = await client.post(
            "/api/community/novels",
            json={
                "title": "点赞测试",
                "author_name": "作者",
                "genre": "玄幻",
                "synopsis": "简介",
                "story_outline": "梗概",
                "chapter_notes": "说明",
                "allow_cocreation": False,
                "tags": [],
                "total_chapters": 10,
                "total_words": 30000,
            },
            headers=auth_headers,
        )
        novel_id = create_resp.json()["id"]
        resp = await client.post(f"/api/community/novels/{novel_id}/like")
        assert resp.status_code == 200
        assert resp.json()["like_count"] == 1

    @pytest.mark.usefixtures("clean_db")
    async def test_update_novel(self, client, auth_headers):
        create_resp = await client.post(
            "/api/community/novels",
            json={
                "title": "原标题",
                "author_name": "作者",
                "genre": "玄幻",
                "synopsis": "原简介",
                "story_outline": "原梗概",
                "chapter_notes": "原说明",
                "allow_cocreation": False,
                "tags": ["tag1"],
                "total_chapters": 10,
                "total_words": 30000,
            },
            headers=auth_headers,
        )
        novel_id = create_resp.json()["id"]
        resp = await client.put(
            f"/api/community/novels/{novel_id}",
            json={
                "synopsis": "新简介",
                "tags": ["tag1", "tag2", "tag3"],
                "allow_cocreation": True,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["synopsis"] == "新简介"
        assert data["allow_cocreation"] is True
        assert len(data["tags"]) == 3

    @pytest.mark.usefixtures("clean_db")
    async def test_update_other_user_novel_403(
        self, client, auth_headers, second_auth_headers
    ):
        """User B should not update User A's novel."""
        create_resp = await client.post(
            "/api/community/novels",
            json={
                "title": "A的小说",
                "author_name": "作者",
                "genre": "玄幻",
                "synopsis": "简介",
                "story_outline": "梗概",
                "chapter_notes": "说明",
                "allow_cocreation": False,
                "tags": [],
                "total_chapters": 10,
                "total_words": 30000,
            },
            headers=auth_headers,
        )
        novel_id = create_resp.json()["id"]
        resp = await client.put(
            f"/api/community/novels/{novel_id}",
            json={
                "synopsis": "篡改内容",
            },
            headers=second_auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.usefixtures("clean_db")
    async def test_delete_novel(self, client, auth_headers):
        create_resp = await client.post(
            "/api/community/novels",
            json={
                "title": "删除",
                "author_name": "作者",
                "genre": "玄幻",
                "synopsis": "简介",
                "story_outline": "梗概",
                "chapter_notes": "说明",
                "allow_cocreation": False,
                "tags": [],
                "total_chapters": 10,
                "total_words": 30000,
            },
            headers=auth_headers,
        )
        novel_id = create_resp.json()["id"]
        resp = await client.delete(
            f"/api/community/novels/{novel_id}", headers=auth_headers
        )
        assert resp.status_code == 200
        # Verify deleted
        resp = await client.get(f"/api/community/novels/{novel_id}")
        assert resp.status_code == 404

    @pytest.mark.usefixtures("clean_db")
    async def test_delete_other_user_novel_403(
        self, client, auth_headers, second_auth_headers
    ):
        """User B should not delete User A's novel."""
        create_resp = await client.post(
            "/api/community/novels",
            json={
                "title": "A的小说",
                "author_name": "作者",
                "genre": "玄幻",
                "synopsis": "简介",
                "story_outline": "梗概",
                "chapter_notes": "说明",
                "allow_cocreation": False,
                "tags": [],
                "total_chapters": 10,
                "total_words": 30000,
            },
            headers=auth_headers,
        )
        novel_id = create_resp.json()["id"]
        resp = await client.delete(
            f"/api/community/novels/{novel_id}", headers=second_auth_headers
        )
        assert resp.status_code == 403

    @pytest.mark.usefixtures("clean_db")
    async def test_list_tags(self, client, auth_headers):
        await client.post(
            "/api/community/novels",
            json={
                "title": "小说A",
                "author_name": "作者",
                "genre": "玄幻",
                "synopsis": "简介",
                "story_outline": "梗概",
                "chapter_notes": "说明",
                "allow_cocreation": False,
                "tags": ["修仙", "冒险"],
                "total_chapters": 10,
                "total_words": 30000,
            },
            headers=auth_headers,
        )
        await client.post(
            "/api/community/novels",
            json={
                "title": "小说B",
                "author_name": "作者",
                "genre": "科幻",
                "synopsis": "简介",
                "story_outline": "梗概",
                "chapter_notes": "说明",
                "allow_cocreation": False,
                "tags": ["修仙", "星际"],
                "total_chapters": 10,
                "total_words": 30000,
            },
            headers=auth_headers,
        )
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
            await client.post(
                "/api/community/novels",
                json={
                    "title": f"小说{i}",
                    "author_name": "作者",
                    "genre": "玄幻",
                    "synopsis": "简介",
                    "story_outline": "梗概",
                    "chapter_notes": "说明",
                    "allow_cocreation": False,
                    "tags": [],
                    "total_chapters": 10,
                    "total_words": 30000,
                },
                headers=auth_headers,
            )
        resp = await client.get("/api/community/novels/random?limit=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    @pytest.mark.usefixtures("clean_db")
    async def test_filter_by_tag(self, client, auth_headers):
        await client.post(
            "/api/community/novels",
            json={
                "title": "修仙文",
                "author_name": "作者",
                "genre": "仙侠",
                "synopsis": "简介",
                "story_outline": "梗概",
                "chapter_notes": "说明",
                "allow_cocreation": False,
                "tags": ["修仙"],
                "total_chapters": 10,
                "total_words": 30000,
            },
            headers=auth_headers,
        )
        await client.post(
            "/api/community/novels",
            json={
                "title": "科幻文",
                "author_name": "作者",
                "genre": "科幻",
                "synopsis": "简介",
                "story_outline": "梗概",
                "chapter_notes": "说明",
                "allow_cocreation": False,
                "tags": ["星际"],
                "total_chapters": 10,
                "total_words": 30000,
            },
            headers=auth_headers,
        )
        resp = await client.get("/api/community/novels?tag=修仙")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "修仙文"


# ─── Settings API ────────────────────────────────────────────


class TestSettingsAPI:
    @pytest.mark.usefixtures("clean_db")
    async def test_get_llm_settings(self, client, admin_headers):
        resp = await client.get("/api/settings/llm", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        # API key should be masked or empty
        assert "api_key" in data or "masked_key" in data or "configured" in data

    @pytest.mark.usefixtures("clean_db")
    async def test_get_llm_settings_without_auth_401(self, client):
        resp = await client.get("/api/settings/llm")
        assert resp.status_code == 401

    @pytest.mark.usefixtures("clean_db")
    async def test_get_llm_settings_non_admin_403(self, client, auth_headers):
        resp = await client.get("/api/settings/llm", headers=auth_headers)
        assert resp.status_code == 403

    @pytest.mark.usefixtures("clean_db")
    async def test_update_llm_settings(self, client, admin_headers):
        """Only admin users can update LLM settings."""
        resp = await client.post(
            "/api/settings/llm",
            json={
                "api_key": "sk-test-key-12345",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200

    @pytest.mark.usefixtures("clean_db")
    async def test_update_llm_settings_non_admin_403(self, client, auth_headers):
        """Non-admin users cannot update LLM settings."""
        resp = await client.post(
            "/api/settings/llm",
            json={
                "api_key": "sk-test-key-12345",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "temperature": 0.8,
                "max_tokens": 4096,
            },
            headers=auth_headers,
        )
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
        resp = await client.post(
            "/api/auth/register",
            json={
                "email": "test@example.com",
                "username": "testuser",
                "password": "test123456",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["user"]["email"] == "test@example.com"
        assert data["user"]["username"] == "testuser"
        assert data["user"]["is_admin"] is False

    @pytest.mark.usefixtures("clean_db")
    async def test_register_duplicate_email_409(self, client):
        await client.post(
            "/api/auth/register",
            json={
                "email": "dup@example.com",
                "username": "user1",
                "password": "pass123456",
            },
        )
        resp = await client.post(
            "/api/auth/register",
            json={
                "email": "dup@example.com",
                "username": "user2",
                "password": "pass123456",
            },
        )
        assert resp.status_code == 409

    @pytest.mark.usefixtures("clean_db")
    async def test_register_short_password_422(self, client):
        resp = await client.post(
            "/api/auth/register",
            json={
                "email": "short@example.com",
                "username": "user",
                "password": "123",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.usefixtures("clean_db")
    async def test_login_success(self, client):
        await client.post(
            "/api/auth/register",
            json={
                "email": "login@example.com",
                "username": "loginuser",
                "password": "pass123456",
            },
        )
        resp = await client.post(
            "/api/auth/login",
            json={
                "email": "login@example.com",
                "password": "pass123456",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["user"]["email"] == "login@example.com"

    @pytest.mark.usefixtures("clean_db")
    async def test_login_wrong_password_401(self, client):
        await client.post(
            "/api/auth/register",
            json={
                "email": "wrong@example.com",
                "username": "wronguser",
                "password": "pass123456",
            },
        )
        resp = await client.post(
            "/api/auth/login",
            json={
                "email": "wrong@example.com",
                "password": "wrongpassword",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.usefixtures("clean_db")
    async def test_login_nonexistent_user_401(self, client):
        resp = await client.post(
            "/api/auth/login",
            json={
                "email": "nobody@example.com",
                "password": "pass123456",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.usefixtures("clean_db")
    async def test_get_me_with_token(self, client):
        register_resp = await client.post(
            "/api/auth/register",
            json={
                "email": "me@example.com",
                "username": "meuser",
                "password": "pass123456",
            },
        )
        token = register_resp.json()["token"]
        resp = await client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
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
        resp = await client.get(
            "/api/auth/me", headers={"Authorization": "Bearer invalid-token-here"}
        )
        assert resp.status_code == 401


# ─── Worldview API ───────────────────────────────────────────


class TestWorldviewAPI:
    @pytest.mark.usefixtures("clean_db")
    async def test_set_worldview(self, client, auth_headers):
        """Create a project, then set its worldview."""
        proj = await client.post(
            "/api/projects",
            json={
                "title": "世界观测试",
                "genre": "玄幻",
                "total_chapters": 10,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = proj.json()["id"]
        resp = await client.post(
            f"/api/worldview/{pid}",
            json={
                "characters": [
                    {
                        "name": "林远",
                        "personality": "坚韧",
                        "background": "孤儿",
                        "motivation": "寻真相",
                        "ability": "灵觉",
                        "relations": [],
                    }
                ],
                "geography": [
                    {
                        "name": "苍澜大陆",
                        "description": "主大陆",
                        "significance": "故事舞台",
                    }
                ],
                "factions": [
                    {
                        "name": "天玄宗",
                        "stance": "正道",
                        "power_level": "顶级",
                        "relations": [],
                    }
                ],
                "power_system": [
                    {
                        "name": "灵气修炼",
                        "levels": "聚气→筑基",
                        "rules": "吸灵气",
                        "limitations": "有瓶颈",
                    }
                ],
                "history": [
                    {
                        "event": "远古大战",
                        "time": "万年前",
                        "description": "上古大战",
                        "impact": "灵气枯竭",
                    }
                ],
                "conflicts": [
                    {
                        "name": "正邪之争",
                        "type": "阵营冲突",
                        "parties": "正道vs魔道",
                        "stakes": "控制权",
                        "resolution_hint": "第三条路",
                    }
                ],
                "special_settings": [
                    {"name": "灵根", "description": "天赋", "rules": "五行灵根"}
                ],
                "source": "manual",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == pid
        assert len(data["parsed_elements"]) > 0
        assert "林远" in [c["name"] for c in data["characters"]]

    @pytest.mark.usefixtures("clean_db")
    async def test_get_worldview(self, client, auth_headers):
        """Set worldview then get it back."""
        proj = await client.post(
            "/api/projects",
            json={
                "title": "获取世界观",
                "genre": "玄幻",
                "total_chapters": 10,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = proj.json()["id"]
        await client.post(
            f"/api/worldview/{pid}",
            json={
                "characters": [
                    {
                        "name": "角色A",
                        "personality": "冷静",
                        "background": "",
                        "motivation": "",
                        "ability": "",
                        "relations": [],
                    }
                ],
                "geography": [],
                "factions": [],
                "power_system": [],
                "history": [],
                "conflicts": [],
                "special_settings": [],
                "source": "manual",
            },
            headers=auth_headers,
        )
        resp = await client.get(f"/api/worldview/{pid}", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()["characters"]) == 1

    @pytest.mark.usefixtures("clean_db")
    async def test_get_worldview_not_set_404(self, client, auth_headers):
        """Get worldview before setting should return 404."""
        proj = await client.post(
            "/api/projects",
            json={
                "title": "无世界观",
                "genre": "玄幻",
                "total_chapters": 10,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = proj.json()["id"]
        resp = await client.get(f"/api/worldview/{pid}", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.usefixtures("clean_db")
    async def test_worldview_without_auth_401(self, client, auth_headers):
        proj = await client.post(
            "/api/projects",
            json={
                "title": "权限测试",
                "genre": "玄幻",
                "total_chapters": 10,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = proj.json()["id"]
        resp = await client.post(
            f"/api/worldview/{pid}",
            json={
                "characters": [],
                "geography": [],
                "factions": [],
                "power_system": [],
                "history": [],
                "conflicts": [],
                "special_settings": [],
                "source": "manual",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.usefixtures("clean_db")
    async def test_worldview_summary(self, client, auth_headers):
        proj = await client.post(
            "/api/projects",
            json={
                "title": "摘要测试",
                "genre": "玄幻",
                "total_chapters": 10,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = proj.json()["id"]
        await client.post(
            f"/api/worldview/{pid}",
            json={
                "characters": [
                    {
                        "name": "主角",
                        "personality": "勇敢",
                        "background": "",
                        "motivation": "",
                        "ability": "",
                        "relations": [],
                    }
                ],
                "geography": [
                    {"name": "城镇", "description": "起始地", "significance": ""}
                ],
                "factions": [],
                "power_system": [],
                "history": [],
                "conflicts": [],
                "special_settings": [],
                "source": "manual",
            },
            headers=auth_headers,
        )
        resp = await client.get(f"/api/worldview/{pid}/summary", headers=auth_headers)
        assert resp.status_code == 200


# ─── Outline API ─────────────────────────────────────────────


class TestOutlineAPI:
    @pytest.mark.usefixtures("clean_db")
    async def test_generate_outline_is_retired(self, client, auth_headers):
        """The public automatic outline generator is no longer registered."""
        proj = await client.post(
            "/api/projects",
            json={
                "title": "大纲测试",
                "genre": "玄幻",
                "total_chapters": 5,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = proj.json()["id"]
        # Set worldview first
        await client.post(
            f"/api/worldview/{pid}",
            json={
                "characters": [
                    {
                        "name": "林远",
                        "personality": "坚韧",
                        "background": "",
                        "motivation": "",
                        "ability": "",
                        "relations": [],
                    }
                ],
                "geography": [{"name": "大陆", "description": "", "significance": ""}],
                "factions": [],
                "power_system": [],
                "history": [],
                "conflicts": [],
                "special_settings": [],
                "source": "manual",
            },
            headers=auth_headers,
        )
        resp = await client.post(
            f"/api/outline/{pid}/generate", headers=auth_headers
        )
        assert resp.status_code == 404

    @pytest.mark.usefixtures("clean_db")
    async def test_get_outline_not_found_404(self, client, auth_headers):
        proj = await client.post(
            "/api/projects",
            json={
                "title": "无大纲",
                "genre": "玄幻",
                "total_chapters": 5,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = proj.json()["id"]
        resp = await client.get(f"/api/outline/{pid}", headers=auth_headers)
        assert resp.status_code == 404


# ─── Chapter API ─────────────────────────────────────────────


class TestChapterAPI:
    @pytest.mark.usefixtures("clean_db")
    async def test_list_chapters_empty(self, client, auth_headers):
        """List chapters for a new project should return empty list."""
        proj = await client.post(
            "/api/projects",
            json={
                "title": "章节测试",
                "genre": "玄幻",
                "total_chapters": 5,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = proj.json()["id"]
        resp = await client.get(f"/api/chapters/{pid}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.usefixtures("clean_db")
    async def test_get_word_counts(self, client, auth_headers):
        """Get word count configuration for a project."""
        proj = await client.post(
            "/api/projects",
            json={
                "title": "字数配置",
                "genre": "玄幻",
                "total_chapters": 5,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = proj.json()["id"]
        resp = await client.get(
            f"/api/chapters/{pid}/word-counts", headers=auth_headers
        )
        assert resp.status_code == 200

    @pytest.mark.usefixtures("clean_db")
    async def test_list_chapters_without_auth_401(self, client, auth_headers):
        proj = await client.post(
            "/api/projects",
            json={
                "title": "权限测试",
                "genre": "玄幻",
                "total_chapters": 5,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = proj.json()["id"]
        resp = await client.get(f"/api/chapters/{pid}")
        assert resp.status_code == 401


# ─── Export API ──────────────────────────────────────────────


class TestExportAPI:
    @pytest.mark.usefixtures("clean_db")
    async def test_export_empty_project_txt(self, client, auth_headers):
        """Export a project with no chapters as txt."""
        proj = await client.post(
            "/api/projects",
            json={
                "title": "导出测试",
                "genre": "玄幻",
                "total_chapters": 5,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = proj.json()["id"]
        resp = await client.get(f"/api/export/{pid}/txt", headers=auth_headers)
        assert resp.status_code == 200
        assert "导出测试" in resp.text

    @pytest.mark.usefixtures("clean_db")
    async def test_export_empty_project_markdown(self, client, auth_headers):
        """Export a project with no chapters as markdown."""
        proj = await client.post(
            "/api/projects",
            json={
                "title": "MD导出",
                "genre": "玄幻",
                "total_chapters": 5,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = proj.json()["id"]
        resp = await client.get(f"/api/export/{pid}/markdown", headers=auth_headers)
        assert resp.status_code == 200
        assert "# MD导出" in resp.text

    @pytest.mark.usefixtures("clean_db")
    async def test_export_nonexistent_project_404(self, client, auth_headers):
        resp = await client.get("/api/export/nonexistent/txt", headers=auth_headers)
        assert resp.status_code == 404

    @pytest.mark.usefixtures("clean_db")
    async def test_export_without_auth_401(self, client, auth_headers):
        proj = await client.post(
            "/api/projects",
            json={
                "title": "导出权限",
                "genre": "玄幻",
                "total_chapters": 5,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = proj.json()["id"]
        resp = await client.get(f"/api/export/{pid}/txt")
        assert resp.status_code == 401

    @pytest.mark.usefixtures("clean_db")
    async def test_export_other_user_project_403(
        self, client, auth_headers, second_auth_headers
    ):
        """User B should not export User A's project."""
        proj = await client.post(
            "/api/projects",
            json={
                "title": "A的项目",
                "genre": "玄幻",
                "total_chapters": 5,
                "chapter_word_count": 2000,
                "style_intensity": "standard",
            },
            headers=auth_headers,
        )
        pid = proj.json()["id"]
        resp = await client.get(f"/api/export/{pid}/txt", headers=second_auth_headers)
        assert resp.status_code == 403
