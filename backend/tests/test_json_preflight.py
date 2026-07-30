import json
import os
from pathlib import Path

import pytest

import app.commands.json_preflight as json_preflight_command
from app.commands.json_preflight import main, render_json, render_text
from app.core.json_preflight import (
    BLOCKED,
    ERROR,
    FIELDS,
    NOT_APPLICABLE,
    PASS,
    REVIEW_REQUIRED,
    PreflightFailure,
    _build_report,
    build_error_report,
    build_not_applicable_report,
    run_preflight,
    validate_field_whitelist,
    validate_environment_label,
    validate_hmac_key,
    validate_schema_rows,
)


TEST_HMAC_KEY = b"unit-test-key-that-is-at-least-32-bytes"


def _valid_aggregate_rows():
    rows = []
    for field in FIELDS:
        rows.append(
            {
                "table_name": field.table,
                "column_name": field.column,
                "category": (
                    "valid_object"
                    if field.expected_shape == "object"
                    else "valid_array"
                ),
                "item_count": 1,
            }
        )
    return rows


def _build_synthetic_report(aggregate_rows=None, sample_rows=None):
    return _build_report(
        environment_label="测试环境",
        postgres_version="160004",
        schema_revision="test_revision",
        row_counts={
            "worldviews": 1,
            "outlines": 1,
            "chapters": 1,
            "story_memories": 1,
        },
        aggregate_rows=aggregate_rows or _valid_aggregate_rows(),
        sample_rows=sample_rows or [],
        hmac_key=TEST_HMAC_KEY,
        sample_limit=10,
        code_revision="test_commit",
    )


def test_field_whitelist_is_exact_and_unique():
    validate_field_whitelist()
    assert len(FIELDS) == 16
    assert len({field.field_code for field in FIELDS}) == 16
    assert {field.table for field in FIELDS} == {
        "worldviews",
        "outlines",
        "chapters",
        "story_memories",
    }


def test_hmac_key_requires_32_bytes():
    with pytest.raises(PreflightFailure, match="hmac_key_too_short"):
        validate_hmac_key("short")
    assert validate_hmac_key("x" * 32) == b"x" * 32


@pytest.mark.parametrize(
    "label",
    [
        "",
        "x" * 65,
        "生产\n状态: 通过",
        "postgresql://user:secret@example/db",
        "host=secret.example",
        "生产/主库",
    ],
)
def test_environment_label_rejects_sensitive_or_structural_input(label):
    with pytest.raises(PreflightFailure, match="environment_label_invalid"):
        validate_environment_label(label)


def test_environment_label_accepts_short_human_alias():
    assert validate_environment_label("生产只读检查（上海）") == "生产只读检查（上海）"


async def test_preflight_rejects_unsupported_dialect_and_driver_before_connecting():
    with pytest.raises(PreflightFailure, match="database_dialect_unsupported"):
        await run_preflight(
            database_url="mysql+asyncmy://user:secret@example/database",
            environment_label="测试环境",
            hmac_key="x" * 32,
        )
    with pytest.raises(PreflightFailure, match="database_driver_unsupported"):
        await run_preflight(
            database_url="postgresql+psycopg2://user:secret@example/database",
            environment_label="测试环境",
            hmac_key="x" * 32,
        )


def test_schema_validation_requires_all_legacy_text_columns():
    rows = [
        {
            "table_name": field.table,
            "column_name": field.column,
            "data_type": "text",
            "udt_name": "text",
        }
        for field in FIELDS
    ]
    validate_schema_rows(rows)

    with pytest.raises(PreflightFailure, match="schema_coverage_mismatch"):
        validate_schema_rows(rows[:-1])

    rows[0] = {**rows[0], "data_type": "json", "udt_name": "json"}
    with pytest.raises(PreflightFailure, match="schema_type_mismatch"):
        validate_schema_rows(rows)


def test_clean_data_shape_is_pass_but_overall_still_requires_later_gates():
    report = _build_synthetic_report()
    assert report["data_shape_status"] == PASS
    assert report["overall_status"] == REVIEW_REQUIRED
    assert report["coverage"]["cells_checked"] == 16
    assert report["write_performed"] is False
    assert report["runtime_conditions"]["backup_available"] == "not_checked"


def test_blocker_report_is_hmac_redacted():
    rows = _valid_aggregate_rows()
    rows[0] = {
        "table_name": FIELDS[0].table,
        "column_name": FIELDS[0].column,
        "category": "invalid_json",
        "item_count": 1,
    }
    report = _build_synthetic_report(
        aggregate_rows=rows,
        sample_rows=[
            {
                "table_name": FIELDS[0].table,
                "column_name": FIELDS[0].column,
                "row_id": "raw-record-id",
                "value": "秘密小说内容",
                "category": "invalid_json",
            }
        ],
    )

    serialized = render_json(report)
    assert report["overall_status"] == BLOCKED
    assert report["data_shape_status"] == BLOCKED
    assert report["issue_index"][0]["severity"] == "BLOCKER"
    assert "raw-record-id" not in serialized
    assert "秘密小说内容" not in serialized
    assert "postgresql://" not in serialized


def test_legacy_parsed_elements_object_requires_review():
    rows = _valid_aggregate_rows()
    parsed_index = next(
        index
        for index, field in enumerate(FIELDS)
        if field.field_code == "worldviews.parsed_elements"
    )
    parsed = FIELDS[parsed_index]
    rows[parsed_index] = {
        "table_name": parsed.table,
        "column_name": parsed.column,
        "category": "parsed_elements_object",
        "item_count": 1,
    }
    report = _build_synthetic_report(aggregate_rows=rows)
    assert report["data_shape_status"] == REVIEW_REQUIRED
    assert report["issue_index"][0]["severity"] == "REVIEW"


def test_classification_count_mismatch_is_an_error():
    rows = _valid_aggregate_rows()
    rows[0] = {**rows[0], "item_count": 2}
    with pytest.raises(PreflightFailure, match="classification_count_mismatch"):
        _build_synthetic_report(aggregate_rows=rows)


def test_not_applicable_and_error_reports_are_explicit():
    sqlite_report = build_not_applicable_report("本地", "test")
    assert sqlite_report["overall_status"] == NOT_APPLICABLE
    assert sqlite_report["write_performed"] is False

    error_report = build_error_report("测试", "safe_event_code", "test")
    assert error_report["overall_status"] == ERROR
    assert error_report["error"] == {
        "event_code": "safe_event_code",
        "connection_details_included": False,
    }


def test_text_output_is_vertical_and_repeats_decision():
    report = _build_synthetic_report()
    report["manager_action"] = "继续完成维护备份恢复门禁" * 12
    output = render_text(report, width=40)
    assert "状态: 需要确认" in output
    assert "数据形态状态: 通过" in output
    assert "最终状态: 需要确认" in output
    assert "后续门禁" in output
    assert "长事务: 未检查" in output
    assert "恢复演练: 未检查" in output
    assert "冻结后复检: 否" in output
    assert "本报告未修改数据" in output
    assert len(max(output.splitlines(), key=len)) <= 40


def test_cli_review_and_blocked_exit_codes(monkeypatch, capsys):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    async def review_report(**_kwargs):
        return _build_synthetic_report()

    monkeypatch.setattr(json_preflight_command, "run_preflight", review_report)
    assert main(["--format", "text", "--no-color"]) == 2
    review_output = capsys.readouterr().out
    assert "状态: 需要确认" in review_output
    assert "\x1b[" not in review_output

    blocked = _build_synthetic_report()
    blocked["overall_status"] = BLOCKED
    blocked["data_shape_status"] = BLOCKED

    async def blocked_report(**_kwargs):
        return blocked

    monkeypatch.setattr(json_preflight_command, "run_preflight", blocked_report)
    assert main(["--format", "json"]) == 3
    assert json.loads(capsys.readouterr().out)["overall_status"] == BLOCKED


@pytest.mark.parametrize(
    "unsafe_label",
    [
        "生产\n最终状态: 通过",
        "postgresql://user:secret@host/database",
        "x" * 65,
    ],
)
def test_cli_redacts_unsafe_environment_label(unsafe_label, capsys):
    exit_code = main(
        [
            "--format",
            "json",
            "--environment-label",
            unsafe_label,
        ]
    )
    output = capsys.readouterr()
    report = json.loads(output.out)

    assert exit_code == 4
    assert report["overall_status"] == ERROR
    assert report["environment_label"] == "已隐藏不安全环境标签"
    assert unsafe_label not in output.out
    assert unsafe_label not in output.err


def test_sqlite_cli_does_not_create_database_file(monkeypatch, tmp_path, capsys):
    from app.config import settings

    database_path = tmp_path / "must-not-exist.db"
    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{database_path}",
    )
    monkeypatch.delenv("JSON_PREFLIGHT_HMAC_KEY", raising=False)

    exit_code = main(["--format", "json", "--environment-label", "本地测试"])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["overall_status"] == NOT_APPLICABLE
    assert report["write_performed"] is False
    assert not database_path.exists()


def test_output_file_is_atomic_and_private(monkeypatch, tmp_path):
    from app.config import settings

    database_path = tmp_path / "unused.db"
    output_path = tmp_path / "report.json"
    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{database_path}",
    )

    assert main(["--format", "json", "--output", str(output_path)]) == 0
    assert json.loads(output_path.read_text())["overall_status"] == NOT_APPLICABLE
    assert not database_path.exists()
    assert os.stat(output_path).st_mode & 0o777 == 0o600


def test_output_file_never_overwrites_existing_path(monkeypatch, tmp_path, capsys):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    output_path = tmp_path / "existing-report.json"
    output_path.write_text("must stay unchanged")

    assert main(["--format", "json", "--output", str(output_path)]) == 4
    assert output_path.read_text() == "must stay unchanged"
    assert "无法安全写入" in capsys.readouterr().err


def test_cli_error_does_not_expose_invalid_connection(monkeypatch, capsys):
    from app.config import settings

    secret_url = "not-a-valid-url-with-secret"
    monkeypatch.setattr(settings, "DATABASE_URL", secret_url)

    exit_code = main(["--format", "json"])
    output = capsys.readouterr()
    report = json.loads(output.out)

    assert exit_code == 4
    assert report["overall_status"] == ERROR
    assert secret_url not in output.out
    assert secret_url not in output.err
