"""Strict immutable candidate lineage reads and version workspace responses."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.generation_execution import (
    MAX_CANDIDATE_BYTES,
    GenerationExecutionError,
    generation_attempt_response,
)
from app.core.generation_preflight import (
    GenerationPreparationError,
    generation_run_response,
)
from app.core.maintenance import ensure_project_writes_available
from app.core.planning_write import operation_fingerprint
from app.models.generation import (
    ChapterGenerationAttempt,
    ChapterGenerationCandidate,
    ChapterGenerationRun,
)
from app.models.project import Project
from app.schemas.generation import (
    GenerationCandidateManualEditResponse,
    GenerationCandidateVersionDetail,
    GenerationCandidateVersionListItem,
    GenerationCandidateVersionListResponse,
)


MAX_CANDIDATE_LINEAGE_DEPTH = 100
_MANUAL_EDIT_OPERATION_TYPE = "generation_candidate_manual_edit_v1"
_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]")


def _word_count(content: str) -> int:
    return len(_WORD_PATTERN.findall(content))


def manual_edit_candidate_id(
    *, user_id: str, project_id: str, operation_key: str
) -> str:
    return hashlib.sha256(
        f"{user_id}:{project_id}:{operation_key}".encode("utf-8")
    ).hexdigest()[:32]


def manual_edit_request_fingerprint(
    *,
    project_id: str,
    user_id: str,
    run_id: str,
    operation_key: str,
    parent_candidate_id: str,
    expected_parent_version_no: int,
    expected_parent_checksum: str,
    expected_context_checksum: str,
    content: str,
) -> str:
    return operation_fingerprint(
        project_id,
        _MANUAL_EDIT_OPERATION_TYPE,
        manual_edit_candidate_id(
            user_id=user_id,
            project_id=project_id,
            operation_key=operation_key,
        ),
        {
            "run_id": run_id,
            "parent_candidate_id": parent_candidate_id,
            "expected_parent_version_no": expected_parent_version_no,
            "expected_parent_checksum": expected_parent_checksum,
            "expected_context_checksum": expected_context_checksum,
            "content_checksum": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content_size_bytes": len(content.encode("utf-8")),
        },
    )


def _corrupt() -> GenerationExecutionError:
    return GenerationExecutionError(
        "GENERATION_CANDIDATE_CORRUPT",
        "章节候选版本链不完整，已停止展示候选。",
        status_code=409,
        recommended_action="reload_generation_candidate",
    )


@dataclass(frozen=True)
class CandidateLineage:
    run_snapshot: dict[str, Any]
    root_candidate_id: str
    root_origin_kind: Literal["generated", "technical_demo"]
    parent_version_no: int | None
    ai_invoked_for_this_version: bool
    billing_effect_for_this_version: Literal["none", "possible"]
    usage_status_for_this_version: Literal["reported", "unavailable", "not_applicable"]


async def _strict_run_snapshot(
    db: AsyncSession,
    *,
    project_id: str,
    run_id: str,
    user_id: str,
) -> tuple[ChapterGenerationRun, dict[str, Any]]:
    run = await db.scalar(
        select(ChapterGenerationRun).where(
            ChapterGenerationRun.project_id == project_id,
            ChapterGenerationRun.id == run_id,
            ChapterGenerationRun.requested_by == user_id,
        )
    )
    if run is None:
        raise _corrupt()
    try:
        return run, generation_run_response(run, replayed=True)
    except GenerationPreparationError as exc:
        raise _corrupt() from exc


async def _list_run_snapshot(
    db: AsyncSession,
    *,
    project_id: str,
    run_id: str,
    user_id: str,
) -> dict[str, Any]:
    run = await db.scalar(
        select(ChapterGenerationRun)
        .join(Project, Project.id == ChapterGenerationRun.project_id)
        .where(
            ChapterGenerationRun.project_id == project_id,
            ChapterGenerationRun.id == run_id,
            ChapterGenerationRun.requested_by == user_id,
            Project.owner_id == user_id,
        )
    )
    if run is None:
        raise GenerationExecutionError(
            "GENERATION_RUN_NOT_FOUND",
            "未找到该生成准备记录。",
            status_code=404,
            recommended_action="return_to_chapter_planning",
        )
    try:
        return generation_run_response(run, replayed=True)
    except GenerationPreparationError as exc:
        raise _corrupt() from exc


def _validate_common_candidate(
    candidate: ChapterGenerationCandidate,
    *,
    project_id: str,
    run_id: str,
    user_id: str,
    chapter_title: str,
) -> None:
    try:
        content_bytes = candidate.content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _corrupt() from exc
    if (
        len(candidate.id) != 32
        or candidate.project_id != project_id
        or candidate.run_id != run_id
        or candidate.created_by != user_id
        or candidate.title != chapter_title
        or candidate.content_format != "plain_text"
        or not candidate.content.strip()
        or candidate.content_size_bytes != len(content_bytes)
        or not 1 <= candidate.content_size_bytes <= MAX_CANDIDATE_BYTES
        or candidate.content_checksum != hashlib.sha256(content_bytes).hexdigest()
        or candidate.word_count != _word_count(candidate.content)
        or candidate.word_count < 1
        or candidate.version_no < 1
    ):
        raise _corrupt()


async def validate_candidate_lineage(
    db: AsyncSession,
    candidate: ChapterGenerationCandidate,
    *,
    user_id: str,
) -> CandidateLineage:
    """Validate every immutable node through its generated/technical root."""

    _, run_snapshot = await _strict_run_snapshot(
        db,
        project_id=candidate.project_id,
        run_id=candidate.run_id,
        user_id=user_id,
    )
    chapter_title = run_snapshot["context_manifest"]["chapter"]["title"]
    target = candidate
    current = candidate
    parent_version_no: int | None = None
    seen: set[str] = set()

    for _ in range(MAX_CANDIDATE_LINEAGE_DEPTH):
        if current.id in seen:
            raise _corrupt()
        seen.add(current.id)
        _validate_common_candidate(
            current,
            project_id=target.project_id,
            run_id=target.run_id,
            user_id=user_id,
            chapter_title=chapter_title,
        )

        if current.origin_kind == "manual_edit":
            if (
                current.source_attempt_id is not None
                or current.source_technical_demo_execution_id is not None
                or current.parent_candidate_id is None
            ):
                raise _corrupt()
            parent = await db.scalar(
                select(ChapterGenerationCandidate).where(
                    ChapterGenerationCandidate.project_id == target.project_id,
                    ChapterGenerationCandidate.run_id == target.run_id,
                    ChapterGenerationCandidate.id == current.parent_candidate_id,
                )
            )
            if (
                parent is None
                or parent.version_no >= current.version_no
                or parent.content == current.content
                or parent.created_at > current.created_at
            ):
                raise _corrupt()
            if current.id == target.id:
                parent_version_no = parent.version_no
            current = parent
            continue

        if current.parent_candidate_id is not None:
            raise _corrupt()
        if current.origin_kind == "generated":
            if (
                current.source_attempt_id is None
                or current.source_technical_demo_execution_id is not None
            ):
                raise _corrupt()
            attempt = await db.scalar(
                select(ChapterGenerationAttempt).where(
                    ChapterGenerationAttempt.project_id == target.project_id,
                    ChapterGenerationAttempt.run_id == target.run_id,
                    ChapterGenerationAttempt.id == current.source_attempt_id,
                    ChapterGenerationAttempt.requested_by == user_id,
                )
            )
            if attempt is None:
                raise _corrupt()
            attempt_snapshot = await generation_attempt_response(
                db, attempt, replayed=True
            )
            if (
                attempt_snapshot["status"] != "succeeded"
                or attempt_snapshot["candidate_id"] != current.id
                or attempt_snapshot["ai_invoked"] is not True
                or attempt_snapshot["billing_effect"] != "possible"
                or attempt_snapshot["usage"]["status"]
                not in {"reported", "unavailable"}
            ):
                raise _corrupt()
            root_kind: Literal["generated", "technical_demo"] = "generated"
            root_usage = attempt_snapshot["usage"]["status"]
        elif current.origin_kind == "technical_demo":
            if (
                current.source_attempt_id is not None
                or current.source_technical_demo_execution_id is None
            ):
                raise _corrupt()
            from app.core.demo_generation import (
                TechnicalDemoError,
                technical_demo_candidate_response,
            )

            try:
                technical_snapshot = await technical_demo_candidate_response(
                    db, current, user_id=user_id
                )
            except TechnicalDemoError as exc:
                raise _corrupt() from exc
            if (
                technical_snapshot["id"] != current.id
                or technical_snapshot["ai_invoked"] is not False
                or technical_snapshot["billing_effect"] != "none"
                or technical_snapshot["usage_status"] != "not_applicable"
            ):
                raise _corrupt()
            root_kind = "technical_demo"
            root_usage = "not_applicable"
        else:
            raise _corrupt()

        is_manual = target.origin_kind == "manual_edit"
        return CandidateLineage(
            run_snapshot=run_snapshot,
            root_candidate_id=current.id,
            root_origin_kind=root_kind,
            parent_version_no=parent_version_no,
            ai_invoked_for_this_version=(
                False if is_manual else root_kind == "generated"
            ),
            billing_effect_for_this_version=(
                "none" if is_manual or root_kind == "technical_demo" else "possible"
            ),
            usage_status_for_this_version=(
                "not_applicable" if is_manual else root_usage
            ),
        )

    raise _corrupt()


async def candidate_version_detail_response(
    db: AsyncSession,
    candidate: ChapterGenerationCandidate,
    *,
    user_id: str,
) -> dict[str, Any]:
    lineage = await validate_candidate_lineage(db, candidate, user_id=user_id)
    snapshot = {
        "id": candidate.id,
        "project_id": candidate.project_id,
        "run_id": candidate.run_id,
        "planning_chapter_id": lineage.run_snapshot["planning_chapter_id"],
        "version_no": candidate.version_no,
        "origin_kind": candidate.origin_kind,
        "parent_candidate_id": candidate.parent_candidate_id,
        "parent_version_no": lineage.parent_version_no,
        "root_candidate_id": lineage.root_candidate_id,
        "root_origin_kind": lineage.root_origin_kind,
        "ai_invoked_for_this_version": lineage.ai_invoked_for_this_version,
        "billing_effect_for_this_version": lineage.billing_effect_for_this_version,
        "usage_status_for_this_version": lineage.usage_status_for_this_version,
        "title": candidate.title,
        "content": candidate.content,
        "content_format": candidate.content_format,
        "content_checksum": candidate.content_checksum,
        "content_size_bytes": candidate.content_size_bytes,
        "word_count": candidate.word_count,
        "created_by": candidate.created_by,
        "created_at": candidate.created_at,
    }
    try:
        return GenerationCandidateVersionDetail.model_validate(snapshot).model_dump(
            mode="json"
        )
    except ValidationError as exc:
        raise _corrupt() from exc


async def get_candidate_version_detail(
    db: AsyncSession,
    *,
    project_id: str,
    run_id: str,
    candidate_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    candidate = await db.scalar(
        select(ChapterGenerationCandidate).where(
            ChapterGenerationCandidate.project_id == project_id,
            ChapterGenerationCandidate.run_id == run_id,
            ChapterGenerationCandidate.id == candidate_id,
        )
    )
    if candidate is None:
        return None
    return await candidate_version_detail_response(db, candidate, user_id=user_id)


async def list_candidate_versions(
    db: AsyncSession,
    *,
    project_id: str,
    run_id: str,
    user_id: str,
    limit: int,
    before_version_no: int | None,
) -> dict[str, Any]:
    run_snapshot = await _list_run_snapshot(
        db, project_id=project_id, run_id=run_id, user_id=user_id
    )
    statement = select(ChapterGenerationCandidate).where(
        ChapterGenerationCandidate.project_id == project_id,
        ChapterGenerationCandidate.run_id == run_id,
    )
    if before_version_no is not None:
        statement = statement.where(
            ChapterGenerationCandidate.version_no < before_version_no
        )
    rows = list(
        (
            await db.scalars(
                statement.order_by(
                    ChapterGenerationCandidate.version_no.desc(),
                    ChapterGenerationCandidate.id.desc(),
                ).limit(limit + 1)
            )
        ).all()
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    items: list[dict[str, Any]] = []
    detail_only = set(GenerationCandidateVersionDetail.model_fields) - set(
        GenerationCandidateVersionListItem.model_fields
    )
    for candidate in page:
        detail = await candidate_version_detail_response(db, candidate, user_id=user_id)
        items.append(
            {key: value for key, value in detail.items() if key not in detail_only}
        )
    next_cursor = str(page[-1].version_no) if has_more and page else None
    snapshot = {
        "schema_version": 1,
        "project_id": project_id,
        "run_id": run_id,
        "planning_chapter_id": run_snapshot["planning_chapter_id"],
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }
    try:
        return GenerationCandidateVersionListResponse.model_validate(
            snapshot
        ).model_dump(mode="json")
    except ValidationError as exc:
        raise _corrupt() from exc


async def find_manual_edit_by_key(
    db: AsyncSession,
    *,
    project_id: str,
    run_id: str,
    user_id: str,
    operation_key: str,
) -> ChapterGenerationCandidate | None:
    candidate_id = manual_edit_candidate_id(
        user_id=user_id,
        project_id=project_id,
        operation_key=operation_key,
    )
    return await db.scalar(
        select(ChapterGenerationCandidate).where(
            ChapterGenerationCandidate.id == candidate_id,
            ChapterGenerationCandidate.project_id == project_id,
            ChapterGenerationCandidate.run_id == run_id,
            ChapterGenerationCandidate.created_by == user_id,
        )
    )


async def manual_edit_response(
    db: AsyncSession,
    candidate: ChapterGenerationCandidate,
    *,
    user_id: str,
    replayed: bool,
) -> dict[str, Any]:
    if candidate.origin_kind != "manual_edit":
        raise _corrupt()
    detail = await candidate_version_detail_response(db, candidate, user_id=user_id)
    try:
        return GenerationCandidateManualEditResponse.model_validate(
            {
                "schema_version": 1,
                "replayed": replayed,
                "ai_invoked": False,
                "billing_effect": "none",
                "usage_status": "not_applicable",
                "candidate": detail,
            }
        ).model_dump(mode="json")
    except ValidationError as exc:
        raise _corrupt() from exc


async def _validate_manual_edit_replay(
    db: AsyncSession,
    candidate: ChapterGenerationCandidate,
    *,
    project_id: str,
    run_id: str,
    user_id: str,
    operation_key: str,
    parent_candidate_id: str,
    expected_parent_version_no: int,
    expected_parent_checksum: str,
    expected_context_checksum: str,
    content: str,
) -> dict[str, Any]:
    expected_id = manual_edit_candidate_id(
        user_id=user_id,
        project_id=project_id,
        operation_key=operation_key,
    )
    lineage = await validate_candidate_lineage(db, candidate, user_id=user_id)
    if (
        candidate.id != expected_id
        or candidate.project_id != project_id
        or candidate.run_id != run_id
        or candidate.created_by != user_id
        or candidate.origin_kind != "manual_edit"
        or candidate.parent_candidate_id != parent_candidate_id
        or candidate.content != content
    ):
        raise GenerationExecutionError(
            "GENERATION_CANDIDATE_OPERATION_CONFLICT",
            "该手工另存编号已用于其他内容。",
            recommended_action="start_new_candidate_manual_edit",
        )
    parent = await db.scalar(
        select(ChapterGenerationCandidate).where(
            ChapterGenerationCandidate.project_id == project_id,
            ChapterGenerationCandidate.run_id == run_id,
            ChapterGenerationCandidate.id == candidate.parent_candidate_id,
        )
    )
    if parent is None:
        raise _corrupt()
    request_fingerprint = manual_edit_request_fingerprint(
        project_id=project_id,
        user_id=user_id,
        run_id=run_id,
        operation_key=operation_key,
        parent_candidate_id=parent_candidate_id,
        expected_parent_version_no=expected_parent_version_no,
        expected_parent_checksum=expected_parent_checksum,
        expected_context_checksum=expected_context_checksum,
        content=content,
    )
    stored_fingerprint = operation_fingerprint(
        candidate.project_id,
        _MANUAL_EDIT_OPERATION_TYPE,
        candidate.id,
        {
            "run_id": candidate.run_id,
            "parent_candidate_id": candidate.parent_candidate_id,
            "expected_parent_version_no": parent.version_no,
            "expected_parent_checksum": parent.content_checksum,
            "expected_context_checksum": lineage.run_snapshot["context_checksum"],
            "content_checksum": candidate.content_checksum,
            "content_size_bytes": candidate.content_size_bytes,
        },
    )
    if (
        parent.version_no != expected_parent_version_no
        or parent.content_checksum != expected_parent_checksum
        or lineage.parent_version_no != parent.version_no
        or lineage.run_snapshot["context_checksum"] != expected_context_checksum
        or stored_fingerprint != request_fingerprint
    ):
        raise GenerationExecutionError(
            "GENERATION_CANDIDATE_OPERATION_CONFLICT",
            "该手工另存编号已用于其他内容。",
            recommended_action="start_new_candidate_manual_edit",
        )
    return await manual_edit_response(db, candidate, user_id=user_id, replayed=True)


async def create_manual_edit_candidate(
    *,
    db: AsyncSession,
    project_id: str,
    run_id: str,
    user_id: str,
    operation_key: str,
    parent_candidate_id: str,
    expected_parent_version_no: int,
    expected_parent_checksum: str,
    expected_context_checksum: str,
    content: str,
) -> dict[str, Any]:
    try:
        content_bytes = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise GenerationExecutionError(
            "GENERATION_CANDIDATE_CONTENT_ENCODING_INVALID",
            "手工另存的候选不是有效的 UTF-8 内容。",
            status_code=422,
            recommended_action="edit_candidate_content",
        ) from exc
    words = _word_count(content)
    if not content.strip() or words < 1:
        raise GenerationExecutionError(
            "GENERATION_CANDIDATE_CONTENT_EMPTY",
            "手工另存的候选内容不能为空。",
            recommended_action="edit_candidate_content",
        )
    if len(content_bytes) > MAX_CANDIDATE_BYTES:
        raise GenerationExecutionError(
            "GENERATION_CANDIDATE_CONTENT_TOO_LARGE",
            "手工另存的候选超出安全上限。",
            recommended_action="shorten_candidate_content",
        )

    candidate_id = manual_edit_candidate_id(
        user_id=user_id,
        project_id=project_id,
        operation_key=operation_key,
    )
    existing = await db.get(ChapterGenerationCandidate, candidate_id)
    if existing is not None:
        return await _validate_manual_edit_replay(
            db,
            existing,
            project_id=project_id,
            run_id=run_id,
            user_id=user_id,
            operation_key=operation_key,
            parent_candidate_id=parent_candidate_id,
            expected_parent_version_no=expected_parent_version_no,
            expected_parent_checksum=expected_parent_checksum,
            expected_context_checksum=expected_context_checksum,
            content=content,
        )

    ensure_project_writes_available()
    try:
        project = await db.scalar(
            select(Project)
            .where(Project.id == project_id)
            .with_for_update(read=True, key_share=True)
        )
        if project is None:
            raise GenerationExecutionError(
                "GENERATION_PROJECT_NOT_FOUND",
                "项目不存在。",
                status_code=404,
                recommended_action="return_to_projects",
            )
        if project.owner_id != user_id:
            raise GenerationExecutionError(
                "GENERATION_PROJECT_FORBIDDEN",
                "无权操作此项目。",
                status_code=403,
                recommended_action="return_to_projects",
            )
        run = await db.scalar(
            select(ChapterGenerationRun)
            .where(
                ChapterGenerationRun.project_id == project_id,
                ChapterGenerationRun.id == run_id,
                ChapterGenerationRun.requested_by == user_id,
            )
            .with_for_update()
        )
        if run is None:
            raise GenerationExecutionError(
                "GENERATION_RUN_NOT_FOUND",
                "未找到手工另存所属的生成准备。",
                status_code=404,
                recommended_action="reload_generation_candidate_versions",
            )

        existing = await db.get(ChapterGenerationCandidate, candidate_id)
        if existing is not None:
            return await _validate_manual_edit_replay(
                db,
                existing,
                project_id=project_id,
                run_id=run_id,
                user_id=user_id,
                operation_key=operation_key,
                parent_candidate_id=parent_candidate_id,
                expected_parent_version_no=expected_parent_version_no,
                expected_parent_checksum=expected_parent_checksum,
                expected_context_checksum=expected_context_checksum,
                content=content,
            )
        try:
            run_snapshot = generation_run_response(run, replayed=True)
        except GenerationPreparationError as exc:
            raise _corrupt() from exc
        if run_snapshot["context_checksum"] != expected_context_checksum:
            raise GenerationExecutionError(
                "GENERATION_CONTEXT_CHECKSUM_CONFLICT",
                "章节候选的上下文与本次另存确认不一致。",
                recommended_action="reload_generation_candidate_versions",
            )
        parent = await db.scalar(
            select(ChapterGenerationCandidate).where(
                ChapterGenerationCandidate.project_id == project_id,
                ChapterGenerationCandidate.run_id == run_id,
                ChapterGenerationCandidate.id == parent_candidate_id,
            )
        )
        if parent is None:
            raise GenerationExecutionError(
                "GENERATION_CANDIDATE_PARENT_NOT_FOUND",
                "未找到要编辑的父候选。",
                status_code=404,
                recommended_action="reload_generation_candidate_versions",
            )
        await validate_candidate_lineage(db, parent, user_id=user_id)
        if (
            parent.version_no != expected_parent_version_no
            or parent.content_checksum != expected_parent_checksum
        ):
            raise GenerationExecutionError(
                "GENERATION_CANDIDATE_PARENT_CHANGED",
                "要编辑的父候选与确认时不一致。",
                recommended_action="reload_generation_candidate_versions",
            )
        if parent.content == content:
            raise GenerationExecutionError(
                "GENERATION_CANDIDATE_CONTENT_UNCHANGED",
                "候选内容未修改，无需另存新版本。",
                recommended_action="edit_candidate_content",
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
            id=candidate_id,
            project_id=project_id,
            run_id=run_id,
            source_attempt_id=None,
            source_technical_demo_execution_id=None,
            parent_candidate_id=parent.id,
            version_no=next_version,
            origin_kind="manual_edit",
            title=run_snapshot["context_manifest"]["chapter"]["title"],
            content=content,
            content_format="plain_text",
            content_checksum=hashlib.sha256(content_bytes).hexdigest(),
            content_size_bytes=len(content_bytes),
            word_count=words,
            created_by=user_id,
        )
        ensure_project_writes_available()
        db.add(candidate)
        await db.flush()
        response = await manual_edit_response(
            db, candidate, user_id=user_id, replayed=False
        )
        await db.commit()
        return response
    except IntegrityError as exc:
        await db.rollback()
        existing = await db.get(ChapterGenerationCandidate, candidate_id)
        if existing is None:
            raise GenerationExecutionError(
                "GENERATION_CANDIDATE_VERSION_CONFLICT",
                "候选版本并发号冲突，未保存本次内容。",
                retryable=True,
                recommended_action="retry_candidate_manual_edit",
            ) from exc
        return await _validate_manual_edit_replay(
            db,
            existing,
            project_id=project_id,
            run_id=run_id,
            user_id=user_id,
            operation_key=operation_key,
            parent_candidate_id=parent_candidate_id,
            expected_parent_version_no=expected_parent_version_no,
            expected_parent_checksum=expected_parent_checksum,
            expected_context_checksum=expected_context_checksum,
            content=content,
        )
    except Exception:
        await db.rollback()
        raise
