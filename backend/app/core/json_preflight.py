"""Read-only preflight for legacy PostgreSQL Text columns that store JSON.

This module deliberately does not import the application database engine,
models, or Alembic. The preflight uses a fixed schema whitelist and emits only
redacted metadata.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Iterable

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool


REPORT_SCHEMA_VERSION: Final = "1.0"
SUPPORTED_POSTGRES_MAJOR: Final = 16
MAX_ENVIRONMENT_LABEL_LENGTH: Final = 64

PASS = "PASS"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
BLOCKED = "BLOCKED"
ERROR = "ERROR"
NOT_APPLICABLE = "NOT_APPLICABLE"

EXIT_CODES: Final = {
    PASS: 0,
    NOT_APPLICABLE: 0,
    REVIEW_REQUIRED: 2,
    BLOCKED: 3,
    ERROR: 4,
}

INFO_CATEGORIES: Final = frozenset(
    {
        "sql_null",
        "empty_array",
        "empty_object",
        "valid_array",
        "valid_object",
    }
)
REVIEW_CATEGORIES: Final = frozenset({"parsed_elements_object"})
BLOCKER_CATEGORIES: Final = frozenset(
    {
        "blank_text",
        "invalid_json",
        "json_null",
        "double_encoded_array",
        "double_encoded_object",
        "json_string",
        "json_number",
        "json_boolean",
        "wrong_top_level",
        "unclassified",
    }
)

CATEGORY_LABELS: Final = {
    "sql_null": "SQL 空值",
    "blank_text": "空白文本",
    "invalid_json": "无法解析的结构",
    "json_null": "显式空值",
    "double_encoded_array": "双重编码列表",
    "double_encoded_object": "双重编码对象",
    "json_string": "字符串顶层",
    "json_number": "数字顶层",
    "json_boolean": "布尔顶层",
    "empty_array": "合法空列表",
    "empty_object": "合法空对象",
    "parsed_elements_object": "已知兼容旧结构",
    "valid_array": "合法列表",
    "valid_object": "合法对象",
    "wrong_top_level": "错误顶层形态",
    "unclassified": "无法分类",
}

CATEGORY_ACTIONS: Final = {
    "parsed_elements_object": "确认旧解析结构与兼容读取结果",
    "blank_text": "确认业务含义并提交逐条处置方案",
    "invalid_json": "定位来源并提交逐条处置方案",
    "json_null": "确认是否应保持为空或转换为 SQL 空值",
    "double_encoded_array": "确认是否允许解除一层编码",
    "double_encoded_object": "确认是否允许解除一层编码",
    "json_string": "确认正确的列表或对象结构",
    "json_number": "确认正确的列表或对象结构",
    "json_boolean": "确认正确的列表或对象结构",
    "wrong_top_level": "确认正确的顶层形态",
    "unclassified": "停止迁移并由开发者检查分类逻辑",
}


@dataclass(frozen=True)
class FieldSpec:
    table: str
    column: str
    area: str
    expected_shape: str
    allow_legacy_object: bool = False

    @property
    def field_code(self) -> str:
        return f"{self.table}.{self.column}"


FIELDS: Final[tuple[FieldSpec, ...]] = (
    FieldSpec("worldviews", "characters", "世界观—角色", "array"),
    FieldSpec("worldviews", "geography", "世界观—地理", "array"),
    FieldSpec("worldviews", "factions", "世界观—阵营", "array"),
    FieldSpec("worldviews", "power_system", "世界观—力量体系", "array"),
    FieldSpec("worldviews", "history", "世界观—历史", "array"),
    FieldSpec("worldviews", "conflicts", "世界观—冲突", "array"),
    FieldSpec("worldviews", "special_settings", "世界观—特殊设定", "array"),
    FieldSpec(
        "worldviews",
        "parsed_elements",
        "世界观—已解析设定",
        "array",
        allow_legacy_object=True,
    ),
    FieldSpec("outlines", "reveal_plan", "大纲—揭示计划", "array"),
    FieldSpec("outlines", "chapters", "大纲—章节计划", "array"),
    FieldSpec("chapters", "revealed_elements", "章节—已揭示设定", "array"),
    FieldSpec("story_memories", "revealed_elements", "故事记忆—已揭示设定", "array"),
    FieldSpec("story_memories", "character_states", "故事记忆—角色状态", "object"),
    FieldSpec("story_memories", "foreshadows", "故事记忆—伏笔", "array"),
    FieldSpec("story_memories", "timeline", "故事记忆—时间线", "array"),
    FieldSpec("story_memories", "chapter_summaries", "故事记忆—章节摘要", "array"),
)

FIELD_BY_CODE: Final = {field.field_code: field for field in FIELDS}
EXPECTED_TABLE_COUNT: Final = 4
EXPECTED_FIELD_COUNT: Final = 16


class PreflightFailure(RuntimeError):
    """A safe failure whose code can be included in a redacted report."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def validate_field_whitelist(fields: Iterable[FieldSpec] = FIELDS) -> None:
    materialized = tuple(fields)
    field_codes = {field.field_code for field in materialized}
    tables = {field.table for field in materialized}
    if len(materialized) != EXPECTED_FIELD_COUNT or len(field_codes) != EXPECTED_FIELD_COUNT:
        raise PreflightFailure("field_whitelist_invalid")
    if len(tables) != EXPECTED_TABLE_COUNT:
        raise PreflightFailure("table_whitelist_invalid")


def validate_hmac_key(key: str) -> bytes:
    encoded = key.encode("utf-8")
    if len(encoded) < 32:
        raise PreflightFailure("hmac_key_too_short")
    return encoded


def validate_environment_label(label: str) -> str:
    candidate = label.strip()
    lower = candidate.lower()
    forbidden_fragments = (
        "://",
        "jdbc:",
        "password",
        "passwd",
        "pwd=",
        "user=",
        "host=",
        "database=",
        "dbname=",
    )
    if not candidate or len(candidate) > MAX_ENVIRONMENT_LABEL_LENGTH:
        raise PreflightFailure("environment_label_invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in candidate):
        raise PreflightFailure("environment_label_invalid")
    if any(fragment in lower for fragment in forbidden_fragments):
        raise PreflightFailure("environment_label_invalid")
    if not re.fullmatch(r"[\w\u3400-\u9fff .()（）_-]+", candidate):
        raise PreflightFailure("environment_label_invalid")
    return candidate


def _hmac_token(key: bytes, namespace: str, value: str, length: int = 24) -> str:
    digest = hmac.new(key, f"{namespace}:{value}".encode(), hashlib.sha256).hexdigest()
    return digest[:length]


def _cells_sql() -> str:
    selects = [
        (
            f"SELECT '{field.table}'::text AS table_name, "
            f"id::text AS row_id, '{field.column}'::text AS column_name, "
            f"'{field.expected_shape}'::text AS expected_shape, "
            f"{str(field.allow_legacy_object).lower()}::boolean AS allow_legacy_object, "
            f"{field.column} AS value FROM {field.table}"
        )
        for field in FIELDS
    ]
    return "\nUNION ALL\n".join(selects)


CLASSIFIED_CTE: Final = f"""
WITH cells AS (
{_cells_sql()}
),
parsed_cells AS (
    SELECT
        *,
        CASE
            WHEN value IS NOT NULL
             AND btrim(value) <> ''
             AND value IS JSON
            THEN value::json
        END AS parsed
    FROM cells
),
classified AS (
    SELECT
        *,
        CASE
            WHEN value IS NULL THEN 'sql_null'
            WHEN btrim(value) = '' THEN 'blank_text'
            WHEN NOT (value IS JSON) THEN 'invalid_json'
            WHEN json_typeof(parsed) = 'null' THEN 'json_null'
            WHEN json_typeof(parsed) = 'string'
             AND ((parsed #>> '{{}}') IS JSON ARRAY)
                THEN 'double_encoded_array'
            WHEN json_typeof(parsed) = 'string'
             AND ((parsed #>> '{{}}') IS JSON OBJECT)
                THEN 'double_encoded_object'
            WHEN json_typeof(parsed) = 'string' THEN 'json_string'
            WHEN json_typeof(parsed) = 'number' THEN 'json_number'
            WHEN json_typeof(parsed) = 'boolean' THEN 'json_boolean'
            WHEN json_typeof(parsed) = 'array'
             AND btrim(value) ~ '^\\[[[:space:]]*\\]$'
                THEN 'empty_array'
            WHEN json_typeof(parsed) = 'object'
             AND allow_legacy_object
                THEN 'parsed_elements_object'
            WHEN json_typeof(parsed) = 'object'
             AND expected_shape = 'object'
             AND btrim(value) ~ '^\\{{[[:space:]]*\\}}$'
                THEN 'empty_object'
            WHEN json_typeof(parsed) = 'array'
             AND expected_shape = 'array'
                THEN 'valid_array'
            WHEN json_typeof(parsed) = 'object'
             AND expected_shape = 'object'
                THEN 'valid_object'
            WHEN json_typeof(parsed) IN ('array', 'object')
                THEN 'wrong_top_level'
            ELSE 'unclassified'
        END AS category
    FROM parsed_cells
)
"""

AGGREGATE_SQL: Final = (
    CLASSIFIED_CTE
    + """
SELECT table_name, column_name, category, count(*)::bigint AS item_count
FROM classified
GROUP BY table_name, column_name, category
ORDER BY table_name, column_name, category
"""
)

_FINDING_CATEGORY_SQL: Final = ", ".join(
    f"'{category}'" for category in sorted(BLOCKER_CATEGORIES | REVIEW_CATEGORIES)
)

SAMPLE_SQL: Final = (
    CLASSIFIED_CTE
    + f"""
, ranked AS (
    SELECT
        table_name,
        column_name,
        row_id,
        value,
        category,
        row_number() OVER (
            PARTITION BY table_name, column_name, category
            ORDER BY row_id
        ) AS sample_number
    FROM classified
    WHERE category IN ({_FINDING_CATEGORY_SQL})
)
SELECT table_name, column_name, row_id, value, category
FROM ranked
WHERE sample_number <= :sample_limit
ORDER BY table_name, column_name, category, sample_number
"""
)

_SCHEMA_PAIR_SQL: Final = ",\n".join(
    f"('{field.table}', '{field.column}')" for field in FIELDS
)

SCHEMA_SQL: Final = f"""
SELECT table_name, column_name, data_type, udt_name
FROM information_schema.columns
WHERE table_schema = current_schema()
  AND (table_name, column_name) IN (
{_SCHEMA_PAIR_SQL}
  )
ORDER BY table_name, column_name
"""

ROW_COUNT_SQL: Final = "\nUNION ALL\n".join(
    f"SELECT '{table}'::text AS table_name, count(*)::bigint AS row_count FROM {table}"
    for table in sorted({field.table for field in FIELDS})
)


def validate_schema_rows(rows: Iterable[dict[str, Any]]) -> None:
    actual: dict[str, tuple[str, str]] = {}
    for row in rows:
        code = f"{row['table_name']}.{row['column_name']}"
        if code in FIELD_BY_CODE:
            actual[code] = (str(row["data_type"]), str(row["udt_name"]))

    if set(actual) != set(FIELD_BY_CODE):
        raise PreflightFailure("schema_coverage_mismatch")
    if any(data_type != "text" or udt_name != "text" for data_type, udt_name in actual.values()):
        raise PreflightFailure("schema_type_mismatch")


def _blank_counts() -> dict[str, dict[str, int]]:
    return {
        field.field_code: {category: 0 for category in CATEGORY_LABELS}
        for field in FIELDS
    }


def _severity(category: str) -> str:
    if category in BLOCKER_CATEGORIES:
        return "BLOCKER"
    if category in REVIEW_CATEGORIES:
        return "REVIEW"
    return "INFO"


def _build_report(
    *,
    environment_label: str,
    postgres_version: str,
    schema_revision: str,
    row_counts: dict[str, int],
    aggregate_rows: Iterable[dict[str, Any]],
    sample_rows: Iterable[dict[str, Any]],
    hmac_key: bytes,
    sample_limit: int,
    code_revision: str,
) -> dict[str, Any]:
    counts = _blank_counts()
    for row in aggregate_rows:
        code = f"{row['table_name']}.{row['column_name']}"
        category = str(row["category"])
        if code not in counts or category not in CATEGORY_LABELS:
            raise PreflightFailure("classification_output_invalid")
        counts[code][category] = int(row["item_count"])

    samples: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in sample_rows:
        code = f"{row['table_name']}.{row['column_name']}"
        category = str(row["category"])
        if code not in FIELD_BY_CODE or category not in (BLOCKER_CATEGORIES | REVIEW_CATEGORIES):
            raise PreflightFailure("sample_output_invalid")
        key = (code, category)
        samples.setdefault(key, []).append(
            {
                "record_ref": _hmac_token(hmac_key, "record", f"{row['table_name']}:{row['row_id']}"),
                "content_fingerprint": _hmac_token(
                    hmac_key,
                    "content",
                    "" if row["value"] is None else str(row["value"]),
                ),
            }
        )

    field_results: list[dict[str, Any]] = []
    issue_index: list[dict[str, Any]] = []
    total_cells = 0
    blocker_items = 0
    review_items = 0

    for field in FIELDS:
        field_counts = counts[field.field_code]
        table_rows = row_counts.get(field.table, 0)
        classified_cells = sum(field_counts.values())
        if classified_cells != table_rows:
            raise PreflightFailure("classification_count_mismatch")
        total_cells += classified_cells

        field_results.append(
            {
                "field_ref": _hmac_token(hmac_key, "field", field.field_code),
                "area": field.area,
                "expected_shape": "列表" if field.expected_shape == "array" else "对象",
                "records_checked": table_rows,
                "classification_counts": field_counts,
            }
        )

        for category in sorted(BLOCKER_CATEGORIES | REVIEW_CATEGORIES):
            count = field_counts[category]
            if count == 0:
                continue
            severity = _severity(category)
            blocker_items += count if severity == "BLOCKER" else 0
            review_items += count if severity == "REVIEW" else 0
            issue_samples = samples.get((field.field_code, category), [])
            issue_index.append(
                {
                    "case_id": _hmac_token(
                        hmac_key,
                        "case",
                        f"{field.field_code}:{category}",
                    ),
                    "area": field.area,
                    "classification": category,
                    "classification_label": CATEGORY_LABELS[category],
                    "severity": severity,
                    "affected_records": count,
                    "recommended_action": CATEGORY_ACTIONS[category],
                    "approved_disposition": False,
                    "samples": issue_samples,
                    "samples_truncated": count > min(sample_limit, len(issue_samples)),
                }
            )

    data_status = BLOCKED if blocker_items else (REVIEW_REQUIRED if review_items else PASS)
    # Runtime, maintenance, and recovery evidence belongs to BUG-002B/C and remains
    # explicitly pending. Therefore this report alone never authorizes conversion.
    overall_status = BLOCKED if blocker_items else REVIEW_REQUIRED
    manager_action = (
        "按问题编号形成逐条处置方案，完成前不得执行转换"
        if blocker_items
        else (
            "确认兼容旧结构，并继续维护、备份和恢复门禁"
            if review_items
            else "数据形态检查通过；继续后续门禁，尚不得执行转换"
        )
    )

    total_records = sum(row_counts.values())
    summary_counts = {
        category: sum(field_counts[category] for field_counts in counts.values())
        for category in CATEGORY_LABELS
    }
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "run_id": uuid.uuid4().hex,
        "generated_at": datetime.now(UTC).isoformat(),
        "environment_label": environment_label,
        "mode": "read_only_preflight",
        "overall_status": overall_status,
        "data_shape_status": data_status,
        "manager_action": manager_action,
        "fixed_notice": "本报告未修改数据，也不构成真实转换批准",
        "write_performed": False,
        "coverage": {
            "planned_tables": EXPECTED_TABLE_COUNT,
            "planned_fields": EXPECTED_FIELD_COUNT,
            "checked_tables": len(row_counts),
            "checked_fields": len(field_results),
            "records_checked": total_records,
            "cells_checked": total_cells,
            "read_failures": 0,
            "schema_revision": schema_revision,
            "code_revision": code_revision,
            "frozen_recheck_completed": False,
            "previous_report_comparison": "not_checked",
        },
        "database_scope": {
            "dialect": "postgresql",
            "postgres_major": SUPPORTED_POSTGRES_MAJOR,
            "postgres_version": postgres_version,
            "connection_details_included": False,
        },
        "summary_counts": summary_counts,
        "runtime_conditions": {
            "schema_matches_legacy_text": "passed",
            "long_transactions": "not_checked",
            "blocking_locks": "not_checked",
            "table_sizes": "not_checked",
            "disk_capacity": "not_checked",
            "wal_impact": "not_checked",
            "replication_lag": "not_checked",
            "backup_available": "not_checked",
            "restore_drill": "not_checked",
        },
        "field_results": field_results,
        "issue_index": issue_index,
    }


def build_not_applicable_report(environment_label: str, code_revision: str) -> dict[str, Any]:
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "run_id": uuid.uuid4().hex,
        "generated_at": datetime.now(UTC).isoformat(),
        "environment_label": environment_label,
        "mode": "read_only_preflight",
        "overall_status": NOT_APPLICABLE,
        "data_shape_status": NOT_APPLICABLE,
        "manager_action": "SQLite 不存在本次 PostgreSQL 物理类型问题，无需执行预检",
        "fixed_notice": "本报告未修改数据，也不构成真实转换批准",
        "write_performed": False,
        "coverage": {
            "planned_tables": EXPECTED_TABLE_COUNT,
            "planned_fields": EXPECTED_FIELD_COUNT,
            "checked_tables": 0,
            "checked_fields": 0,
            "records_checked": 0,
            "cells_checked": 0,
            "read_failures": 0,
            "schema_revision": "not_checked",
            "code_revision": code_revision,
            "frozen_recheck_completed": False,
            "previous_report_comparison": "not_checked",
        },
        "database_scope": {
            "dialect": "sqlite",
            "connection_details_included": False,
        },
        "runtime_conditions": {
            "schema_matches_legacy_text": "not_applicable",
            "long_transactions": "not_applicable",
            "blocking_locks": "not_applicable",
            "table_sizes": "not_applicable",
            "disk_capacity": "not_applicable",
            "wal_impact": "not_applicable",
            "replication_lag": "not_applicable",
            "backup_available": "not_applicable",
            "restore_drill": "not_applicable",
        },
        "summary_counts": {category: 0 for category in CATEGORY_LABELS},
        "field_results": [],
        "issue_index": [],
    }


def build_error_report(
    environment_label: str,
    error_code: str,
    code_revision: str,
) -> dict[str, Any]:
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "run_id": uuid.uuid4().hex,
        "generated_at": datetime.now(UTC).isoformat(),
        "environment_label": environment_label,
        "mode": "read_only_preflight",
        "overall_status": ERROR,
        "data_shape_status": ERROR,
        "manager_action": "预检未完整运行；请按事件码检查配置或数据库结构",
        "fixed_notice": "本报告未修改数据，也不构成真实转换批准",
        "write_performed": False,
        "error": {
            "event_code": error_code,
            "connection_details_included": False,
        },
        "coverage": {
            "planned_tables": EXPECTED_TABLE_COUNT,
            "planned_fields": EXPECTED_FIELD_COUNT,
            "checked_tables": 0,
            "checked_fields": 0,
            "records_checked": 0,
            "cells_checked": 0,
            "read_failures": 1,
            "schema_revision": "unknown",
            "code_revision": code_revision,
            "frozen_recheck_completed": False,
            "previous_report_comparison": "not_checked",
        },
        "field_results": [],
        "issue_index": [],
    }


async def _begin_readonly_transaction(connection: AsyncConnection):
    transaction = await connection.begin()
    try:
        await connection.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        await connection.execute(text("SET LOCAL statement_timeout = '30s'"))
        await connection.execute(text("SET LOCAL lock_timeout = '1s'"))
        read_only = (await connection.execute(text("SHOW transaction_read_only"))).scalar_one()
        if str(read_only).lower() != "on":
            raise PreflightFailure("readonly_transaction_not_enforced")
    except Exception:
        await transaction.rollback()
        raise
    return transaction


async def run_preflight(
    *,
    database_url: str,
    environment_label: str,
    hmac_key: str | None,
    sample_limit: int = 10,
    code_revision: str = "unknown",
) -> dict[str, Any]:
    """Run the read-only preflight or return a no-op report for SQLite."""

    validate_field_whitelist()
    environment_label = validate_environment_label(environment_label)
    if sample_limit < 0 or sample_limit > 100:
        raise PreflightFailure("sample_limit_out_of_range")

    try:
        database_config = make_url(database_url)
        backend = database_config.get_backend_name()
    except Exception as exc:
        raise PreflightFailure("database_configuration_invalid") from exc

    if backend == "sqlite":
        return build_not_applicable_report(environment_label, code_revision)
    if backend != "postgresql":
        raise PreflightFailure("database_dialect_unsupported")
    if database_config.drivername != "postgresql+asyncpg":
        raise PreflightFailure("database_driver_unsupported")
    if not hmac_key:
        raise PreflightFailure("hmac_key_missing")
    encoded_hmac_key = validate_hmac_key(hmac_key)

    engine = create_async_engine(
        database_url,
        echo=False,
        poolclass=NullPool,
        connect_args={"server_settings": {"application_name": "novel_json_preflight"}},
    )
    try:
        async with engine.connect() as connection:
            transaction = await _begin_readonly_transaction(connection)
            try:
                version_num = int(
                    (await connection.execute(text("SHOW server_version_num"))).scalar_one()
                )
                postgres_major = version_num // 10000
                if postgres_major != SUPPORTED_POSTGRES_MAJOR:
                    raise PreflightFailure("postgres_version_unsupported")

                schema_rows = (
                    await connection.execute(text(SCHEMA_SQL))
                ).mappings()
                validate_schema_rows(schema_rows)

                revision_table = (
                    await connection.execute(
                        text("SELECT to_regclass(current_schema() || '.alembic_version')")
                    )
                ).scalar_one_or_none()
                if revision_table is None:
                    raise PreflightFailure("alembic_revision_missing")
                schema_revision = str(
                    (
                        await connection.execute(
                            text("SELECT version_num FROM alembic_version LIMIT 1")
                        )
                    ).scalar_one()
                )

                row_count_rows = (await connection.execute(text(ROW_COUNT_SQL))).mappings()
                row_counts = {
                    str(row["table_name"]): int(row["row_count"])
                    for row in row_count_rows
                }
                aggregate_rows = list(
                    (await connection.execute(text(AGGREGATE_SQL))).mappings()
                )
                sample_rows = list(
                    (
                        await connection.execute(
                            text(SAMPLE_SQL),
                            {"sample_limit": sample_limit},
                        )
                    ).mappings()
                )
                report = _build_report(
                    environment_label=environment_label,
                    postgres_version=str(version_num),
                    schema_revision=schema_revision,
                    row_counts=row_counts,
                    aggregate_rows=aggregate_rows,
                    sample_rows=sample_rows,
                    hmac_key=encoded_hmac_key,
                    sample_limit=sample_limit,
                    code_revision=code_revision,
                )
            finally:
                if transaction.is_active:
                    await transaction.rollback()
        return report
    finally:
        await engine.dispose()
