"""Tests for fail-safe historical JSON/Text compatibility readers."""

import pytest

from app.core.legacy_json import read_legacy_object_list


def test_native_object_list_is_copied():
    original = [{"chapter_num": 1, "nested": {"title": "启程"}}]
    result = read_legacy_object_list(original)

    assert result.valid is True
    assert result.items == original
    result.items[0]["nested"]["title"] = "修改"
    assert original[0]["nested"]["title"] == "启程"


def test_json_string_object_list_is_decoded():
    result = read_legacy_object_list(
        '[{"chapter_num": 1, "title": "启程"}]'
    )

    assert result.valid is True
    assert result.items == [{"chapter_num": 1, "title": "启程"}]


def test_double_encoded_json_string_object_list_is_decoded():
    result = read_legacy_object_list(
        '"[{\\"chapter_num\\": 1, \\"title\\": \\"启程\\"}]"'
    )

    assert result.valid is True
    assert result.items == [{"chapter_num": 1, "title": "启程"}]


@pytest.mark.parametrize(
    ("value", "category"),
    [
        ("", "empty_string"),
        ("{broken", "malformed_json"),
        ('{"chapter_num": 1}', "not_a_list"),
        ('["not-an-object"]', "item_not_an_object"),
        ({"chapter_num": 1}, "not_a_list"),
        (["not-an-object"], "item_not_an_object"),
    ],
)
def test_invalid_values_return_category_without_content(value, category):
    result = read_legacy_object_list(value)

    assert result.valid is False
    assert result.items == []
    assert result.error_category == category
    assert "broken" not in result.error_category


def test_none_and_empty_list_are_valid_empty_values():
    assert read_legacy_object_list(None).valid is True
    assert read_legacy_object_list(None).items == []
    assert read_legacy_object_list([]).valid is True
    assert read_legacy_object_list([]).items == []


def test_triple_encoded_list_exceeds_the_bounded_reader():
    result = read_legacy_object_list(
        '"\\"[{\\\\\\"chapter_num\\\\\\": 1}]\\""'
    )

    assert result.valid is False
    assert result.items == []
    assert result.error_category == "not_a_list"
