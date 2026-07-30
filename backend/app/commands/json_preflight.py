"""CLI for the read-only legacy JSON preflight."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from app.core.json_preflight import (
    CATEGORY_LABELS,
    ERROR,
    EXIT_CODES,
    PreflightFailure,
    build_error_report,
    run_preflight,
    validate_environment_label,
)

STATUS_LABELS = {
    "PASS": "通过",
    "REVIEW_REQUIRED": "需要确认",
    "BLOCKED": "阻断",
    "ERROR": "未完成",
    "NOT_APPLICABLE": "不适用",
}

CONDITION_LABELS = {
    "schema_matches_legacy_text": "历史结构匹配",
    "long_transactions": "长事务",
    "blocking_locks": "阻塞锁",
    "table_sizes": "表大小",
    "disk_capacity": "磁盘容量",
    "wal_impact": "日志写入影响",
    "replication_lag": "复制延迟",
    "backup_available": "可用备份",
    "restore_drill": "恢复演练",
}

VALUE_LABELS = {
    True: "是",
    False: "否",
    "passed": "通过",
    "not_checked": "未检查",
    "not_applicable": "不适用",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "只读检查历史项目资料的存储形态；不会修改数据，也不授权真实转换。"
        )
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="报告格式，默认 text",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="可选输出文件；使用 0600 权限原子写入，默认写到 stdout",
    )
    parser.add_argument(
        "--environment-label",
        default=os.getenv("APP_ENV_LABEL", "未指定环境"),
        help="不包含主机、数据库名或连接信息的环境别名",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=10,
        help="每个字段与问题分类最多输出的 HMAC 脱敏样本数（0-100）",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="兼容无颜色终端；当前输出始终不依赖颜色",
    )
    return parser


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _line(label: str, value: Any) -> str:
    return f"{label}: {value}"


def render_text(report: dict[str, Any], width: int = 80) -> str:
    status = report["overall_status"]
    status_label = STATUS_LABELS.get(status, "未知")
    data_status = report.get("data_shape_status", status)
    data_status_label = STATUS_LABELS.get(data_status, "未知")
    coverage = report.get("coverage", {})
    lines = [
        "历史项目资料只读预检",
        _line("状态", status_label),
        _line("数据形态状态", data_status_label),
        _line("影响", report["fixed_notice"]),
        _line("下一步", report["manager_action"]),
        _line("检查编号", report["run_id"]),
        _line("生成时间", report["generated_at"]),
        _line("环境", report["environment_label"]),
        "",
        "覆盖情况",
        _line("计划表数", coverage.get("planned_tables", 0)),
        _line("实际检查表数", coverage.get("checked_tables", 0)),
        _line("计划字段数", coverage.get("planned_fields", 0)),
        _line("实际检查字段数", coverage.get("checked_fields", 0)),
        _line("检查记录数", coverage.get("records_checked", 0)),
        _line("检查数据单元数", coverage.get("cells_checked", 0)),
        _line(
            "冻结后复检",
            VALUE_LABELS.get(
                coverage.get("frozen_recheck_completed", False),
                "未知",
            ),
        ),
        "",
    ]

    if report.get("summary_counts"):
        lines.append("分类统计")
        for category, count in report["summary_counts"].items():
            if count:
                lines.append(_line(CATEGORY_LABELS.get(category, category), count))
        lines.append("")

    runtime_conditions = report.get("runtime_conditions", {})
    lines.append("后续门禁")
    if not runtime_conditions:
        lines.append("未提供")
    for condition, value in runtime_conditions.items():
        lines.append(
            _line(
                CONDITION_LABELS.get(condition, condition),
                VALUE_LABELS.get(value, "未知"),
            )
        )
    lines.append("")

    issues = report.get("issue_index", [])
    lines.append("问题索引")
    if not issues:
        lines.append("无")
    for issue in issues:
        lines.extend(
            [
                _line("问题编号", issue["case_id"]),
                _line("区域", issue["area"]),
                _line("分类", issue["classification_label"]),
                _line("严重度", issue["severity"]),
                _line("影响记录数", issue["affected_records"]),
                _line("建议行动", issue["recommended_action"]),
                "",
            ]
        )

    lines.extend(
        [
            _line("最终状态", status_label),
            _line("最终下一步", report["manager_action"]),
        ]
    )
    wrapped: list[str] = []
    safe_width = max(40, width)
    for line in lines:
        if not line:
            wrapped.append("")
            continue
        wrapped.extend(
            textwrap.wrap(
                line,
                width=safe_width,
                replace_whitespace=False,
                drop_whitespace=False,
                break_long_words=True,
                break_on_hyphens=False,
            )
            or [""]
        )
    return "\n".join(wrapped) + "\n"


def _write_atomic(path: Path, content: str) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError("refusing to overwrite an existing report")
    parent = path.resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            os.chmod(temp_path, 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _terminal_width() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    code_revision = os.getenv("GIT_COMMIT_SHA", "unknown")
    safe_environment_label = "已隐藏不安全环境标签"
    report: dict[str, Any]
    try:
        safe_environment_label = validate_environment_label(args.environment_label)
        # Import only after parsing so --help has no settings or filesystem side effects.
        from app.config import settings

        report = asyncio.run(
            run_preflight(
                database_url=settings.DATABASE_URL,
                environment_label=safe_environment_label,
                hmac_key=settings.JSON_PREFLIGHT_HMAC_KEY,
                sample_limit=args.sample_limit,
                code_revision=code_revision,
            )
        )
    except PreflightFailure as exc:
        report = build_error_report(
            safe_environment_label,
            exc.code,
            code_revision,
        )
    except Exception:
        # Never serialize database/driver exceptions: they may contain connection
        # details, SQL fragments, or user content.
        report = build_error_report(
            safe_environment_label,
            "unexpected_preflight_failure",
            code_revision,
        )

    content = (
        render_json(report)
        if args.format == "json"
        else render_text(report, width=_terminal_width())
    )
    try:
        if args.output:
            _write_atomic(args.output, content)
        else:
            sys.stdout.write(content)
    except Exception:
        sys.stderr.write(
            "预检报告无法安全写入；未修改数据库，也未输出连接或小说内容。\n"
        )
        return EXIT_CODES[ERROR]
    return EXIT_CODES.get(report["overall_status"], EXIT_CODES[ERROR])


if __name__ == "__main__":
    raise SystemExit(main())
