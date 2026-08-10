import asyncio
import json

import pytest
from sqlalchemy import select

from app.core.legacy_json import read_legacy_json
from app.models.project import Worldview


def _payload(name: str, expected_source_checksum: str | None = None) -> dict:
    return {
        "characters": [{
            "name": name,
            "personality": "沉稳",
            "background": "",
            "motivation": "守护故乡",
            "ability": "观星",
            "relations": [],
        }],
        "geography": [],
        "factions": [],
        "power_system": [],
        "history": [],
        "conflicts": [],
        "special_settings": [],
        "raw_text": None,
        "source": "manual",
        "expected_source_checksum": expected_source_checksum,
    }


async def _project(client, headers) -> str:
    response = await client.post(
        "/api/projects",
        headers=headers,
        json={
            "title": "世界观并发保存",
            "genre": "玄幻",
            "total_chapters": 10,
            "chapter_word_count": 1000,
            "style_intensity": "standard",
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


@pytest.mark.usefixtures("clean_db")
async def test_worldview_save_rejects_stale_change_and_allows_same_content_replay(
    client, auth_headers
):
    project_id = await _project(client, auth_headers)
    created = await client.post(
        f"/api/worldview/{project_id}",
        headers=auth_headers,
        json=_payload("初始角色"),
    )
    assert created.status_code == 200
    first_checksum = created.json()["source_checksum"]
    assert len(first_checksum) == 64

    updated = await client.post(
        f"/api/worldview/{project_id}",
        headers=auth_headers,
        json=_payload("窗口一保存", first_checksum),
    )
    assert updated.status_code == 200
    second_checksum = updated.json()["source_checksum"]
    assert second_checksum != first_checksum

    stale = await client.post(
        f"/api/worldview/{project_id}",
        headers=auth_headers,
        json=_payload("窗口二覆盖", first_checksum),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == {
        "code": "WORLDVIEW_SOURCE_STALE",
        "message": "服务器上的世界观已发生变化，请先重新加载并核对本地草稿。",
        "retryable": False,
        "reload_required": True,
    }

    unchanged = await client.get(
        f"/api/worldview/{project_id}", headers=auth_headers
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["characters"][0]["name"] == "窗口一保存"
    assert unchanged.json()["source_checksum"] == second_checksum

    replay = await client.post(
        f"/api/worldview/{project_id}",
        headers=auth_headers,
        json=_payload("窗口一保存", first_checksum),
    )
    assert replay.status_code == 200
    assert replay.json()["source_checksum"] == second_checksum


@pytest.mark.usefixtures("clean_db")
async def test_existing_worldview_change_requires_expected_checksum(client, auth_headers):
    project_id = await _project(client, auth_headers)
    created = await client.post(
        f"/api/worldview/{project_id}",
        headers=auth_headers,
        json=_payload("原版本"),
    )
    assert created.status_code == 200

    missing_token = await client.post(
        f"/api/worldview/{project_id}",
        headers=auth_headers,
        json=_payload("无令牌修改"),
    )
    assert missing_token.status_code == 409

    current = await client.get(
        f"/api/worldview/{project_id}", headers=auth_headers
    )
    assert current.json()["characters"][0]["name"] == "原版本"


@pytest.mark.usefixtures("clean_db")
async def test_postgres_concurrent_worldview_updates_have_one_winner(
    client, auth_headers
):
    from tests.conftest import TEST_DATABASE_BACKEND

    if TEST_DATABASE_BACKEND != "postgresql":
        pytest.skip("PostgreSQL row-lock behavior")
    project_id = await _project(client, auth_headers)
    created = await client.post(
        f"/api/worldview/{project_id}",
        headers=auth_headers,
        json=_payload("并发初始版本"),
    )
    checksum = created.json()["source_checksum"]

    first, second = await asyncio.gather(
        client.post(
            f"/api/worldview/{project_id}",
            headers=auth_headers,
            json=_payload("并发窗口甲", checksum),
        ),
        client.post(
            f"/api/worldview/{project_id}",
            headers=auth_headers,
            json=_payload("并发窗口乙", checksum),
        ),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    winner = first if first.status_code == 200 else second
    current = await client.get(
        f"/api/worldview/{project_id}", headers=auth_headers
    )
    assert current.status_code == 200
    assert current.json()["characters"] == winner.json()["characters"]
    assert current.json()["source_checksum"] == winner.json()["source_checksum"]


@pytest.mark.usefixtures("clean_db")
async def test_file_write_failure_can_replay_committed_worldview_safely(
    client, auth_headers, monkeypatch
):
    import app.api.worldview as worldview_api

    project_id = await _project(client, auth_headers)
    calls = 0

    def fail_once(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated file write failure")

    monkeypatch.setattr(worldview_api, "save_worldview_file", fail_once)
    with pytest.raises(RuntimeError, match="simulated file write failure"):
        await client.post(
            f"/api/worldview/{project_id}",
            headers=auth_headers,
            json=_payload("已提交待补文件版本"),
        )

    replay = await client.post(
        f"/api/worldview/{project_id}",
        headers=auth_headers,
        json=_payload("已提交待补文件版本"),
    )
    assert replay.status_code == 200
    assert calls == 2
    current = await client.get(
        f"/api/worldview/{project_id}", headers=auth_headers
    )
    assert current.json()["characters"][0]["name"] == "已提交待补文件版本"


@pytest.mark.usefixtures("clean_db")
async def test_worldview_get_decodes_historical_json_text_without_rewriting(
    client, auth_headers
):
    from tests.conftest import TestSessionLocal

    project_id = await _project(client, auth_headers)
    created = await client.post(
        f"/api/worldview/{project_id}",
        headers=auth_headers,
        json=_payload("历史文本角色"),
    )
    assert created.status_code == 200

    async with TestSessionLocal() as session:
        worldview = await session.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        assert worldview is not None
        for field in (
            "characters", "geography", "factions", "power_system",
            "history", "conflicts", "special_settings", "parsed_elements",
        ):
            current = read_legacy_json(getattr(worldview, field))
            assert current.valid
            setattr(worldview, field, json.dumps(current.value))
        await session.commit()

    response = await client.get(
        f"/api/worldview/{project_id}", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["characters"][0]["name"] == "历史文本角色"
    assert len(response.json()["source_checksum"]) == 64


@pytest.mark.usefixtures("clean_db")
async def test_summary_and_progress_decode_double_encoded_elements_without_rewriting(
    client, auth_headers
):
    from tests.conftest import TestSessionLocal

    project_id = await _project(client, auth_headers)
    created = await client.post(
        f"/api/worldview/{project_id}",
        headers=auth_headers,
        json=_payload("双编码角色"),
    )
    assert created.status_code == 200

    async with TestSessionLocal() as session:
        worldview = await session.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        parsed = read_legacy_json(worldview.parsed_elements)
        assert parsed.valid
        stored_value = json.dumps(
            json.dumps(parsed.value, ensure_ascii=False), ensure_ascii=False
        )
        worldview.parsed_elements = stored_value
        await session.commit()

    summary = await client.get(
        f"/api/worldview/{project_id}/summary", headers=auth_headers
    )
    progress = await client.get(
        f"/api/chapters/{project_id}/progress", headers=auth_headers
    )

    assert summary.status_code == 200, summary.text
    assert summary.json()["total"] >= 1
    assert progress.status_code == 200, progress.text
    assert progress.json()["total_elements"] >= 1
    async with TestSessionLocal() as session:
        worldview = await session.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        assert worldview.parsed_elements == stored_value


@pytest.mark.usefixtures("clean_db")
async def test_summary_and_progress_fail_closed_for_over_encoded_elements(
    client, auth_headers
):
    from tests.conftest import TestSessionLocal

    project_id = await _project(client, auth_headers)
    created = await client.post(
        f"/api/worldview/{project_id}",
        headers=auth_headers,
        json=_payload("不应泄露的角色"),
    )
    assert created.status_code == 200

    async with TestSessionLocal() as session:
        worldview = await session.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        parsed = read_legacy_json(worldview.parsed_elements)
        assert parsed.valid
        stored_value = json.dumps(
            json.dumps(
                json.dumps(parsed.value, ensure_ascii=False), ensure_ascii=False
            ),
            ensure_ascii=False,
        )
        worldview.parsed_elements = stored_value
        await session.commit()

    summary = await client.get(
        f"/api/worldview/{project_id}/summary", headers=auth_headers
    )
    progress = await client.get(
        f"/api/chapters/{project_id}/progress", headers=auth_headers
    )

    assert summary.status_code == 422
    assert summary.json()["detail"]["code"] == "WORLDVIEW_LEGACY_JSON_INVALID"
    assert "不应泄露的角色" not in summary.text
    assert progress.status_code == 422
    assert progress.json()["detail"]["code"] == "LEGACY_WORLDVIEW_ELEMENTS_INVALID"
    assert "不应泄露的角色" not in progress.text
    async with TestSessionLocal() as session:
        worldview = await session.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        assert worldview.parsed_elements == stored_value
