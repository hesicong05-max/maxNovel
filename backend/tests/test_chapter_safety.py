"""Regression tests for chapter generation data-integrity safeguards."""

import json
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.api.chapters import (
    _active_generation_projects,
    _chapter_output_token_budget,
    _stream_single_chapter,
)
from app.core.llm_client import llm_client
from app.models.project import Chapter, Outline, Project, ProjectStatus, StoryMemory, Worldview
from tests.conftest import TestSessionLocal


@pytest.mark.parametrize(
    ("target_words", "configured_max", "expected"),
    [
        (500, 2048, 2048),
        (3000, 4096, 4096),
        (3000, 8192, 6500),
        (10000, 32768, 20500),
        (3000, "invalid", 4096),
    ],
)
def test_chapter_output_budget_respects_configured_maximum(
    target_words, configured_max, expected
):
    assert _chapter_output_token_budget(target_words, configured_max) == expected


@pytest.mark.asyncio
async def test_overlapping_project_generation_is_rejected():
    project_id = "already-running"
    _active_generation_projects.add(project_id)
    try:
        events = [
            json.loads(event.removeprefix("data: ").strip())
            async for event in _stream_single_chapter(project_id, 1, "user-1")
        ]
    finally:
        _active_generation_projects.discard(project_id)

    assert events == [
        {
            "type": "error",
            "error": "该项目已有生成任务正在运行，请等待完成后重试",
        }
    ]


async def _create_ready_project(client, headers, total_chapters: int = 1) -> str:
    project_resp = await client.post(
        "/api/projects",
        headers=headers,
        json={
            "title": "生成安全测试",
            "genre": "玄幻",
            "total_chapters": total_chapters,
            "chapter_word_count": 1000,
            "style_intensity": "standard",
        },
    )
    assert project_resp.status_code == 200
    project_id = project_resp.json()["id"]

    worldview_resp = await client.post(
        f"/api/worldview/{project_id}",
        headers=headers,
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
            "geography": [],
            "factions": [],
            "power_system": [],
            "history": [],
            "conflicts": [],
            "special_settings": [],
            "source": "manual",
        },
    )
    assert worldview_resp.status_code == 200

    # DEV-003D1: construct a historical Outline directly. Public outline
    # generation is retired, while Chapter must keep reading existing rows.
    async with TestSessionLocal() as db:
        result = await db.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one()
        project.status = ProjectStatus.OUTLINE_CONFIRMED
        db.add(Outline(
            project_id=project_id,
            story_arc="旧项目兼容规划",
            chapters=[{
                "chapter_num": chapter_num,
                "title": f"第{chapter_num}章",
                "summary": "兼容测试章节",
                "key_events": [],
                "reveal_elements": [],
            } for chapter_num in range(1, total_chapters + 1)],
            reveal_plan=[],
        ))
        db.add(StoryMemory(project_id=project_id))
        await db.commit()
    return project_id


@pytest.mark.asyncio
@pytest.mark.usefixtures("clean_db")
async def test_interrupted_generation_never_saves_partial_chapter(client, auth_headers):
    project_id = await _create_ready_project(client, auth_headers)

    async def interrupted_stream(*args, **kwargs):
        yield "这是未完成的半章"
        raise RuntimeError("private upstream detail")

    with patch.object(llm_client, "chat_stream", new=interrupted_stream):
        response = await client.post(
            f"/api/chapters/{project_id}/1/generate",
            headers=auth_headers,
        )

    assert response.status_code == 200
    assert "未保存不完整内容" in response.text
    assert "private upstream detail" not in response.text
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert not any(event["type"] == "complete" for event in events)

    chapter_resp = await client.get(
        f"/api/chapters/{project_id}/1",
        headers=auth_headers,
    )
    assert chapter_resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.usefixtures("clean_db")
async def test_empty_generation_is_not_saved_as_complete(client, auth_headers):
    project_id = await _create_ready_project(client, auth_headers)

    async def empty_stream(*args, **kwargs):
        if False:
            yield ""

    with patch.object(llm_client, "chat_stream", new=empty_stream):
        response = await client.post(
            f"/api/chapters/{project_id}/1/generate",
            headers=auth_headers,
        )

    assert response.status_code == 200
    assert "未生成有效内容" in response.text
    assert '"type": "complete"' not in response.text

    chapter_resp = await client.get(
        f"/api/chapters/{project_id}/1",
        headers=auth_headers,
    )
    assert chapter_resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.usefixtures("clean_db")
async def test_generation_fails_closed_when_worldview_elements_are_over_encoded(
    client, auth_headers
):
    project_id = await _create_ready_project(client, auth_headers)
    async with TestSessionLocal() as db:
        worldview = await db.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        worldview.parsed_elements = json.dumps(
            json.dumps(
                json.dumps([{"name": "不应进入提示词"}], ensure_ascii=False),
                ensure_ascii=False,
            ),
            ensure_ascii=False,
        )
        await db.commit()

    with patch.object(llm_client, "chat_stream") as chat_stream:
        response = await client.post(
            f"/api/chapters/{project_id}/1/generate",
            headers=auth_headers,
        )

    assert response.status_code == 200
    assert "LEGACY_WORLDVIEW_ELEMENTS_INVALID" in response.text
    assert "不应进入提示词" not in response.text
    chat_stream.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.usefixtures("clean_db")
async def test_batch_generation_validates_worldview_before_status_or_content_writes(
    client, auth_headers
):
    project_id = await _create_ready_project(client, auth_headers, total_chapters=2)
    async with TestSessionLocal() as db:
        worldview = await db.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        worldview.parsed_elements = json.dumps(
            json.dumps(
                json.dumps([{"name": "不应进入提示词"}], ensure_ascii=False),
                ensure_ascii=False,
            ),
            ensure_ascii=False,
        )
        await db.commit()

    with patch.object(llm_client, "chat_stream") as chat_stream:
        response = await client.post(
            f"/api/chapters/{project_id}/generate-all",
            headers=auth_headers,
        )

    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["code"] == "LEGACY_WORLDVIEW_ELEMENTS_INVALID"
    assert "不应进入提示词" not in str(events)
    chat_stream.assert_not_called()
    async with TestSessionLocal() as db:
        project = await db.scalar(select(Project).where(Project.id == project_id))
        chapters = list((await db.scalars(
            select(Chapter).where(Chapter.project_id == project_id)
        )).all())
        memory = await db.scalar(
            select(StoryMemory).where(StoryMemory.project_id == project_id)
        )
        assert project.status == ProjectStatus.OUTLINE_CONFIRMED
        assert chapters == []
        assert memory.chapter_summaries in (None, [])
        assert memory.timeline in (None, [])


@pytest.mark.asyncio
@pytest.mark.usefixtures("clean_db")
async def test_edit_updates_story_memory_and_project_completion(client, auth_headers):
    project_id = await _create_ready_project(client, auth_headers)
    generate_resp = await client.post(
        f"/api/chapters/{project_id}/1/generate",
        headers=auth_headers,
    )
    assert generate_resp.status_code == 200
    assert '"type": "complete"' in generate_resp.text

    edited_content = "林远没有离开山谷，而是决定返回洞府调查真相。"
    edit_resp = await client.put(
        f"/api/chapters/{project_id}/1",
        headers=auth_headers,
        json={"content": edited_content},
    )
    assert edit_resp.status_code == 200

    async with TestSessionLocal() as db:
        result = await db.execute(
            select(StoryMemory).where(StoryMemory.project_id == project_id)
        )
        memory = result.scalar_one()
        assert memory.chapter_summaries == [
            {
                "chapter_num": 1,
                "summary": edited_content,
            }
        ]
        matching_events = [
            event
            for event in memory.timeline
            if event["chapter"] == 1 and event["event"] == "章节生成"
        ]
        assert matching_events == [
            {
                "chapter": 1,
                "event": "章节生成",
                "description": edited_content,
            }
        ]

    project_resp = await client.get(
        f"/api/projects/{project_id}",
        headers=auth_headers,
    )
    assert project_resp.json()["status"] == ProjectStatus.COMPLETED.value


@pytest.mark.asyncio
@pytest.mark.usefixtures("clean_db")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("total_chapters", 0),
        ("total_chapters", 51),
        ("chapter_word_count", 499),
        ("chapter_word_count", 10001),
        ("style_intensity", "unknown"),
    ],
)
async def test_project_generation_bounds_are_enforced(
    client, auth_headers, field, value
):
    payload = {
        "title": "边界测试",
        "genre": "玄幻",
        "total_chapters": 10,
        "chapter_word_count": 1000,
        "style_intensity": "standard",
    }
    payload[field] = value
    response = await client.post(
        "/api/projects",
        headers=auth_headers,
        json=payload,
    )
    assert response.status_code == 422
