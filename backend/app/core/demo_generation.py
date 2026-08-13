"""Exactly-once, zero-LLM execution for the isolated technical demo."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.demo_fixture_store import (
    DemoFixtureDivergedError,
    fixture_ids,
    validate_fixture_rows,
)
from app.core.demo_generation_content import (
    ADAPTER_SCHEMA_VERSION,
    CONTENT_SPEC_VERSION,
    TECHNICAL_DEMO_CONTENT,
)
from app.core.generation_preflight import (
    GenerationPreparationError,
    generation_run_response,
)
from app.core.maintenance import ensure_project_writes_available
from app.core.planning_write import operation_fingerprint
from app.models.generation import (
    ChapterGenerationCandidate,
    ChapterGenerationRun,
    ChapterTechnicalDemoExecution,
)
from app.models.planning import NovelPlan, PlanningChapter, PlanningPart
from app.models.project import Project
from app.schemas.demo_generation import (
    TechnicalDemoCandidateResponse,
    TechnicalDemoCapabilityResponse,
    TechnicalDemoExecutionResponse,
)

FIXTURE_VERSION = 1
_OPERATION_TYPE = "technical_demo_execute"
_MAX_CANDIDATE_BYTES = 262_144


class TechnicalDemoError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        retryable: bool = False,
        recommended_action: str,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = {
            "code": code,
            "message": message,
            "retryable": retryable,
            "recommended_action": recommended_action,
        }


class TechnicalDemoAdapter(Protocol):
    adapter_schema_version: int
    content_spec_version: int

    def render(self, manifest: dict[str, Any]) -> str: ...


@dataclass
class FixedTechnicalDemoAdapter:
    adapter_schema_version: int = ADAPTER_SCHEMA_VERSION
    content_spec_version: int = CONTENT_SPEC_VERSION

    def render(self, manifest: dict[str, Any]) -> str:
        del manifest
        return TECHNICAL_DEMO_CONTENT


def get_technical_demo_adapter() -> TechnicalDemoAdapter:
    return FixedTechnicalDemoAdapter()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _word_count(content: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]", content))


def _capability_snapshot(run: ChapterGenerationRun) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "execution_mode": "technical_demo",
        "fixture_version": FIXTURE_VERSION,
        "adapter_schema_version": ADAPTER_SCHEMA_VERSION,
        "content_spec_version": CONTENT_SPEC_VERSION,
        "project_id": run.project_id,
        "planning_chapter_id": run.planning_chapter_id,
        "run_id": run.id,
        "context_checksum": run.context_checksum,
        "fixed_response": True,
        "ai_invoked": False,
        "billing_effect": "none",
        "usage_status": "not_applicable",
    }


def _capability_response(run: ChapterGenerationRun) -> dict[str, Any]:
    snapshot = _capability_snapshot(run)
    return TechnicalDemoCapabilityResponse(
        **snapshot,
        capability_checksum=_checksum(
            {
                "capability": snapshot,
                "fixed_content_checksum": hashlib.sha256(
                    TECHNICAL_DEMO_CONTENT.encode()
                ).hexdigest(),
            }
        ),
    ).model_dump(mode="json")


async def _load_run(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    run_id: str,
    *,
    lock: bool,
) -> ChapterGenerationRun:
    ids = fixture_ids(user_id)
    if ids.project != project_id:
        raise TechnicalDemoError(
            "TECHNICAL_DEMO_FIXTURE_REQUIRED",
            "仅隔离的技术模拟样例可使用此功能。",
            status_code=404,
            recommended_action="return_to_projects",
        )
    try:
        await validate_fixture_rows(db, user_id)
    except DemoFixtureDivergedError as exc:
        raise TechnicalDemoError(
            "TECHNICAL_DEMO_FIXTURE_NOT_READY",
            "技术模拟样例已变化或不存在。",
            status_code=404,
            recommended_action="return_to_projects",
        ) from exc
    run = await db.scalar(
        select(ChapterGenerationRun).where(
            ChapterGenerationRun.project_id == project_id,
            ChapterGenerationRun.id == run_id,
            ChapterGenerationRun.requested_by == user_id,
            ChapterGenerationRun.planning_chapter_id == ids.chapter,
        )
    )
    if run is None:
        raise TechnicalDemoError(
            "TECHNICAL_DEMO_RUN_NOT_FOUND",
            "未找到第一章的技术模拟准备记录。",
            status_code=404,
            recommended_action="refresh_generation_preflight",
        )
    try:
        manifest = generation_run_response(run, replayed=True)["context_manifest"]
    except GenerationPreparationError as exc:
        raise _corrupt() from exc
    if (
        manifest["chapter"]["id"] != ids.chapter
        or manifest["counts"] != {"elements": 7, "relations": 3, "warnings": 0}
        or run.context_checksum != _checksum(manifest)
    ):
        raise TechnicalDemoError(
            "TECHNICAL_DEMO_CONTEXT_STALE",
            "技术模拟上下文与固定样例不一致，请重新检查。",
            recommended_action="refresh_generation_preflight",
        )
    if not lock:
        return run

    project = await db.scalar(
        select(Project)
        .where(Project.id == project_id, Project.owner_id == user_id)
        .with_for_update(read=True, key_share=True)
    )
    plan = await db.scalar(
        select(NovelPlan)
        .where(NovelPlan.project_id == project_id, NovelPlan.id == run.plan_id)
        .with_for_update()
    )
    chapter_identity = await db.scalar(
        select(PlanningChapter).where(
            PlanningChapter.project_id == project_id,
            PlanningChapter.id == ids.chapter,
        )
    )
    part = None
    chapter = None
    locked_run = None
    if chapter_identity is not None:
        part = await db.scalar(
            select(PlanningPart)
            .where(
                PlanningPart.project_id == project_id,
                PlanningPart.id == chapter_identity.part_id,
            )
            .with_for_update()
        )
        chapter = await db.scalar(
            select(PlanningChapter)
            .where(
                PlanningChapter.project_id == project_id,
                PlanningChapter.id == ids.chapter,
            )
            .with_for_update()
        )
        locked_run = await db.scalar(
            select(ChapterGenerationRun)
            .where(
                ChapterGenerationRun.project_id == project_id,
                ChapterGenerationRun.id == run_id,
                ChapterGenerationRun.requested_by == user_id,
            )
            .with_for_update()
        )
    if project is None:
        raise TechnicalDemoError(
            "TECHNICAL_DEMO_PROJECT_NOT_FOUND",
            "技术模拟样例项目不存在。",
            status_code=404,
            recommended_action="return_to_projects",
        )
    if any(value is None for value in (plan, part, chapter, locked_run)):
        raise _corrupt()
    if (
        plan.status != "active"
        or part.status != "active"
        or chapter.status != "active"
        or plan.structure_version != locked_run.structure_version
        or plan.assignment_version != locked_run.assignment_version
        or chapter.lock_version != locked_run.chapter_lock_version
    ):
        raise TechnicalDemoError(
            "TECHNICAL_DEMO_PREFLIGHT_STALE",
            "规划已变化，请重新检查第一章上下文。",
            recommended_action="refresh_generation_preflight",
        )
    return locked_run


def _corrupt() -> TechnicalDemoError:
    return TechnicalDemoError(
        "TECHNICAL_DEMO_RECORD_CORRUPT",
        "技术模拟记录不完整，系统已停止自动处理。",
        recommended_action="contact_support",
    )


async def technical_demo_capability_response(
    db: AsyncSession, project_id: str, user_id: str, run_id: str
) -> dict[str, Any]:
    run = await _load_run(db, project_id, user_id, run_id, lock=False)
    return _capability_response(run)


def technical_demo_request_fingerprint(
    project_id: str,
    run_id: str,
    expected_context_checksum: str,
    expected_capability_checksum: str,
) -> str:
    return operation_fingerprint(
        project_id,
        _OPERATION_TYPE,
        run_id,
        {
            "fixture_version": FIXTURE_VERSION,
            "adapter_schema_version": ADAPTER_SCHEMA_VERSION,
            "content_spec_version": CONTENT_SPEC_VERSION,
            "expected_context_checksum": expected_context_checksum,
            "expected_capability_checksum": expected_capability_checksum,
            "confirm_technical_demo": True,
        },
    )


async def find_technical_demo_execution_by_key(
    db: AsyncSession, project_id: str, user_id: str, operation_key: str
) -> ChapterTechnicalDemoExecution | None:
    return await db.scalar(
        select(ChapterTechnicalDemoExecution).where(
            ChapterTechnicalDemoExecution.project_id == project_id,
            ChapterTechnicalDemoExecution.requested_by == user_id,
            ChapterTechnicalDemoExecution.operation_key == operation_key,
        )
    )


async def technical_demo_execution_response(
    db: AsyncSession,
    execution: ChapterTechnicalDemoExecution,
    *,
    replayed: bool,
) -> dict[str, Any]:
    run, manifest = await _validated_technical_execution_context(db, execution)
    candidate = await db.scalar(
        select(ChapterGenerationCandidate).where(
            ChapterGenerationCandidate.project_id == execution.project_id,
            ChapterGenerationCandidate.run_id == execution.run_id,
            ChapterGenerationCandidate.source_technical_demo_execution_id
            == execution.id,
        )
    )
    if candidate is None:
        raise _corrupt()
    _validate_technical_candidate(candidate, execution, manifest)
    snapshot = {
        "fixture_version": execution.fixture_version,
        "adapter_schema_version": execution.adapter_schema_version,
        "content_spec_version": execution.content_spec_version,
        "project_id": execution.project_id,
        "planning_chapter_id": run.planning_chapter_id,
        "run_id": execution.run_id,
        "operation_key": execution.operation_key,
        "context_checksum": execution.context_checksum,
        "capability_checksum": execution.capability_checksum,
        "execution_id": execution.id,
        "candidate_id": candidate.id,
        "status": execution.status,
        "replayed": replayed,
        "ai_invoked": execution.ai_invoked,
        "billing_effect": execution.billing_effect,
        "usage_status": execution.usage_status,
        "created_at": execution.created_at,
        "completed_at": execution.completed_at,
    }
    try:
        return TechnicalDemoExecutionResponse.model_validate(snapshot).model_dump(
            mode="json"
        )
    except ValidationError as exc:
        raise _corrupt() from exc


async def _validated_technical_execution_context(
    db: AsyncSession,
    execution: ChapterTechnicalDemoExecution,
) -> tuple[ChapterGenerationRun, dict[str, Any]]:
    run = await db.scalar(
        select(ChapterGenerationRun).where(
            ChapterGenerationRun.project_id == execution.project_id,
            ChapterGenerationRun.id == execution.run_id,
            ChapterGenerationRun.requested_by == execution.requested_by,
        )
    )
    if run is None:
        raise _corrupt()
    try:
        manifest = generation_run_response(run, replayed=True)["context_manifest"]
    except GenerationPreparationError as exc:
        raise _corrupt() from exc
    capability = _capability_response(run)
    expected_fingerprint = technical_demo_request_fingerprint(
        execution.project_id,
        execution.run_id,
        execution.context_checksum,
        execution.capability_checksum,
    )
    if (
        execution.status != "succeeded"
        or execution.execution_mode != "technical_demo"
        or execution.ai_invoked is not False
        or execution.billing_effect != "none"
        or execution.usage_status != "not_applicable"
        or execution.fixture_version != FIXTURE_VERSION
        or execution.adapter_schema_version != ADAPTER_SCHEMA_VERSION
        or execution.content_spec_version != CONTENT_SPEC_VERSION
        or execution.context_checksum != run.context_checksum
        or execution.capability_checksum != capability["capability_checksum"]
        or execution.request_fingerprint != expected_fingerprint
        or execution.completed_at < execution.created_at
    ):
        raise _corrupt()
    return run, manifest


def _validate_technical_candidate(
    candidate: ChapterGenerationCandidate,
    execution: ChapterTechnicalDemoExecution,
    manifest: dict[str, Any],
) -> None:
    content_bytes = candidate.content.encode()
    if (
        candidate.project_id != execution.project_id
        or candidate.run_id != execution.run_id
        or candidate.origin_kind != "technical_demo"
        or candidate.source_attempt_id is not None
        or candidate.source_technical_demo_execution_id != execution.id
        or candidate.parent_candidate_id is not None
        or candidate.created_by != execution.requested_by
        or candidate.title != manifest["chapter"]["title"]
        or candidate.content_format != "plain_text"
        or not candidate.content.strip()
        or not 1 <= candidate.content_size_bytes <= _MAX_CANDIDATE_BYTES
        or candidate.content_size_bytes != len(content_bytes)
        or candidate.content_checksum != hashlib.sha256(content_bytes).hexdigest()
        or candidate.word_count != _word_count(candidate.content)
        or candidate.word_count < 1
        or candidate.version_no < 1
    ):
        raise _corrupt()


async def execute_technical_demo(
    *,
    db: AsyncSession,
    project_id: str,
    user_id: str,
    run_id: str,
    operation_key: str,
    expected_context_checksum: str,
    expected_capability_checksum: str,
    adapter: TechnicalDemoAdapter,
) -> dict[str, Any]:
    fingerprint = technical_demo_request_fingerprint(
        project_id,
        run_id,
        expected_context_checksum,
        expected_capability_checksum,
    )
    existing = await find_technical_demo_execution_by_key(
        db, project_id, user_id, operation_key
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint or existing.run_id != run_id:
            raise TechnicalDemoError(
                "TECHNICAL_DEMO_OPERATION_CONFLICT",
                "该技术模拟编号已用于其他请求。",
                recommended_action="start_new_technical_demo",
            )
        return await technical_demo_execution_response(db, existing, replayed=True)

    ensure_project_writes_available()
    run = await _load_run(db, project_id, user_id, run_id, lock=True)
    capability = _capability_response(run)
    if (
        run.context_checksum != expected_context_checksum
        or capability["capability_checksum"] != expected_capability_checksum
        or adapter.adapter_schema_version != ADAPTER_SCHEMA_VERSION
        or adapter.content_spec_version != CONTENT_SPEC_VERSION
    ):
        raise TechnicalDemoError(
            "TECHNICAL_DEMO_CONFIRMATION_STALE",
            "技术模拟确认已失效，请重新核对。",
            recommended_action="refresh_technical_demo_capability",
        )
    now = _now()
    execution = ChapterTechnicalDemoExecution(
        id=uuid.uuid4().hex,
        project_id=project_id,
        run_id=run_id,
        requested_by=user_id,
        operation_key=operation_key,
        request_fingerprint=fingerprint,
        status="succeeded",
        execution_mode="technical_demo",
        ai_invoked=False,
        billing_effect="none",
        usage_status="not_applicable",
        fixture_version=FIXTURE_VERSION,
        adapter_schema_version=ADAPTER_SCHEMA_VERSION,
        content_spec_version=CONTENT_SPEC_VERSION,
        context_checksum=run.context_checksum,
        capability_checksum=capability["capability_checksum"],
        created_at=now,
        completed_at=now,
    )
    db.add(execution)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        winner = await find_technical_demo_execution_by_key(
            db, project_id, user_id, operation_key
        )
        if winner is None or winner.request_fingerprint != fingerprint:
            raise TechnicalDemoError(
                "TECHNICAL_DEMO_OPERATION_CONFLICT",
                "技术模拟编号冲突，未创建新候选。",
                recommended_action="start_new_technical_demo",
            )
        return await technical_demo_execution_response(db, winner, replayed=True)

    manifest = generation_run_response(run, replayed=True)["context_manifest"]
    try:
        content = adapter.render(manifest).strip()
    except Exception as exc:
        await db.rollback()
        raise TechnicalDemoError(
            "TECHNICAL_DEMO_ADAPTER_UNAVAILABLE",
            "固定技术模拟内容暂时无法生成，本次未保存执行或候选。",
            status_code=503,
            retryable=True,
            recommended_action="start_new_confirmed_technical_demo",
        ) from exc
    content_bytes = content.encode()
    word_count = _word_count(content)
    if not content or not word_count or len(content_bytes) > _MAX_CANDIDATE_BYTES:
        await db.rollback()
        raise TechnicalDemoError(
            "TECHNICAL_DEMO_CONTENT_INVALID",
            "固定技术模拟内容无法安全保存。",
            recommended_action="contact_support",
        )
    next_version = (
        await db.scalar(
            select(func.max(ChapterGenerationCandidate.version_no)).where(
                ChapterGenerationCandidate.project_id == project_id,
                ChapterGenerationCandidate.run_id == run_id,
            )
        )
        or 0
    ) + 1
    candidate = ChapterGenerationCandidate(
        id=uuid.uuid4().hex,
        project_id=project_id,
        run_id=run_id,
        source_attempt_id=None,
        source_technical_demo_execution_id=execution.id,
        parent_candidate_id=None,
        version_no=next_version,
        origin_kind="technical_demo",
        title=manifest["chapter"]["title"],
        content=content,
        content_format="plain_text",
        content_checksum=hashlib.sha256(content_bytes).hexdigest(),
        content_size_bytes=len(content_bytes),
        word_count=word_count,
        created_by=user_id,
        created_at=now,
    )
    db.add(candidate)
    try:
        ensure_project_writes_available()
        await db.flush()
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return await technical_demo_execution_response(db, execution, replayed=False)


async def technical_demo_candidate_response(
    db: AsyncSession,
    candidate: ChapterGenerationCandidate,
    *,
    user_id: str,
) -> dict[str, Any]:
    execution = await db.scalar(
        select(ChapterTechnicalDemoExecution).where(
            ChapterTechnicalDemoExecution.project_id == candidate.project_id,
            ChapterTechnicalDemoExecution.id
            == candidate.source_technical_demo_execution_id,
            ChapterTechnicalDemoExecution.run_id == candidate.run_id,
            ChapterTechnicalDemoExecution.requested_by == user_id,
        )
    )
    if execution is None:
        raise _corrupt()
    run, manifest = await _validated_technical_execution_context(db, execution)
    _validate_technical_candidate(candidate, execution, manifest)
    snapshot = {
        "id": candidate.id,
        "project_id": candidate.project_id,
        "run_id": candidate.run_id,
        "planning_chapter_id": run.planning_chapter_id,
        "source_technical_demo_execution_id": execution.id,
        "parent_candidate_id": None,
        "version_no": candidate.version_no,
        "origin_kind": candidate.origin_kind,
        "title": candidate.title,
        "content": candidate.content,
        "content_format": candidate.content_format,
        "content_checksum": candidate.content_checksum,
        "content_size_bytes": candidate.content_size_bytes,
        "word_count": candidate.word_count,
        "created_by": candidate.created_by,
        "ai_invoked": execution.ai_invoked,
        "billing_effect": execution.billing_effect,
        "usage_status": execution.usage_status,
        "created_at": candidate.created_at,
    }
    try:
        return TechnicalDemoCandidateResponse.model_validate(snapshot).model_dump(
            mode="json"
        )
    except ValidationError as exc:
        raise _corrupt() from exc
