"""Compatibility tests for retired outline generation and historical reads."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.project import Outline, Project, StoryMemory


async def _create_project_with_worldview(
    client,
    auth_headers,
    total_chapters: int = 5,
) -> str:
    project_response = await client.post(
        "/api/projects",
        json={
            "title": "历史章节安排兼容测试",
            "genre": "玄幻",
            "total_chapters": total_chapters,
            "chapter_word_count": 2000,
            "style_intensity": "standard",
        },
        headers=auth_headers,
    )
    project_id = project_response.json()["id"]
    await client.post(
        f"/api/worldview/{project_id}",
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
            "geography": [
                {"name": "大陆", "description": "", "significance": ""}
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
    return project_id


class TestRetiredOutlinePublicAPI:
    @pytest.mark.usefixtures("clean_db")
    async def test_only_legacy_read_route_remains_in_openapi(self, client):
        paths = (await client.get("/openapi.json")).json()["paths"]

        assert "/api/outline/{project_id}" in paths
        assert set(paths["/api/outline/{project_id}"]) == {"get"}
        assert "/api/outline/{project_id}/generate" not in paths
        assert "/api/outline/{project_id}/generate-stream" not in paths
        assert "/api/outline/{project_id}/diagnose" not in paths
        assert "/api/outline/{project_id}/confirm" not in paths

    @pytest.mark.usefixtures("clean_db")
    async def test_retired_routes_do_not_call_llm_or_write_legacy_state(
        self,
        client,
        auth_headers,
        db_session,
    ):
        project_id = await _create_project_with_worldview(client, auth_headers)
        project_before = await db_session.get(Project, project_id)
        assert project_before is not None
        status_before = project_before.status
        payload = {"story_arc": "不应写入", "chapters": []}

        with (
            patch(
                "app.core.llm_client.llm_client.chat",
                new_callable=AsyncMock,
            ) as chat,
            patch("app.core.llm_client.llm_client.chat_stream") as chat_stream,
        ):
            responses = [
                await client.post(
                    f"/api/outline/{project_id}/generate",
                    headers=auth_headers,
                ),
                await client.post(
                    f"/api/outline/{project_id}/generate-stream",
                    headers=auth_headers,
                ),
                await client.get(
                    f"/api/outline/{project_id}/diagnose",
                    headers=auth_headers,
                ),
                await client.put(
                    f"/api/outline/{project_id}",
                    json=payload,
                    headers=auth_headers,
                ),
                await client.post(
                    f"/api/outline/{project_id}/confirm",
                    headers=auth_headers,
                ),
            ]

        assert [response.status_code for response in responses] == [
            404,
            404,
            404,
            405,
            404,
        ]
        chat.assert_not_awaited()
        chat_stream.assert_not_called()

        outline_result = await db_session.execute(
            select(Outline).where(Outline.project_id == project_id)
        )
        assert outline_result.scalar_one_or_none() is None
        memory_result = await db_session.execute(
            select(StoryMemory).where(StoryMemory.project_id == project_id)
        )
        assert memory_result.scalar_one_or_none() is None
        await db_session.refresh(project_before)
        assert project_before.status == status_before


class TestHistoricalOutlineRead:
    @pytest.mark.usefixtures("clean_db")
    async def test_owner_can_read_historical_outline(
        self,
        client,
        auth_headers,
        db_session,
    ):
        project_id = await _create_project_with_worldview(
            client,
            auth_headers,
            total_chapters=1,
        )
        db_session.add(
            Outline(
                project_id=project_id,
                story_arc="旧规划原地保留",
                chapters=[
                    {
                        "chapter_num": 1,
                        "title": "旧章节",
                        "summary": "旧摘要",
                        "key_events": [],
                        "reveal_elements": [],
                    }
                ],
                reveal_plan=[],
            )
        )
        await db_session.commit()

        response = await client.get(
            f"/api/outline/{project_id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["story_arc"] == "旧规划原地保留"
        assert response.json()["chapters"][0]["title"] == "旧章节"

    @pytest.mark.usefixtures("clean_db")
    async def test_historical_read_enforces_authentication_and_ownership(
        self,
        client,
        auth_headers,
        second_auth_headers,
        db_session,
    ):
        project_id = await _create_project_with_worldview(client, auth_headers)
        db_session.add(
            Outline(
                project_id=project_id,
                story_arc="私有历史规划",
                chapters=[],
                reveal_plan=[],
            )
        )
        await db_session.commit()

        unauthenticated = await client.get(f"/api/outline/{project_id}")
        other_user = await client.get(
            f"/api/outline/{project_id}",
            headers=second_auth_headers,
        )

        assert unauthenticated.status_code == 401
        assert other_user.status_code == 403

    @pytest.mark.usefixtures("clean_db")
    async def test_missing_history_points_to_second_stage_planner(
        self,
        client,
        auth_headers,
    ):
        project_id = await _create_project_with_worldview(client, auth_headers)

        response = await client.get(
            f"/api/outline/{project_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert response.json()["detail"] == (
            "未找到历史章节安排；新章节规划将在第二阶段开放"
        )

    @pytest.mark.usefixtures("clean_db")
    async def test_missing_history_blocks_chapter_llm_before_generation(
        self,
        client,
        auth_headers,
    ):
        project_id = await _create_project_with_worldview(client, auth_headers)

        with patch("app.api.chapters.llm_client.chat_stream") as chat_stream:
            response = await client.post(
                f"/api/chapters/{project_id}/1/generate",
                headers=auth_headers,
            )

        assert response.status_code == 200
        assert "未检测到可用的历史章节安排" in response.text
        chat_stream.assert_not_called()

    @pytest.mark.usefixtures("clean_db")
    async def test_corrupt_historical_lists_degrade_without_mutation(
        self,
        client,
        auth_headers,
        db_session,
    ):
        project_id = await _create_project_with_worldview(client, auth_headers)
        outline = Outline(
            project_id=project_id,
            story_arc="历史规划仍保留",
            chapters="{invalid",
            reveal_plan="[invalid",
        )
        db_session.add(outline)
        await db_session.commit()
        stored_chapters = outline.chapters
        stored_reveal_plan = outline.reveal_plan

        response = await client.get(
            f"/api/outline/{project_id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["chapters"] == []
        assert response.json()["reveal_plan"] == []
        await db_session.refresh(outline)
        assert outline.chapters == stored_chapters
        assert outline.reveal_plan == stored_reveal_plan
