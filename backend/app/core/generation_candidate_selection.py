"""Strict chapter-level reads for the author-selected candidate pointer."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.generation_candidates import (
    candidate_version_detail_response,
    validate_candidate_lineage,
)
from app.core.generation_execution import GenerationExecutionError
from app.core.maintenance import ensure_project_writes_available
from app.core.planning_write import operation_fingerprint
from app.models.generation import (
    ChapterGenerationCandidate,
    ChapterGenerationCandidateSelection,
    ChapterGenerationCandidateSelectionOperation,
)
from app.models.planning import PlanningChapter
from app.models.project import Project, _utcnow
from app.schemas.generation import (
    GenerationCandidateSelectionOperationResponse,
    GenerationCandidateSelectionCurrentResponse,
    GenerationCandidateVersionDetail,
    GenerationCandidateVersionListItem,
)


_SELECTION_OPERATION_TYPE = "generation_candidate_selection_v1"


def candidate_selection_id(*, project_id: str, planning_chapter_id: str) -> str:
    return hashlib.sha256(
        f"{project_id}:{planning_chapter_id}:candidate-selection".encode("utf-8")
    ).hexdigest()[:32]


def candidate_selection_operation_id(
    *, user_id: str, project_id: str, operation_key: str
) -> str:
    return hashlib.sha256(
        f"{user_id}:{project_id}:{operation_key}".encode("utf-8")
    ).hexdigest()[:32]


def candidate_selection_request_fingerprint(
    *,
    project_id: str,
    user_id: str,
    planning_chapter_id: str,
    operation_key: str,
    expected_selection_version: int,
    target_run_id: str,
    target_candidate_id: str,
    expected_candidate_version_no: int,
    expected_candidate_checksum: str,
    expected_context_checksum: str,
) -> str:
    return operation_fingerprint(
        project_id,
        _SELECTION_OPERATION_TYPE,
        candidate_selection_operation_id(
            user_id=user_id,
            project_id=project_id,
            operation_key=operation_key,
        ),
        {
            "planning_chapter_id": planning_chapter_id,
            "expected_selection_version": expected_selection_version,
            "target_run_id": target_run_id,
            "target_candidate_id": target_candidate_id,
            "expected_candidate_version_no": expected_candidate_version_no,
            "expected_candidate_checksum": expected_candidate_checksum,
            "expected_context_checksum": expected_context_checksum,
        },
    )


def _selection_corrupt() -> GenerationExecutionError:
    return GenerationExecutionError(
        "GENERATION_CANDIDATE_SELECTION_CORRUPT",
        "章节采用版本记录不完整，已停止展示采用状态。",
        status_code=409,
        recommended_action="reload_candidate_selection",
    )


def _selection_target_corrupt() -> GenerationExecutionError:
    return GenerationExecutionError(
        "GENERATION_CANDIDATE_SELECTION_TARGET_CORRUPT",
        "要采用的候选版本记录不完整，已停止本次采用。",
        retryable=False,
        recommended_action="reload_generation_candidate_versions",
    )


def _candidate_summary(detail: dict[str, Any]) -> dict[str, Any]:
    detail_only = set(GenerationCandidateVersionDetail.model_fields) - set(
        GenerationCandidateVersionListItem.model_fields
    )
    return {key: value for key, value in detail.items() if key not in detail_only}


async def candidate_selection_current_response(
    db: AsyncSession,
    *,
    project_id: str,
    planning_chapter_id: str,
    user_id: str,
) -> dict[str, Any]:
    chapter = await db.scalar(
        select(PlanningChapter).where(
            PlanningChapter.project_id == project_id,
            PlanningChapter.id == planning_chapter_id,
        )
    )
    if chapter is None:
        raise GenerationExecutionError(
            "GENERATION_PLANNING_CHAPTER_NOT_FOUND",
            "未找到该章节规划。",
            status_code=404,
            recommended_action="return_to_chapter_planning",
        )

    selection = await db.scalar(
        select(ChapterGenerationCandidateSelection).where(
            ChapterGenerationCandidateSelection.project_id == project_id,
            ChapterGenerationCandidateSelection.planning_chapter_id
            == planning_chapter_id,
        )
    )
    if selection is None:
        snapshot = {
            "schema_version": 1,
            "project_id": project_id,
            "planning_chapter_id": planning_chapter_id,
            "state": "none",
            "selection_version": 0,
            "run_id": None,
            "context_checksum": None,
            "candidate": None,
            "selected_at": None,
            "changed_by": None,
        }
    else:
        expected_selection_id = candidate_selection_id(
            project_id=project_id,
            planning_chapter_id=planning_chapter_id,
        )
        operation = await db.scalar(
            select(ChapterGenerationCandidateSelectionOperation).where(
                ChapterGenerationCandidateSelectionOperation.project_id == project_id,
                ChapterGenerationCandidateSelectionOperation.id
                == selection.last_operation_id,
            )
        )
        if operation is None:
            raise _selection_corrupt()
        try:
            receipt = await _selection_operation_response(
                db, operation, user_id=user_id, replayed=True
            )
        except GenerationExecutionError as exc:
            raise _selection_corrupt() from exc
        result = receipt["result"]
        result_candidate = result["candidate"]
        if (
            selection.id != expected_selection_id
            or selection.project_id != project_id
            or selection.planning_chapter_id != planning_chapter_id
            or selection.changed_by != user_id
            or selection.selected_at != operation.created_at
            or selection.updated_at != selection.selected_at
            or selection.created_at > selection.updated_at
            or operation.planning_chapter_id != planning_chapter_id
            or operation.result_selection_version != selection.selection_version
            or operation.result_run_id != selection.run_id
            or operation.result_candidate_id != selection.candidate_id
            or operation.result_candidate_version_no != selection.candidate_version_no
            or operation.result_candidate_checksum != selection.candidate_checksum
            or operation.result_context_checksum != selection.context_checksum
            or result["selection_version"] != selection.selection_version
            or result["run_id"] != selection.run_id
            or result["context_checksum"] != selection.context_checksum
            or result_candidate["id"] != selection.candidate_id
            or result_candidate["version_no"] != selection.candidate_version_no
            or result_candidate["content_checksum"] != selection.candidate_checksum
        ):
            raise _selection_corrupt()
        snapshot = {
            "schema_version": 1,
            "project_id": project_id,
            "planning_chapter_id": planning_chapter_id,
            "state": "selected",
            "selection_version": selection.selection_version,
            "run_id": selection.run_id,
            "context_checksum": selection.context_checksum,
            "candidate": result_candidate,
            "selected_at": selection.selected_at,
            "changed_by": selection.changed_by,
        }

    try:
        return GenerationCandidateSelectionCurrentResponse.model_validate(
            snapshot
        ).model_dump(mode="json")
    except ValidationError as exc:
        raise _selection_corrupt() from exc


async def _selection_operation_response(
    db: AsyncSession,
    operation: ChapterGenerationCandidateSelectionOperation,
    *,
    user_id: str,
    replayed: bool,
) -> dict[str, Any]:
    expected_id = candidate_selection_operation_id(
        user_id=user_id,
        project_id=operation.project_id,
        operation_key=operation.operation_key,
    )
    stored_fingerprint = candidate_selection_request_fingerprint(
        project_id=operation.project_id,
        user_id=user_id,
        planning_chapter_id=operation.planning_chapter_id,
        operation_key=operation.operation_key,
        expected_selection_version=operation.previous_selection_version,
        target_run_id=operation.result_run_id,
        target_candidate_id=operation.result_candidate_id,
        expected_candidate_version_no=operation.result_candidate_version_no,
        expected_candidate_checksum=operation.result_candidate_checksum,
        expected_context_checksum=operation.result_context_checksum,
    )
    result_candidate = await db.scalar(
        select(ChapterGenerationCandidate).where(
            ChapterGenerationCandidate.project_id == operation.project_id,
            ChapterGenerationCandidate.run_id == operation.result_run_id,
            ChapterGenerationCandidate.id == operation.result_candidate_id,
        )
    )
    if result_candidate is None:
        raise _selection_corrupt()
    try:
        result_lineage = await validate_candidate_lineage(
            db, result_candidate, user_id=user_id
        )
        result_detail = await candidate_version_detail_response(
            db, result_candidate, user_id=user_id
        )
    except GenerationExecutionError as exc:
        raise _selection_corrupt() from exc
    if operation.previous_selection_version == 0:
        if any(
            value is not None
            for value in (
                operation.previous_run_id,
                operation.previous_candidate_id,
                operation.previous_candidate_version_no,
                operation.previous_candidate_checksum,
                operation.previous_context_checksum,
            )
        ):
            raise _selection_corrupt()
        previous_snapshot = {
            "state": "none",
            "selection_version": 0,
            "run_id": None,
            "context_checksum": None,
            "candidate": None,
        }
    else:
        previous_candidate = await db.scalar(
            select(ChapterGenerationCandidate).where(
                ChapterGenerationCandidate.project_id == operation.project_id,
                ChapterGenerationCandidate.run_id == operation.previous_run_id,
                ChapterGenerationCandidate.id == operation.previous_candidate_id,
            )
        )
        if previous_candidate is None:
            raise _selection_corrupt()
        try:
            previous_lineage = await validate_candidate_lineage(
                db, previous_candidate, user_id=user_id
            )
            previous_detail = await candidate_version_detail_response(
                db, previous_candidate, user_id=user_id
            )
        except GenerationExecutionError as exc:
            raise _selection_corrupt() from exc
        if (
            previous_detail["planning_chapter_id"] != operation.planning_chapter_id
            or previous_detail["run_id"] != operation.previous_run_id
            or previous_detail["version_no"] != operation.previous_candidate_version_no
            or previous_detail["content_checksum"]
            != operation.previous_candidate_checksum
            or previous_lineage.run_snapshot["context_checksum"]
            != operation.previous_context_checksum
        ):
            raise _selection_corrupt()
        previous_snapshot = {
            "state": "selected",
            "selection_version": operation.previous_selection_version,
            "run_id": operation.previous_run_id,
            "context_checksum": operation.previous_context_checksum,
            "candidate": _candidate_summary(previous_detail),
        }

    if (
        operation.id != expected_id
        or operation.requested_by != user_id
        or operation.request_fingerprint != stored_fingerprint
        or operation.result_selection_version
        != operation.previous_selection_version + 1
        or result_detail["planning_chapter_id"] != operation.planning_chapter_id
        or result_detail["run_id"] != operation.result_run_id
        or result_detail["version_no"] != operation.result_candidate_version_no
        or result_detail["content_checksum"] != operation.result_candidate_checksum
        or result_lineage.run_snapshot["context_checksum"]
        != operation.result_context_checksum
    ):
        raise _selection_corrupt()
    snapshot = {
        "schema_version": 1,
        "project_id": operation.project_id,
        "planning_chapter_id": operation.planning_chapter_id,
        "operation_key": operation.operation_key,
        "replayed": replayed,
        "changed": True,
        "ai_invoked": False,
        "billing_effect": "none",
        "usage_status": "not_applicable",
        "previous": previous_snapshot,
        "result": {
            "state": "selected",
            "selection_version": operation.result_selection_version,
            "run_id": operation.result_run_id,
            "context_checksum": operation.result_context_checksum,
            "candidate": _candidate_summary(result_detail),
        },
        "selected_at": operation.created_at,
        "changed_by": operation.requested_by,
    }
    try:
        return GenerationCandidateSelectionOperationResponse.model_validate(
            snapshot
        ).model_dump(mode="json")
    except ValidationError as exc:
        raise _selection_corrupt() from exc


async def find_candidate_selection_operation_by_key(
    db: AsyncSession,
    *,
    project_id: str,
    planning_chapter_id: str,
    user_id: str,
    operation_key: str,
) -> ChapterGenerationCandidateSelectionOperation | None:
    operation_id = candidate_selection_operation_id(
        user_id=user_id,
        project_id=project_id,
        operation_key=operation_key,
    )
    operation = await db.get(ChapterGenerationCandidateSelectionOperation, operation_id)
    if operation is None:
        return None
    if (
        operation.project_id != project_id
        or operation.requested_by != user_id
        or operation.operation_key != operation_key
    ):
        raise _selection_corrupt()
    if operation.planning_chapter_id != planning_chapter_id:
        return None
    return operation


async def candidate_selection_operation_response(
    db: AsyncSession,
    operation: ChapterGenerationCandidateSelectionOperation,
    *,
    user_id: str,
    replayed: bool,
) -> dict[str, Any]:
    return await _selection_operation_response(
        db, operation, user_id=user_id, replayed=replayed
    )


async def _validate_operation_replay(
    db: AsyncSession,
    operation: ChapterGenerationCandidateSelectionOperation,
    *,
    project_id: str,
    planning_chapter_id: str,
    user_id: str,
    operation_key: str,
    expected_selection_version: int,
    target_run_id: str,
    target_candidate_id: str,
    expected_candidate_version_no: int,
    expected_candidate_checksum: str,
    expected_context_checksum: str,
) -> dict[str, Any]:
    request_fingerprint = candidate_selection_request_fingerprint(
        project_id=project_id,
        user_id=user_id,
        planning_chapter_id=planning_chapter_id,
        operation_key=operation_key,
        expected_selection_version=expected_selection_version,
        target_run_id=target_run_id,
        target_candidate_id=target_candidate_id,
        expected_candidate_version_no=expected_candidate_version_no,
        expected_candidate_checksum=expected_candidate_checksum,
        expected_context_checksum=expected_context_checksum,
    )
    if (
        operation.project_id != project_id
        or operation.planning_chapter_id != planning_chapter_id
        or operation.requested_by != user_id
        or operation.operation_key != operation_key
        or operation.request_fingerprint != request_fingerprint
        or operation.previous_selection_version != expected_selection_version
        or operation.result_run_id != target_run_id
        or operation.result_candidate_id != target_candidate_id
        or operation.result_candidate_version_no != expected_candidate_version_no
        or operation.result_candidate_checksum != expected_candidate_checksum
        or operation.result_context_checksum != expected_context_checksum
    ):
        raise GenerationExecutionError(
            "GENERATION_CANDIDATE_SELECTION_OPERATION_CONFLICT",
            "该采用操作编号已用于其他请求。",
            recommended_action="start_new_candidate_selection",
        )
    return await _selection_operation_response(
        db, operation, user_id=user_id, replayed=True
    )


async def select_generation_candidate(
    *,
    db: AsyncSession,
    project_id: str,
    planning_chapter_id: str,
    user_id: str,
    operation_key: str,
    expected_selection_version: int,
    target_run_id: str,
    target_candidate_id: str,
    expected_candidate_version_no: int,
    expected_candidate_checksum: str,
    expected_context_checksum: str,
) -> dict[str, Any]:
    operation_id = candidate_selection_operation_id(
        user_id=user_id,
        project_id=project_id,
        operation_key=operation_key,
    )
    existing = await db.get(ChapterGenerationCandidateSelectionOperation, operation_id)
    if existing is not None:
        return await _validate_operation_replay(
            db,
            existing,
            project_id=project_id,
            planning_chapter_id=planning_chapter_id,
            user_id=user_id,
            operation_key=operation_key,
            expected_selection_version=expected_selection_version,
            target_run_id=target_run_id,
            target_candidate_id=target_candidate_id,
            expected_candidate_version_no=expected_candidate_version_no,
            expected_candidate_checksum=expected_candidate_checksum,
            expected_context_checksum=expected_context_checksum,
        )

    ensure_project_writes_available()
    try:
        project = await db.scalar(
            select(Project)
            .where(Project.id == project_id, Project.owner_id == user_id)
            .with_for_update(read=True, key_share=True)
        )
        if project is None:
            raise GenerationExecutionError(
                "GENERATION_PROJECT_NOT_FOUND",
                "项目不存在。",
                status_code=404,
                recommended_action="return_to_projects",
            )
        chapter = await db.scalar(
            select(PlanningChapter)
            .where(
                PlanningChapter.project_id == project_id,
                PlanningChapter.id == planning_chapter_id,
            )
            .with_for_update()
        )
        if chapter is None:
            raise GenerationExecutionError(
                "GENERATION_PLANNING_CHAPTER_NOT_FOUND",
                "未找到该章节规划。",
                status_code=404,
                recommended_action="return_to_chapter_planning",
            )
        if chapter.status != "active":
            raise GenerationExecutionError(
                "GENERATION_PLANNING_CHAPTER_ARCHIVED",
                "归档章节不能修改采用版本，请先恢复章节。",
                recommended_action="restore_planning_chapter",
            )

        selection = await db.scalar(
            select(ChapterGenerationCandidateSelection)
            .where(
                ChapterGenerationCandidateSelection.project_id == project_id,
                ChapterGenerationCandidateSelection.planning_chapter_id
                == planning_chapter_id,
            )
            .with_for_update()
        )
        existing = await db.get(
            ChapterGenerationCandidateSelectionOperation, operation_id
        )
        if existing is not None:
            return await _validate_operation_replay(
                db,
                existing,
                project_id=project_id,
                planning_chapter_id=planning_chapter_id,
                user_id=user_id,
                operation_key=operation_key,
                expected_selection_version=expected_selection_version,
                target_run_id=target_run_id,
                target_candidate_id=target_candidate_id,
                expected_candidate_version_no=expected_candidate_version_no,
                expected_candidate_checksum=expected_candidate_checksum,
                expected_context_checksum=expected_context_checksum,
            )

        if selection is None:
            current_version = 0
        else:
            await candidate_selection_current_response(
                db,
                project_id=project_id,
                planning_chapter_id=planning_chapter_id,
                user_id=user_id,
            )
            current_version = selection.selection_version
        if current_version != expected_selection_version:
            raise GenerationExecutionError(
                "GENERATION_CANDIDATE_SELECTION_VERSION_CONFLICT",
                "章节采用版本已变化，请重新读取后再确认。",
                retryable=False,
                recommended_action="reload_candidate_selection",
            )

        candidate = await db.scalar(
            select(ChapterGenerationCandidate).where(
                ChapterGenerationCandidate.project_id == project_id,
                ChapterGenerationCandidate.run_id == target_run_id,
                ChapterGenerationCandidate.id == target_candidate_id,
            )
        )
        if candidate is None:
            raise GenerationExecutionError(
                "GENERATION_CANDIDATE_VERSION_NOT_FOUND",
                "未找到要采用的章节候选版本。",
                status_code=404,
                recommended_action="reload_generation_candidate_versions",
            )
        try:
            lineage = await validate_candidate_lineage(db, candidate, user_id=user_id)
            detail = await candidate_version_detail_response(
                db, candidate, user_id=user_id
            )
        except GenerationExecutionError as exc:
            raise _selection_target_corrupt() from exc
        if (
            detail["planning_chapter_id"] != planning_chapter_id
            or detail["run_id"] != target_run_id
            or detail["version_no"] != expected_candidate_version_no
            or detail["content_checksum"] != expected_candidate_checksum
            or lineage.run_snapshot["context_checksum"] != expected_context_checksum
        ):
            raise GenerationExecutionError(
                "GENERATION_CANDIDATE_SELECTION_TARGET_CHANGED",
                "要采用的候选版本与确认时不一致。",
                recommended_action="reload_generation_candidate_versions",
            )
        if selection is not None and selection.candidate_id == target_candidate_id:
            raise GenerationExecutionError(
                "GENERATION_CANDIDATE_ALREADY_SELECTED",
                "该候选已经是章节采用版本。",
                recommended_action="reload_candidate_selection",
            )

        request_fingerprint = candidate_selection_request_fingerprint(
            project_id=project_id,
            user_id=user_id,
            planning_chapter_id=planning_chapter_id,
            operation_key=operation_key,
            expected_selection_version=expected_selection_version,
            target_run_id=target_run_id,
            target_candidate_id=target_candidate_id,
            expected_candidate_version_no=expected_candidate_version_no,
            expected_candidate_checksum=expected_candidate_checksum,
            expected_context_checksum=expected_context_checksum,
        )
        selected_at = _utcnow()
        operation = ChapterGenerationCandidateSelectionOperation(
            id=operation_id,
            project_id=project_id,
            planning_chapter_id=planning_chapter_id,
            requested_by=user_id,
            operation_key=operation_key,
            request_fingerprint=request_fingerprint,
            previous_selection_version=current_version,
            previous_run_id=selection.run_id if selection is not None else None,
            previous_candidate_id=(
                selection.candidate_id if selection is not None else None
            ),
            previous_candidate_version_no=(
                selection.candidate_version_no if selection is not None else None
            ),
            previous_candidate_checksum=(
                selection.candidate_checksum if selection is not None else None
            ),
            previous_context_checksum=(
                selection.context_checksum if selection is not None else None
            ),
            result_selection_version=current_version + 1,
            result_run_id=target_run_id,
            result_candidate_id=target_candidate_id,
            result_candidate_version_no=expected_candidate_version_no,
            result_candidate_checksum=expected_candidate_checksum,
            result_context_checksum=expected_context_checksum,
            created_at=selected_at,
        )
        ensure_project_writes_available()
        db.add(operation)
        await db.flush()
        if selection is None:
            selection = ChapterGenerationCandidateSelection(
                id=candidate_selection_id(
                    project_id=project_id,
                    planning_chapter_id=planning_chapter_id,
                ),
                project_id=project_id,
                planning_chapter_id=planning_chapter_id,
                run_id=target_run_id,
                candidate_id=target_candidate_id,
                candidate_version_no=expected_candidate_version_no,
                candidate_checksum=expected_candidate_checksum,
                context_checksum=expected_context_checksum,
                selection_version=1,
                changed_by=user_id,
                last_operation_id=operation_id,
                selected_at=selected_at,
                created_at=selected_at,
                updated_at=selected_at,
            )
            db.add(selection)
        else:
            selection.run_id = target_run_id
            selection.candidate_id = target_candidate_id
            selection.candidate_version_no = expected_candidate_version_no
            selection.candidate_checksum = expected_candidate_checksum
            selection.context_checksum = expected_context_checksum
            selection.selection_version = current_version + 1
            selection.changed_by = user_id
            selection.last_operation_id = operation_id
            selection.selected_at = selected_at
            selection.updated_at = selected_at
        await db.flush()
        ensure_project_writes_available()
        response = await _selection_operation_response(
            db, operation, user_id=user_id, replayed=False
        )
        await db.commit()
        return response
    except IntegrityError as exc:
        await db.rollback()
        existing = await db.get(
            ChapterGenerationCandidateSelectionOperation, operation_id
        )
        if existing is not None:
            return await _validate_operation_replay(
                db,
                existing,
                project_id=project_id,
                planning_chapter_id=planning_chapter_id,
                user_id=user_id,
                operation_key=operation_key,
                expected_selection_version=expected_selection_version,
                target_run_id=target_run_id,
                target_candidate_id=target_candidate_id,
                expected_candidate_version_no=expected_candidate_version_no,
                expected_candidate_checksum=expected_candidate_checksum,
                expected_context_checksum=expected_context_checksum,
            )
        raise GenerationExecutionError(
            "GENERATION_CANDIDATE_SELECTION_VERSION_CONFLICT",
            "章节采用版本已变化，请重新读取后再确认。",
            retryable=False,
            recommended_action="reload_candidate_selection",
        ) from exc
    except Exception:
        await db.rollback()
        raise
