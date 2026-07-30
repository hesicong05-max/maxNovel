import json
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

from app.core.lore_migration import (
    build_legacy_compatibility_projection,
    compare_legacy_file_payload,
    deterministic_element_id,
    legacy_structured_payload,
    legacy_worldview_checksum,
    project_legacy_worldview,
    type_field_definitions,
    validate_projection,
)


def _worldview(**overrides):
    values = {
        "characters": [
            {"name": "林岚", "personality": "沉稳"},
            {"name": "林岚", "personality": "冲动"},
        ],
        "geography": [{"name": "云港", "description": "浮空港口"}],
        "factions": [{"name": "星盟", "stance": "守序"}],
        "power_system": [{"name": "灵阶", "rules": "九阶"}],
        "history": [{"event": "天裂", "description": "天空裂开"}],
        "conflicts": [{"name": "王位争夺", "stakes": "王国存亡"}],
        "special_settings": [{"name": "禁飞令", "rules": "夜间禁飞"}],
        "parsed_elements": [
            {"id": "legacy-char-1", "category": "character", "name": "林岚"},
            {"id": "legacy-char-2", "category": "character", "name": "林岚"},
            {"id": "legacy-place", "category": "geography", "name": "云港"},
        ],
        "source": "imported",
        "created_at": datetime(2026, 7, 30, 12, 0, 0),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_projection_maps_all_legacy_categories_without_mutation():
    worldview = _worldview()
    before = deepcopy(worldview.__dict__)

    projection = project_legacy_worldview("project-a", worldview)

    assert len(projection.elements) == 8
    assert worldview.__dict__ == before
    assert {element.type_key for element in projection.elements} == {
        "character",
        "location",
        "faction",
        "rule",
        "event",
        "conflict",
    }
    location = next(
        element for element in projection.elements if element.legacy_category == "geography"
    )
    assert location.type_key == "location"
    assert location.type_display_name == "地点"
    assert all(element.type_key != "scene" for element in projection.elements)


def test_duplicate_names_have_stable_distinct_project_namespaced_ids():
    worldview = _worldview()
    first = project_legacy_worldview("project-a", worldview)
    second = project_legacy_worldview("project-a", worldview)
    other_project = project_legacy_worldview("project-b", worldview)

    assert [element.id for element in first.elements] == [
        element.id for element in second.elements
    ]
    assert len({element.id for element in first.elements}) == len(first.elements)
    assert {element.id for element in first.elements}.isdisjoint(
        {element.id for element in other_project.elements}
    )


def test_deterministic_id_never_reuses_legacy_12_character_id_directly():
    element_id = deterministic_element_id("project-a", "characters", 0, "abc123def456")
    assert len(element_id) == 32
    assert element_id != "abc123def456"


def test_parsed_name_mismatch_is_reported_and_not_used_as_identity():
    worldview = _worldview(
        parsed_elements=[
            {"id": "wrong-id", "category": "character", "name": "另一角色"}
        ]
    )
    projection = project_legacy_worldview("project-a", worldview)

    assert "characters:0:parsed_name_mismatch" in projection.warnings
    first = projection.elements[0]
    assert first.legacy_id is None


def test_checksum_changes_only_when_projected_source_changes():
    worldview = _worldview()
    checksum = legacy_worldview_checksum(worldview)
    worldview.raw_text = "不参与列表身份的完整原文"
    assert legacy_worldview_checksum(worldview) == checksum
    worldview.characters[0]["name"] = "林岚·改"
    assert legacy_worldview_checksum(worldview) != checksum


def test_projection_decodes_historical_text_json_columns_losslessly():
    normal = _worldview()
    text_backed = _worldview(
        **{
            category: json.dumps(
                getattr(normal, category),
                ensure_ascii=False,
            )
            for category in (
                "characters",
                "geography",
                "factions",
                "power_system",
                "history",
                "conflicts",
                "special_settings",
            )
        },
        parsed_elements=json.dumps(
            normal.parsed_elements,
            ensure_ascii=False,
        ),
    )

    projection = project_legacy_worldview("project-a", text_backed)

    assert validate_projection(projection)["valid"] is True
    assert len(projection.elements) == 8
    assert legacy_worldview_checksum(text_backed) == legacy_worldview_checksum(normal)
    assert (
        build_legacy_compatibility_projection(projection)
        == legacy_structured_payload(normal)
    )


def test_invalid_collection_fails_validation_without_crashing():
    projection = project_legacy_worldview(
        "project-a",
        _worldview(geography={"name": "错误结构"}),
    )
    report = validate_projection(projection)
    assert report["valid"] is False
    assert "geography:invalid_collection" in report["warnings"]


def test_field_definitions_use_stable_keys_for_dynamic_forms():
    definitions = type_field_definitions("character")
    assert [field["key"] for field in definitions] == [
        "personality",
        "background",
        "motivation",
        "ability",
    ]
    assert type_field_definitions("unknown") == []


def test_compatibility_projection_round_trips_seven_structured_arrays():
    worldview = _worldview()
    projection = project_legacy_worldview("project-a", worldview)
    compatibility = build_legacy_compatibility_projection(projection)

    assert compatibility == legacy_structured_payload(worldview)
    comparison = compare_legacy_file_payload(worldview, compatibility)
    assert comparison["matches"] is True


def test_compatibility_projection_preserves_non_object_entries_losslessly():
    worldview = _worldview(
        characters=["林岚", 7, None, True],
        parsed_elements=[],
    )

    projection = project_legacy_worldview("project-a", worldview)
    compatibility = build_legacy_compatibility_projection(projection)

    assert compatibility["characters"] == ["林岚", 7, None, True]
    assert compatibility == legacy_structured_payload(worldview)
    assert validate_projection(projection)["valid"] is True
    assert [element.name for element in projection.elements[:4]] == [
        "林岚",
        "角色2",
        "角色3",
        "角色4",
    ]


def test_project_file_difference_is_reported_without_changing_database_payload():
    worldview = _worldview()
    before = deepcopy(worldview.__dict__)
    file_payload = legacy_structured_payload(worldview)
    file_payload["geography"][0]["name"] = "文件中的旧名称"

    comparison = compare_legacy_file_payload(worldview, file_payload)

    assert comparison["matches"] is False
    assert worldview.__dict__ == before
