#!/usr/bin/env python3
"""Fail-closed health check for the isolated final-demo environment."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


EXPECTED_COUNTS = {
    "setting_type_count": 6,
    "element_count": 7,
    "source_count": 7,
    "relation_count": 3,
    "part_count": 1,
    "chapter_count": 2,
    "assignment_count": 7,
    "foreshadow_lifecycle_count": 1,
    "foreshadow_plan_count": 2,
    "foreshadow_fact_count": 0,
}

ZERO_WRITE_QUERIES = {
    "chapter_generation_runs": 'SELECT COUNT(*) FROM "chapter_generation_runs"',
    "chapter_generation_attempts": 'SELECT COUNT(*) FROM "chapter_generation_attempts"',
    "chapter_technical_demo_executions": 'SELECT COUNT(*) FROM "chapter_technical_demo_executions"',
    "chapter_generation_candidates": 'SELECT COUNT(*) FROM "chapter_generation_candidates"',
    "chapter_generation_candidate_selection_operations": (
        'SELECT COUNT(*) FROM "chapter_generation_candidate_selection_operations"'
    ),
    "chapter_generation_candidate_selections": (
        'SELECT COUNT(*) FROM "chapter_generation_candidate_selections"'
    ),
    "foreshadow_facts": 'SELECT COUNT(*) FROM "foreshadow_facts"',
    "chapters": 'SELECT COUNT(*) FROM "chapters"',
}

REQUIRED_DEMO_PATHS = (
    "/api/demo/v1/projects/{project_id}/planning/generation-runs/{run_id}/technical-generation-capability",
    "/api/demo/v1/projects/{project_id}/planning/generation-runs/{run_id}/technical-demo-executions",
    "/api/demo/v1/projects/{project_id}/planning/technical-demo-executions/by-key/{operation_key}",
    "/api/projects/{project_id}/planning/chapters/{chapter_id}/candidate-selection",
    "/api/projects/{project_id}/planning/chapters/{chapter_id}/candidate-selection-operations",
)


class CheckError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--frontend-url", required=True)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--credentials-file", required=True, type=Path)
    parser.add_argument("--initialize-user", action="store_true")
    parser.add_argument("--bootstrap-if-missing", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=30.0)
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckError(message)


def require_loopback_http(url: str) -> None:
    parsed = urlsplit(url)
    require(parsed.scheme == "http", "健康检查只允许 loopback HTTP")
    require(parsed.hostname in {"127.0.0.1", "localhost"}, "健康检查只允许 loopback 主机")
    require(parsed.username is None and parsed.password is None, "URL 不得包含凭据")


def read_credentials(path: Path) -> dict[str, str]:
    require(path.is_file() and not path.is_symlink(), "凭据文件缺失或是符号链接")
    if os.name == "posix":
        require(path.stat().st_mode & 0o077 == 0, "凭据文件权限必须为 0600")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckError("无法读取凭据文件") from exc
    require(isinstance(value, dict), "凭据文件格式错误")
    expected = {"run_id", "email", "username", "password"}
    require(set(value) == expected, "凭据文件字段不精确")
    require(all(isinstance(value[key], str) and value[key] for key in expected), "凭据字段为空")
    return value


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    expected: tuple[int, ...] = (200,),
    timeout: float = 5.0,
) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        require_loopback_http(url)
        # The URL is restricted to loopback HTTP immediately above.
        with urlopen(request, timeout=timeout) as response:  # nosec B310
            status = response.status
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8", errors="replace")
    except (URLError, TimeoutError, OSError) as exc:
        raise CheckError(f"请求失败：{url}") from exc
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise CheckError(f"响应不是 JSON：{url}") from exc
    require(status in expected, f"HTTP {status}：{url}")
    return status, value


def wait_for_json(url: str, expected_value: dict[str, Any], wait_seconds: float) -> None:
    deadline = time.monotonic() + wait_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            _, value = request_json(url, timeout=1.5)
            require(value == expected_value, f"健康响应不精确：{value!r}")
            return
        except CheckError as exc:
            last_error = exc
            time.sleep(0.25)
    raise CheckError(f"服务未在 {wait_seconds:g} 秒内就绪") from last_error


def wait_for_frontend(url: str, wait_seconds: float) -> None:
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            request = Request(url, headers={"Accept": "text/html"})
            require_loopback_http(url)
            # The URL is restricted to loopback HTTP immediately above.
            with urlopen(request, timeout=1.5) as response:  # nosec B310
                body = response.read().decode("utf-8", errors="replace")
                if response.status == 200 and "<div id=\"root\"></div>" in body:
                    return
        except (HTTPError, URLError, TimeoutError, OSError):
            pass
        time.sleep(0.25)
    raise CheckError(f"前端未在 {wait_seconds:g} 秒内就绪")


def sqlite_counts(database: Path) -> dict[str, int]:
    require(database.is_file() and not database.is_symlink(), "SQLite 文件缺失或是符号链接")
    uri = f"file:{database.resolve()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            missing = set(ZERO_WRITE_QUERIES) - tables
            require(not missing, f"数据库缺少预期表：{sorted(missing)}")
            return {
                table: int(connection.execute(query).fetchone()[0])
                for table, query in ZERO_WRITE_QUERIES.items()
            }
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise CheckError("SQLite 只读检查失败") from exc


def main() -> int:
    args = parse_args()
    backend_api_base = args.api_base.rstrip("/")
    frontend_url = args.frontend_url.rstrip("/") + "/"
    browser_api_base = frontend_url.rstrip("/") + "/api"
    credentials = read_credentials(args.credentials_file)
    bootstrap_posts = 0

    wait_for_json(f"{backend_api_base}/health", {"status": "ok"}, args.wait_seconds)
    wait_for_frontend(frontend_url, args.wait_seconds)
    wait_for_json(f"{browser_api_base}/health", {"status": "ok"}, args.wait_seconds)

    auth_payload = {
        "email": credentials["email"],
        "username": credentials["username"],
        "password": credentials["password"],
    }
    if args.initialize_user:
        status, _ = request_json(
            f"{browser_api_base}/auth/register",
            method="POST",
            payload=auth_payload,
            expected=(200, 409),
        )
        require(status in {200, 409}, "注册状态异常")

    _, login = request_json(
        f"{browser_api_base}/auth/login",
        method="POST",
        payload={"email": credentials["email"], "password": credentials["password"]},
    )
    require(isinstance(login, dict) and isinstance(login.get("token"), str), "登录响应缺少 token")
    token = login["token"]
    _, me = request_json(f"{browser_api_base}/auth/me", token=token)
    require(me.get("email") == credentials["email"], "登录账号与凭据不一致")
    require(me.get("username") == credentials["username"], "登录用户名与凭据不一致")

    _, fixture = request_json(f"{browser_api_base}/demo/v1/fixture", token=token)
    state = fixture.get("state")
    if state == "missing":
        require(args.bootstrap_if_missing, "fixture 缺失，未授权 bootstrap")
        request_json(
            f"{browser_api_base}/demo/v1/bootstrap",
            method="POST",
            payload={"fixture_version": 1, "operation_key": "demo:v1:bootstrap"},
            token=token,
        )
        bootstrap_posts = 1
        _, fixture = request_json(f"{browser_api_base}/demo/v1/fixture", token=token)
        state = fixture.get("state")
    require(state == "ready", f"fixture 未就绪：{state!r}")
    require(fixture.get("counts") == EXPECTED_COUNTS, "fixture 计数不精确")
    require(fixture.get("can_bootstrap") is False, "ready fixture 不应允许 bootstrap")
    require(fixture.get("preserved") is False, "ready fixture 不应处于 preserved")

    anchors = (
        "project_id",
        "plan_id",
        "part_id",
        "chapter_id",
        "element_id",
        "assignment_id",
        "second_chapter_id",
        "foreshadow_element_id",
        "foreshadow_lifecycle_id",
    )
    require(all(isinstance(fixture.get(key), str) and len(fixture[key]) == 32 for key in anchors), "fixture 锚点无效")
    project_id = fixture["project_id"]

    _, project = request_json(f"{browser_api_base}/projects/{project_id}", token=token)
    require(project.get("id") == project_id, "项目详情身份不一致")

    _, lore = request_json(
        f"{browser_api_base}/projects/{project_id}/lore/elements?limit=30", token=token
    )
    require(isinstance(lore.get("items"), list) and len(lore["items"]) == 7, "Lore 列表数量异常")
    require(any(item.get("id") == fixture["element_id"] for item in lore["items"]), "Lore 深链元素缺失")

    _, planning = request_json(f"{browser_api_base}/projects/{project_id}/planning", token=token)
    require(planning.get("id") == fixture["plan_id"], "规划身份不一致")
    chapters = [chapter for part in planning.get("parts", []) for chapter in part.get("chapters", [])]
    require(len(chapters) == 2, "规划章节数量异常")
    require({chapter.get("id") for chapter in chapters} == {fixture["chapter_id"], fixture["second_chapter_id"]}, "规划章节锚点异常")

    _, foreshadows = request_json(
        f"{browser_api_base}/projects/{project_id}/planning/foreshadows?limit=50",
        token=token,
    )
    require(len(foreshadows.get("items", [])) == 1, "伏笔生命周期数量异常")
    require(foreshadows["items"][0].get("id") == fixture["foreshadow_lifecycle_id"], "伏笔深链锚点异常")

    _, openapi = request_json(backend_api_base.removesuffix("/api") + "/openapi.json")
    paths = openapi.get("paths", {})
    require(all(path in paths for path in REQUIRED_DEMO_PATHS), "Demo 所需 API 路由缺失")

    zero_counts = sqlite_counts(args.database)
    require(all(value == 0 for value in zero_counts.values()), f"Final fixture 已包含演示写入：{zero_counts}")

    result = {
        "schema_version": 1,
        "run_id": credentials["run_id"],
        "state": "ready",
        "bootstrap_posts": bootstrap_posts,
        "project_id": project_id,
        "counts": EXPECTED_COUNTS,
        "no_llm": True,
        "invariants": zero_counts,
        "frontend_url": frontend_url,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CheckError as exc:
        print(f"demo healthcheck failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
