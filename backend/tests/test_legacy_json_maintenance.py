"""BUG-002B1 maintenance freeze contract and write-boundary tests."""

import json

import pytest

from app.api.chapters import _generate_chapter_core
from app.config import settings as app_settings
from app.core.maintenance import (
    PROJECT_WRITE_FROZEN_CODE,
    PROJECT_WRITE_FROZEN_MESSAGE,
    ProjectWriteFrozenError,
)
from app.core.memory_store import memory_store
from app.models.project import Outline, StoryMemory


PROJECT_PAYLOAD = {
    "title": "维护冻结测试",
    "genre": "玄幻",
    "total_chapters": 1,
    "chapter_word_count": 1000,
    "style_intensity": "standard",
}

WORLDVIEW_PAYLOAD = {
    "characters": [],
    "geography": [],
    "factions": [],
    "power_system": [],
    "history": [],
    "conflicts": [],
    "special_settings": [],
    "source": "manual",
}

EXPECTED_KEYS = {
    "detail",
    "code",
    "maintenance_state",
    "retryable",
    "retry_after_seconds",
    "event_id",
}


async def _create_project(client, auth_headers) -> str:
    response = await client.post(
        "/api/projects",
        json=PROJECT_PAYLOAD,
        headers=auth_headers,
    )
    assert response.status_code == 200
    return response.json()["id"]


def _assert_frozen_response(response) -> None:
    assert response.status_code == 503
    assert response.headers["retry-after"] == "60"
    payload = response.json()
    assert set(payload) == EXPECTED_KEYS
    assert payload == {
        "detail": PROJECT_WRITE_FROZEN_MESSAGE,
        "code": PROJECT_WRITE_FROZEN_CODE,
        "maintenance_state": "write_frozen",
        "retryable": True,
        "retry_after_seconds": 60,
        "event_id": "BUG-002B",
    }
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in (
        "postgres",
        "sqlite",
        "database",
        "traceback",
        "select ",
        "update ",
        "/users/",
    ):
        assert forbidden not in serialized


@pytest.mark.usefixtures("clean_db")
async def test_status_endpoint_is_safe_and_reflects_runtime_switch(
    client, monkeypatch
):
    response = await client.get("/api/version/maintenance")
    assert response.status_code == 200
    assert response.json() == {"active": False}

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    response = await client.get("/api/version/maintenance")
    assert response.status_code == 200
    payload = response.json()
    assert payload["active"] is True
    assert payload["code"] == PROJECT_WRITE_FROZEN_CODE
    assert payload["detail"] == PROJECT_WRITE_FROZEN_MESSAGE


@pytest.mark.usefixtures("clean_db")
async def test_all_protected_http_entrypoints_share_one_contract(
    client, auth_headers, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)

    requests = [
        ("POST", f"/api/worldview/{project_id}", WORLDVIEW_PAYLOAD),
        ("POST", f"/api/outline/{project_id}/generate", None),
        ("POST", f"/api/outline/{project_id}/generate-stream", None),
        (
            "PUT",
            f"/api/outline/{project_id}",
            {"story_arc": "", "chapters": []},
        ),
        ("POST", f"/api/outline/{project_id}/confirm", None),
        (
            "PUT",
            f"/api/chapters/{project_id}/word-counts",
            {"total_word_count": None, "chapters": []},
        ),
        (
            "PUT",
            f"/api/chapters/{project_id}/1",
            {"title": "不会被保存"},
        ),
        ("POST", f"/api/chapters/{project_id}/1/generate", None),
        ("POST", f"/api/chapters/{project_id}/generate-all", None),
        ("DELETE", f"/api/projects/{project_id}", None),
    ]

    for method, path, body in requests:
        response = await client.request(
            method,
            path,
            json=body,
            headers=auth_headers,
        )
        _assert_frozen_response(response)

    response = await client.get(f"/api/projects/{project_id}", headers=auth_headers)
    assert response.status_code == 200


@pytest.mark.usefixtures("clean_db")
async def test_unrelated_reads_and_writes_remain_available(
    client, auth_headers, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)

    assert (await client.get("/api/projects", headers=auth_headers)).status_code == 200
    assert (
        await client.get(f"/api/projects/{project_id}", headers=auth_headers)
    ).status_code == 200

    updated = dict(PROJECT_PAYLOAD, title="维护期间仍可改项目标题")
    assert (
        await client.put(
            f"/api/projects/{project_id}",
            json=updated,
            headers=auth_headers,
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/api/worldview/{project_id}/import",
            json={"document_text": "太短"},
            headers=auth_headers,
        )
    ).status_code == 422
    assert (
        await client.post(
            "/api/projects",
            json=dict(PROJECT_PAYLOAD, title="第二个项目"),
            headers=auth_headers,
        )
    ).status_code == 200


@pytest.mark.usefixtures("clean_db")
async def test_authentication_still_precedes_maintenance_disclosure(
    client, monkeypatch
):
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    response = await client.delete("/api/projects/not-owned")
    assert response.status_code == 401


@pytest.mark.usefixtures("clean_db")
async def test_memory_store_reads_existing_but_blocks_mutation(
    client, auth_headers, db_session, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    memory = StoryMemory(
        project_id=project_id,
        revealed_elements=[],
        character_states={},
        foreshadows=[],
        timeline=[],
        chapter_summaries=[],
    )
    db_session.add(memory)
    await db_session.commit()
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)

    existing = await memory_store.get_or_create(db_session, project_id)
    assert existing.id == memory.id
    with pytest.raises(ProjectWriteFrozenError):
        await memory_store.add_chapter_summary(
            db_session,
            existing,
            chapter_num=1,
            summary="不会被保存",
        )


@pytest.mark.usefixtures("clean_db")
async def test_memory_store_blocks_creation_and_chapter_sse_terminates_safely(
    client, auth_headers, db_session, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)

    with pytest.raises(ProjectWriteFrozenError):
        await memory_store.get_or_create(db_session, project_id)

    events = [
        event
        async for event in _generate_chapter_core(
            db_session,
            project_id,
            chapter_num=1,
        )
    ]

    assert len(events) == 1
    event = json.loads(events[0].removeprefix("data: ").strip())
    assert event["type"] == "error"
    assert event["error"]["code"] == PROJECT_WRITE_FROZEN_CODE
    assert event["error"]["detail"] == PROJECT_WRITE_FROZEN_MESSAGE


@pytest.mark.usefixtures("clean_db")
async def test_runtime_unfreeze_restores_protected_write(
    client, auth_headers, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)
    _assert_frozen_response(
        await client.post(
            f"/api/worldview/{project_id}",
            json=WORLDVIEW_PAYLOAD,
            headers=auth_headers,
        )
    )

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", False)
    response = await client.post(
        f"/api/worldview/{project_id}",
        json=WORLDVIEW_PAYLOAD,
        headers=auth_headers,
    )
    assert response.status_code == 200


@pytest.mark.usefixtures("clean_db")
async def test_outline_stream_rechecks_freeze_before_fresh_session_save(
    client, auth_headers, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    worldview = await client.post(
        f"/api/worldview/{project_id}",
        json={
            **WORLDVIEW_PAYLOAD,
            "characters": [{"name": "林远"}],
        },
        headers=auth_headers,
    )
    assert worldview.status_code == 200

    complete_outline = json.dumps(
        {
            "story_arc": "林远踏上旅途。",
            "chapters": [
                {
                    "chapter_num": 1,
                    "title": "启程",
                    "summary": "林远出发。",
                    "key_events": ["离开故乡"],
                    "reveal_elements": ["林远"],
                }
            ],
            "reveal_plan": [],
        },
        ensure_ascii=False,
    )

    async def freeze_after_generation(*_args, **_kwargs):
        yield complete_outline
        monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)

    monkeypatch.setattr(
        "app.api.outline.llm_client.chat_stream",
        freeze_after_generation,
    )
    response = await client.post(
        f"/api/outline/{project_id}/generate-stream",
        headers=auth_headers,
    )
    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    error = next(event for event in events if event["type"] == "error")
    assert error["error"]["code"] == PROJECT_WRITE_FROZEN_CODE

    read_response = await client.get(
        f"/api/outline/{project_id}",
        headers=auth_headers,
    )
    assert read_response.status_code == 404


@pytest.mark.usefixtures("clean_db")
async def test_chapter_stream_rechecks_freeze_before_final_commit(
    client, auth_headers, db_session, monkeypatch
):
    project_id = await _create_project(client, auth_headers)
    worldview = await client.post(
        f"/api/worldview/{project_id}",
        json={
            **WORLDVIEW_PAYLOAD,
            "characters": [{"name": "林远"}],
        },
        headers=auth_headers,
    )
    assert worldview.status_code == 200
    db_session.add(
        Outline(
            project_id=project_id,
            story_arc="林远踏上旅途。",
            chapters=[
                {
                    "chapter_num": 1,
                    "title": "启程",
                    "summary": "林远出发。",
                    "key_events": ["离开故乡"],
                    "reveal_elements": ["林远"],
                }
            ],
            reveal_plan=[],
        )
    )
    await db_session.commit()

    async def freeze_after_generation(*_args, **_kwargs):
        yield "林远离开故乡，踏上旅途。"
        monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", True)

    monkeypatch.setattr(
        "app.api.chapters.llm_client.chat_stream",
        freeze_after_generation,
    )
    response = await client.post(
        f"/api/chapters/{project_id}/1/generate",
        headers=auth_headers,
    )
    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    error = next(event for event in events if event["type"] == "error")
    assert error["error"]["code"] == PROJECT_WRITE_FROZEN_CODE

    read_response = await client.get(
        f"/api/chapters/{project_id}/1",
        headers=auth_headers,
    )
    assert read_response.status_code == 404

    monkeypatch.setattr(app_settings, "LEGACY_JSON_WRITES_FROZEN", False)
    batch_response = await client.post(
        f"/api/chapters/{project_id}/generate-all",
        headers=auth_headers,
    )
    batch_events = [
        json.loads(line.removeprefix("data: "))
        for line in batch_response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert any(
        event.get("error", {}).get("code") == PROJECT_WRITE_FROZEN_CODE
        for event in batch_events
        if isinstance(event.get("error"), dict)
    )
    assert all(event["type"] != "batch_complete" for event in batch_events)
