"""Persistent, idempotent lore extraction API."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import User, get_current_user, get_project_for_owner
from app.core.llm_client import LLMSingleCallError, llm_client
from app.core.lore_extraction import (
    EXTRACTOR_VERSION,
    ExtractionValidationError,
    build_extraction_messages,
    candidate_needs_attention,
    field_display_label,
    prepare_candidates,
    source_hash,
    type_display_name,
)
from app.core.lore_migration import BUILTIN_TYPE_KEYS, TYPE_FIELD_SCHEMAS
from app.core.lore_write import LoreWriteError, create_element
from app.core.maintenance import (
    ProjectWriteFrozenError,
    ensure_project_writes_available,
)
from app.api.lore import _decode_cursor, _encode_cursor
from app.database import get_db
from app.models.extraction import (
    LoreCandidateFieldEvidence,
    LoreCandidateRevision,
    LoreExtractionBatch,
    LoreExtractionCandidate,
)
from app.models.project import _utcnow
from app.schemas.extraction import (
    MAX_EXTRACTION_SOURCE_CHARS,
    LoreCandidateEvidenceResponse,
    LoreCandidateInboxResponse,
    LoreCandidateActionInput,
    LoreCandidateActionResponse,
    LoreCandidateActions,
    LoreCandidateEdit,
    LoreCandidateRevisionResponse,
    LoreCandidateRevisionsResponse,
    LoreExtractionBatchResponse,
    LoreExtractionCandidateResponse,
    LoreExtractionCandidatesResponse,
    LoreExtractionCreate,
)


router = APIRouter(
    prefix="/api/projects/{project_id}/lore/extractions",
    tags=["lore-extraction"],
)


def _candidate_inbox_signature(filters: dict[str, object]) -> str:
    payload = json.dumps(
        filters,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


async def _load_batch(
    db: AsyncSession,
    project_id: str,
    batch_id: str,
) -> LoreExtractionBatch:
    batch = await db.scalar(
        select(LoreExtractionBatch).where(
            LoreExtractionBatch.id == batch_id,
            LoreExtractionBatch.project_id == project_id,
        )
    )
    if batch is None:
        raise HTTPException(status_code=404, detail="提取批次不存在")
    return batch


async def _batch_response(
    db: AsyncSession,
    batch: LoreExtractionBatch,
) -> LoreExtractionBatchResponse:
    statuses = await db.execute(
        select(LoreExtractionCandidate.status).where(
            LoreExtractionCandidate.batch_id == batch.id
        )
    )
    counts = Counter(statuses.scalars().all())
    return LoreExtractionBatchResponse(
        id=batch.id,
        project_id=batch.project_id,
        status=batch.status,
        source_kind=batch.source_kind,
        source_ref=batch.source_ref,
        source_hash=batch.source_hash,
        extractor_version=batch.extractor_version,
        model_name=batch.model_name,
        candidate_count=batch.candidate_count,
        pending_review_count=counts["pending_review"],
        accepted_count=counts["accepted"],
        rejected_count=counts["rejected"],
        failed_count=counts["failed"],
        retryable=batch.retryable,
        error_code=batch.error_code,
        error_message=batch.error_message,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


async def _candidate_responses(
    db: AsyncSession,
    candidates: list[LoreExtractionCandidate],
    *,
    project_mode: str | None = None,
) -> list[LoreExtractionCandidateResponse]:
    candidate_ids = [candidate.id for candidate in candidates]
    evidence_by_candidate: dict[str, list[LoreCandidateFieldEvidence]] = {
        candidate_id: [] for candidate_id in candidate_ids
    }
    if candidate_ids:
        rows = await db.execute(
            select(LoreCandidateFieldEvidence)
            .where(LoreCandidateFieldEvidence.candidate_id.in_(candidate_ids))
            .order_by(
                LoreCandidateFieldEvidence.candidate_id,
                LoreCandidateFieldEvidence.is_name.desc(),
                LoreCandidateFieldEvidence.field_key,
            )
        )
        for evidence in rows.scalars().all():
            evidence_by_candidate[evidence.candidate_id].append(evidence)

    responses: list[LoreExtractionCandidateResponse] = []
    for candidate in candidates:
        overrides = candidate.user_overrides or {}
        resolutions = candidate.suggestion_resolutions or {}
        disabled_reasons: list[str] = []
        if not candidate.name:
            disabled_reasons.append("name_missing")
        if not candidate.type_key:
            disabled_reasons.append("type_missing")
        elif candidate.type_key not in BUILTIN_TYPE_KEYS:
            disabled_reasons.append("type_invalid")
        if "needs_confirmation" in (candidate.field_states or {}).values():
            disabled_reasons.append("fields_need_confirmation")
        if candidate.status != "pending_review":
            disabled_reasons.append("candidate_not_pending")
        suggestion_ids = {
            item.get("suggestion_id")
            for item in (candidate.duplicate_conflict_suggestions or [])
            if item.get("suggestion_id")
        }
        unresolved = sorted(
            suggestion_id
            for suggestion_id in suggestion_ids
            if resolutions.get(suggestion_id) not in ("accept_as_new", "dismissed")
        )
        accept_reasons = list(disabled_reasons)
        if unresolved:
            accept_reasons.append("suggestions_unresolved")
        if project_mode is not None and project_mode != "relational":
            accept_reasons.append("lore_mode_not_relational")
        edit_reasons = (
            [] if candidate.status == "pending_review" else ["candidate_not_pending"]
        )
        reject_reasons = list(edit_reasons)
        responses.append(
            LoreExtractionCandidateResponse(
                id=candidate.id,
                batch_id=candidate.batch_id,
                ordinal=candidate.ordinal,
                type_key=candidate.type_key,
                type_display_name=type_display_name(candidate.type_key),
                name=candidate.name,
                summary=candidate.summary or "",
                payload=candidate.payload or {},
                field_states=candidate.field_states or {},
                relation_suggestions=candidate.relation_suggestions or [],
                duplicate_conflict_suggestions=(
                    candidate.duplicate_conflict_suggestions or []
                ),
                suggestion_resolutions=resolutions,
                user_overrides=overrides,
                status=candidate.status,
                revision=candidate.revision,
                accepted_element_id=candidate.accepted_element_id,
                error_code=candidate.error_code,
                needs_attention=candidate.needs_attention,
                evidence=[
                    LoreCandidateEvidenceResponse(
                        id=item.id,
                        field_key=item.field_key,
                        label=field_display_label(
                            candidate.type_key,
                            item.field_key,
                        ),
                        value=item.value,
                        extracted_value=item.value,
                        current_value=(
                            candidate.name
                            if item.is_name
                            else (candidate.payload or {}).get(item.field_key)
                        ),
                        current_state=(
                            "provided"
                            if item.is_name and candidate.name
                            else (
                                "unknown"
                                if item.is_name
                                else (candidate.field_states or {}).get(
                                    item.field_key,
                                    "unknown",
                                )
                            )
                        ),
                        value_origin=(
                            overrides[item.field_key]["origin"]
                            if item.field_key in overrides
                            else "ai_extraction"
                        ),
                        state=item.state,
                        excerpt=item.excerpt,
                        locator=(
                            {
                                "char_start": item.char_start,
                                "char_end": item.char_end,
                                "complete": True,
                            }
                            if item.char_start is not None
                            else {"complete": False}
                        ),
                        excerpt_hash=item.excerpt_hash,
                        source_hash=item.source_hash,
                        is_name=item.is_name,
                    )
                    for item in evidence_by_candidate[candidate.id]
                ],
                can_accept=not accept_reasons,
                disabled_reasons=accept_reasons,
                actions=LoreCandidateActions(
                    can_edit=not edit_reasons,
                    can_accept=not accept_reasons,
                    can_reject=not reject_reasons,
                    can_open_element=bool(candidate.accepted_element_id),
                    disabled_reasons={
                        "edit": edit_reasons,
                        "accept": accept_reasons,
                        "reject": reject_reasons,
                    },
                ),
                created_at=candidate.created_at,
                updated_at=candidate.updated_at,
            )
        )
    return responses


async def _mark_batch_failed(
    db: AsyncSession,
    batch: LoreExtractionBatch,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    outcome_unknown: bool = False,
) -> None:
    batch.status = "outcome_unknown" if outcome_unknown else "failed"
    batch.error_code = code
    batch.error_message = message
    batch.retryable = retryable and not outcome_unknown
    batch.llm_completed_at = _utcnow()
    batch.lock_version += 1
    await db.commit()


async def _load_candidate(
    db: AsyncSession,
    project_id: str,
    batch_id: str,
    candidate_id: str,
) -> LoreExtractionCandidate:
    candidate = await db.scalar(
        select(LoreExtractionCandidate).where(
            LoreExtractionCandidate.id == candidate_id,
            LoreExtractionCandidate.project_id == project_id,
            LoreExtractionCandidate.batch_id == batch_id,
        )
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="提取候选不存在")
    return candidate


def _terminal_conflict(candidate: LoreExtractionCandidate, action: str) -> HTTPException:
    code = (
        "LORE_CANDIDATE_ALREADY_ACCEPTED"
        if candidate.status == "accepted"
        else "LORE_CANDIDATE_ALREADY_REJECTED"
    )
    return HTTPException(
        status_code=409,
        detail={
            "code": code,
            "message": f"候选已{'接受' if candidate.status == 'accepted' else '拒绝'}，无法{action}",
            "candidate_id": candidate.id,
            "latest_revision": candidate.revision,
            "current_status": candidate.status,
        },
    )


async def _claim_candidate_revision(
    db: AsyncSession,
    candidate: LoreExtractionCandidate,
    expected_version: int,
) -> LoreExtractionCandidate:
    result = await db.execute(
        update(LoreExtractionCandidate)
        .where(
            LoreExtractionCandidate.id == candidate.id,
            LoreExtractionCandidate.project_id == candidate.project_id,
            LoreExtractionCandidate.status == "pending_review",
            LoreExtractionCandidate.revision == expected_version,
        )
        .values(revision=expected_version + 1, updated_at=_utcnow())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 1:
        await db.refresh(candidate)
        return candidate
    latest = await db.scalar(
        select(LoreExtractionCandidate)
        .where(LoreExtractionCandidate.id == candidate.id)
        .execution_options(populate_existing=True)
    )
    if latest is None:
        raise HTTPException(status_code=404, detail="提取候选不存在")
    raise HTTPException(
        status_code=409,
        detail={
            "code": "LORE_CANDIDATE_VERSION_CONFLICT",
            "message": "候选已被更新，请查看最新内容",
            "candidate_id": latest.id,
            "latest_revision": latest.revision,
            "current_status": latest.status,
            "updated_at": latest.updated_at.isoformat(),
            "changed_fields": [],
            "reload_required": True,
        },
    )


def _validate_candidate_content(
    body: LoreCandidateEdit,
) -> tuple[dict[str, str | None], dict[str, str]]:
    if body.type_key not in BUILTIN_TYPE_KEYS:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "LORE_CANDIDATE_TYPE_REQUIRED",
                "message": "候选类型无效",
            },
        )
    definitions = TYPE_FIELD_SCHEMAS.get(body.type_key, [])
    allowed = {definition["key"] for definition in definitions}
    invalid = sorted((set(body.payload) | set(body.field_states)) - allowed)
    if invalid:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "LORE_CANDIDATE_FIELD_INVALID",
                "message": "候选字段不属于当前类型",
                "field_errors": [
                    {"field": key, "message": "字段不属于当前设定类型"}
                    for key in invalid
                ],
            },
        )
    payload: dict[str, str | None] = {}
    states: dict[str, str] = {}
    errors: list[dict[str, str]] = []
    for definition in definitions:
        key = definition["key"]
        value = body.payload.get(key)
        if value is not None and not isinstance(value, str):
            errors.append({"field": key, "message": "字段值必须是文本或空"})
            continue
        state = body.field_states.get(
            key,
            "provided" if value not in (None, "") else "unknown",
        )
        if state == "provided" and value in (None, ""):
            errors.append({"field": key, "message": "provided 字段不能为空"})
        if state == "unknown" and value not in (None, ""):
            errors.append({"field": key, "message": "unknown 字段必须为空"})
        payload[key] = value
        states[key] = state
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "LORE_CANDIDATE_FIELD_INVALID",
                "message": "候选字段校验失败",
                "field_errors": errors,
            },
        )
    return payload, states


def _validate_resolutions(
    candidate: LoreExtractionCandidate,
    supplied: dict[str, str],
) -> dict[str, str]:
    suggestion_ids = {
        item.get("suggestion_id")
        for item in (candidate.duplicate_conflict_suggestions or [])
        if item.get("suggestion_id")
    }
    unknown = sorted(set(supplied) - suggestion_ids)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "LORE_CANDIDATE_SUGGESTION_INVALID",
                "message": "处理决定不属于当前候选提示",
                "suggestion_ids": unknown,
            },
        )
    merged = dict(candidate.suggestion_resolutions or {})
    merged.update(supplied)
    return merged


async def _record_candidate_revision(
    db: AsyncSession,
    candidate: LoreExtractionCandidate,
    user_id: str,
    change_kind: str,
) -> None:
    db.add(
        LoreCandidateRevision(
            candidate_id=candidate.id,
            revision=candidate.revision,
            type_key=candidate.type_key,
            name=candidate.name,
            summary=candidate.summary or "",
            payload=candidate.payload or {},
            field_states=candidate.field_states or {},
            suggestion_resolutions=candidate.suggestion_resolutions or {},
            user_overrides=candidate.user_overrides or {},
            change_kind=change_kind,
            created_by=user_id,
        )
    )


async def _action_progress(
    db: AsyncSession,
    project_id: str,
    batch_id: str,
) -> tuple[int, str | None]:
    remaining = await db.scalar(
        select(func.count())
        .select_from(LoreExtractionCandidate)
        .where(
            LoreExtractionCandidate.project_id == project_id,
            LoreExtractionCandidate.batch_id == batch_id,
            LoreExtractionCandidate.status == "pending_review",
        )
    )
    next_id = await db.scalar(
        select(LoreExtractionCandidate.id)
        .where(
            LoreExtractionCandidate.project_id == project_id,
            LoreExtractionCandidate.batch_id == batch_id,
            LoreExtractionCandidate.status == "pending_review",
        )
        .order_by(LoreExtractionCandidate.ordinal)
        .limit(1)
    )
    return int(remaining or 0), next_id


async def _build_action_response(
    db: AsyncSession,
    candidate: LoreExtractionCandidate,
    project_mode: str,
    result: str,
    replayed: bool,
) -> LoreCandidateActionResponse:
    remaining, next_id = await _action_progress(
        db,
        candidate.project_id,
        candidate.batch_id,
    )
    response = (
        await _candidate_responses(
            db,
            [candidate],
            project_mode=project_mode,
        )
    )[0]
    return LoreCandidateActionResponse(
        candidate=response,
        action_result=result,
        replayed=replayed,
        accepted_element_id=candidate.accepted_element_id,
        remaining_pending_count=remaining,
        next_pending_candidate_id=next_id,
    )


@router.post("", response_model=LoreExtractionBatchResponse, status_code=status.HTTP_201_CREATED)
async def create_extraction_batch(
    project_id: str,
    body: LoreExtractionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    if len(body.document_text) > MAX_EXTRACTION_SOURCE_CHARS:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "EXTRACTION_SOURCE_TOO_LONG",
                "message": (
                    f"当前提取最多支持 {MAX_EXTRACTION_SOURCE_CHARS} 个字符，"
                    "未截断或发起 LLM 调用"
                ),
            },
        )
    ensure_project_writes_available()
    document_hash = source_hash(body.document_text)
    existing = await db.scalar(
        select(LoreExtractionBatch).where(
            LoreExtractionBatch.project_id == project_id,
            LoreExtractionBatch.idempotency_key == body.idempotency_key,
        )
    )
    if existing is not None:
        if existing.source_hash != document_hash:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "EXTRACTION_IDEMPOTENCY_CONFLICT",
                    "message": "同一幂等键已绑定不同文档",
                },
            )
        return await _batch_response(db, existing)

    llm_client._reload()
    batch = LoreExtractionBatch(
        project_id=project_id,
        requested_by=current_user.id,
        idempotency_key=body.idempotency_key,
        source_kind=body.source_kind,
        source_ref=body.source_ref,
        source_text=body.document_text,
        source_hash=document_hash,
        extractor_version=EXTRACTOR_VERSION,
        model_name=llm_client.model if llm_client.api_key else None,
        status="running",
        llm_started_at=_utcnow(),
    )
    db.add(batch)
    try:
        await db.commit()
        await db.refresh(batch)
    except IntegrityError:
        await db.rollback()
        raced = await db.scalar(
            select(LoreExtractionBatch).where(
                LoreExtractionBatch.project_id == project_id,
                LoreExtractionBatch.idempotency_key == body.idempotency_key,
            )
        )
        if raced is None:
            raise
        if raced.source_hash != document_hash:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "EXTRACTION_IDEMPOTENCY_CONFLICT",
                    "message": "同一幂等键已绑定不同文档",
                },
            )
        return await _batch_response(db, raced)

    batch_id = batch.id

    try:
        raw_response = await llm_client.chat_once(
            build_extraction_messages(body.document_text),
            temperature=0.0,
            max_tokens=8000,
        )
    except LLMSingleCallError as exc:
        await _mark_batch_failed(
            db,
            batch,
            exc.code,
            exc.safe_message,
            retryable=exc.retryable,
            outcome_unknown=exc.outcome_unknown,
        )
        return await _batch_response(db, batch)

    batch.raw_response = raw_response
    try:
        ensure_project_writes_available()
        prepared = await prepare_candidates(
            db,
            project_id,
            body.document_text,
            raw_response,
        )
        for item in prepared:
            candidate = LoreExtractionCandidate(
                project_id=project_id,
                batch_id=batch.id,
                ordinal=item.ordinal,
                deterministic_key=item.deterministic_key,
                type_key=item.type_key,
                name=item.name,
                summary=item.summary,
                payload=item.payload,
                field_states=item.field_states,
                relation_suggestions=item.relation_suggestions,
                duplicate_conflict_suggestions=(
                    item.duplicate_conflict_suggestions
                ),
                suggestion_resolutions={},
                user_overrides={},
                needs_attention=candidate_needs_attention(
                    name=item.name,
                    type_key=item.type_key,
                    field_states=item.field_states,
                    suggestions=item.duplicate_conflict_suggestions,
                    resolutions={},
                ),
                status="pending_review",
            )
            db.add(candidate)
            await db.flush()
            db.add(
                LoreCandidateRevision(
                    candidate_id=candidate.id,
                    revision=1,
                    type_key=candidate.type_key,
                    name=candidate.name,
                    summary=candidate.summary,
                    payload=candidate.payload,
                    field_states=candidate.field_states,
                    suggestion_resolutions={},
                    user_overrides={},
                    change_kind="extracted",
                    created_by=current_user.id,
                )
            )
            for evidence in item.evidence:
                db.add(
                    LoreCandidateFieldEvidence(
                        candidate_id=candidate.id,
                        field_key=evidence.field_key,
                        value=evidence.value,
                        state=evidence.state,
                        excerpt=evidence.excerpt,
                        char_start=evidence.char_start,
                        char_end=evidence.char_end,
                        excerpt_hash=evidence.excerpt_hash,
                        source_hash=document_hash,
                        is_name=evidence.is_name,
                    )
                )
        ensure_project_writes_available()
        batch.status = "completed"
        batch.candidate_count = len(prepared)
        batch.llm_completed_at = _utcnow()
        batch.lock_version += 1
        await db.commit()
        await db.refresh(batch)
    except ProjectWriteFrozenError:
        await db.rollback()
        batch = await _load_batch(db, project_id, batch_id)
        batch.raw_response = raw_response
        await _mark_batch_failed(
            db,
            batch,
            "PROJECT_WRITE_FROZEN",
            "项目资料正在升级，未保存任何提取候选",
            retryable=True,
        )
        raise
    except ExtractionValidationError as exc:
        await db.rollback()
        batch = await _load_batch(db, project_id, batch_id)
        batch.raw_response = raw_response
        await _mark_batch_failed(db, batch, exc.code, exc.safe_message)
    except Exception:
        await db.rollback()
        batch = await _load_batch(db, project_id, batch_id)
        if batch.status != "completed":
            batch.raw_response = raw_response
            await _mark_batch_failed(
                db,
                batch,
                "EXTRACTION_SAVE_FAILED",
                "候选保存失败，未写入任何候选",
            )
    return await _batch_response(db, batch)


@router.get("/candidates", response_model=LoreCandidateInboxResponse)
async def list_project_extraction_candidates(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    q: Annotated[str | None, Query(max_length=200)] = None,
    candidate_status: Annotated[
        str | None,
        Query(alias="status", pattern="^(pending_review|accepted|rejected|failed)$"),
    ] = "pending_review",
    type_key: Annotated[
        str | None,
        Query(alias="type", max_length=50),
    ] = None,
    batch_id: Annotated[str | None, Query(max_length=32)] = None,
    needs_attention: bool | None = None,
):
    project = await get_project_for_owner(project_id, current_user, db)
    normalized_query = (q or "").strip().casefold()
    applied_filters: dict[str, object] = {
        "q": normalized_query,
        "status": candidate_status or "",
        "type": type_key or "",
        "batch_id": batch_id or "",
        "needs_attention": needs_attention,
    }
    query_signature = _candidate_inbox_signature(applied_filters)
    filters = [LoreExtractionCandidate.project_id == project_id]
    if candidate_status:
        filters.append(LoreExtractionCandidate.status == candidate_status)
    if type_key:
        filters.append(LoreExtractionCandidate.type_key == type_key)
    if batch_id:
        filters.append(LoreExtractionCandidate.batch_id == batch_id)
    if normalized_query:
        filters.append(
            or_(
                func.lower(LoreExtractionCandidate.name).contains(normalized_query),
                func.lower(LoreExtractionCandidate.summary).contains(normalized_query),
            )
        )
    if needs_attention is not None:
        filters.append(
            LoreExtractionCandidate.needs_attention.is_(
                needs_attention
            )
        )

    page_filters = list(filters)
    if cursor:
        cursor_data = _decode_cursor(cursor)
        if (
            cursor_data.get("kind") != "candidate_inbox"
            or cursor_data.get("project_id") != project_id
            or cursor_data.get("filters") != query_signature
        ):
            raise HTTPException(status_code=400, detail="分页游标与当前候选查询不匹配")
        after = cursor_data.get("after")
        if (
            not isinstance(after, list)
            or len(after) != 2
            or not all(isinstance(value, str) for value in after)
        ):
            raise HTTPException(status_code=400, detail="分页游标无效")
        try:
            after_time = datetime.fromisoformat(after[0])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="分页游标无效") from exc
        page_filters.append(
            or_(
                LoreExtractionCandidate.updated_at < after_time,
                (
                    (LoreExtractionCandidate.updated_at == after_time)
                    & (LoreExtractionCandidate.id < after[1])
                ),
            )
        )

    total = await db.scalar(
        select(func.count())
        .select_from(LoreExtractionCandidate)
        .where(*filters)
    )
    result = await db.execute(
        select(LoreExtractionCandidate)
        .where(*page_filters)
        .order_by(
            LoreExtractionCandidate.updated_at.desc(),
            LoreExtractionCandidate.id.desc(),
        )
        .limit(limit + 1)
    )
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor(
            {
                "v": 1,
                "kind": "candidate_inbox",
                "project_id": project_id,
                "filters": query_signature,
                "after": [last.updated_at.isoformat(), last.id],
            }
        )
    return LoreCandidateInboxResponse(
        items=await _candidate_responses(
            db,
            page,
            project_mode=project.lore_storage_mode or "legacy",
        ),
        next_cursor=next_cursor,
        has_more=has_more,
        total=int(total or 0),
        applied_filters=applied_filters,
        query_signature=query_signature,
    )


@router.get("/{batch_id}", response_model=LoreExtractionBatchResponse)
async def get_extraction_batch(
    project_id: str,
    batch_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    batch = await _load_batch(db, project_id, batch_id)
    return await _batch_response(db, batch)


@router.get(
    "/{batch_id}/candidates",
    response_model=LoreExtractionCandidatesResponse,
)
async def list_extraction_candidates(
    project_id: str,
    batch_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)
    await _load_batch(db, project_id, batch_id)
    result = await db.execute(
        select(LoreExtractionCandidate)
        .where(
            LoreExtractionCandidate.project_id == project_id,
            LoreExtractionCandidate.batch_id == batch_id,
        )
        .order_by(LoreExtractionCandidate.ordinal)
    )
    candidates = list(result.scalars().all())
    return LoreExtractionCandidatesResponse(
        items=await _candidate_responses(
            db,
            candidates,
            project_mode=project.lore_storage_mode or "legacy",
        ),
        total=len(candidates),
    )


@router.get(
    "/{batch_id}/candidates/{candidate_id}",
    response_model=LoreExtractionCandidateResponse,
)
async def get_extraction_candidate(
    project_id: str,
    batch_id: str,
    candidate_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)
    await _load_batch(db, project_id, batch_id)
    candidate = await _load_candidate(
        db,
        project_id,
        batch_id,
        candidate_id,
    )
    return (
        await _candidate_responses(
            db,
            [candidate],
            project_mode=project.lore_storage_mode or "legacy",
        )
    )[0]


@router.get(
    "/{batch_id}/candidates/{candidate_id}/revisions",
    response_model=LoreCandidateRevisionsResponse,
)
async def list_candidate_revisions(
    project_id: str,
    batch_id: str,
    candidate_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    await get_project_for_owner(project_id, current_user, db)
    await _load_candidate(db, project_id, batch_id, candidate_id)
    result = await db.execute(
        select(LoreCandidateRevision)
        .where(LoreCandidateRevision.candidate_id == candidate_id)
        .order_by(LoreCandidateRevision.revision)
    )
    revisions = list(result.scalars().all())
    return LoreCandidateRevisionsResponse(
        items=[
            LoreCandidateRevisionResponse(
                revision=item.revision,
                type_key=item.type_key,
                name=item.name,
                summary=item.summary or "",
                payload=item.payload or {},
                field_states=item.field_states or {},
                suggestion_resolutions=item.suggestion_resolutions or {},
                user_overrides=item.user_overrides or {},
                change_kind=item.change_kind,
                created_by=item.created_by,
                created_at=item.created_at,
            )
            for item in revisions
        ],
        total=len(revisions),
    )


@router.patch(
    "/{batch_id}/candidates/{candidate_id}",
    response_model=LoreExtractionCandidateResponse,
)
async def edit_extraction_candidate(
    project_id: str,
    batch_id: str,
    candidate_id: str,
    body: LoreCandidateEdit,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)
    ensure_project_writes_available()
    candidate = await _load_candidate(db, project_id, batch_id, candidate_id)
    if candidate.status != "pending_review":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LORE_CANDIDATE_NOT_PENDING",
                "message": "只有待审候选可以编辑",
                "current_status": candidate.status,
                "latest_revision": candidate.revision,
            },
        )
    payload, field_states = _validate_candidate_content(body)
    resolutions = _validate_resolutions(candidate, body.suggestion_resolutions)
    baseline = await db.scalar(
        select(LoreCandidateRevision).where(
            LoreCandidateRevision.candidate_id == candidate.id,
            LoreCandidateRevision.revision == 1,
        )
    )
    if baseline is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LORE_CANDIDATE_AUDIT_INCOMPLETE",
                "message": "候选缺少初始提取记录，暂时无法安全编辑",
                "candidate_id": candidate.id,
                "reload_required": True,
            },
        )
    now = _utcnow().isoformat()
    overrides: dict[str, dict[str, object]] = {}
    current_values: dict[str, object] = {"name": body.name, **payload}
    current_states = {
        "name": "provided" if body.name else "unknown",
        **field_states,
    }
    baseline_values: dict[str, object] = {
        "name": baseline.name,
        **(baseline.payload or {}),
    }
    baseline_states = {
        "name": "provided" if baseline.name else "unknown",
        **(baseline.field_states or {}),
    }
    for key, value in current_values.items():
        state = current_states.get(key, "unknown")
        if (
            value == baseline_values.get(key)
            and state == baseline_states.get(key, "unknown")
        ):
            continue
        overrides[key] = {
            "value": value,
            "origin": "user_cleared" if value in (None, "") else "user_override",
            "state": state,
            "original_value": baseline_values.get(key),
            "original_state": baseline_states.get(key, "unknown"),
            "edited_by": current_user.id,
            "edited_at": now,
        }
    if body.type_key != baseline.type_key:
        overrides["type_key"] = {
            "value": body.type_key,
            "origin": "user_override",
            "original_value": baseline.type_key,
            "edited_by": current_user.id,
            "edited_at": now,
        }
    if body.summary != (baseline.summary or ""):
        overrides["summary"] = {
            "value": body.summary,
            "origin": "user_cleared" if not body.summary else "user_override",
            "original_value": baseline.summary or "",
            "edited_by": current_user.id,
            "edited_at": now,
        }
    await _claim_candidate_revision(db, candidate, body.expected_version)
    candidate.type_key = body.type_key
    candidate.name = body.name
    candidate.summary = body.summary
    candidate.payload = payload
    candidate.field_states = field_states
    candidate.suggestion_resolutions = resolutions
    candidate.user_overrides = overrides
    candidate.needs_attention = candidate_needs_attention(
        name=candidate.name,
        type_key=candidate.type_key,
        field_states=candidate.field_states,
        suggestions=candidate.duplicate_conflict_suggestions,
        resolutions=candidate.suggestion_resolutions,
    )
    await _record_candidate_revision(db, candidate, current_user.id, "edited")
    ensure_project_writes_available()
    await db.commit()
    await db.refresh(candidate)
    return (
        await _candidate_responses(
            db,
            [candidate],
            project_mode=project.lore_storage_mode or "legacy",
        )
    )[0]


async def _candidate_sources(
    db: AsyncSession,
    batch: LoreExtractionBatch,
    candidate: LoreExtractionCandidate,
) -> list[dict[str, object]]:
    result = await db.execute(
        select(LoreCandidateFieldEvidence).where(
            LoreCandidateFieldEvidence.candidate_id == candidate.id,
            LoreCandidateFieldEvidence.char_start.is_not(None),
            LoreCandidateFieldEvidence.char_end.is_not(None),
        )
    )
    evidence = list(result.scalars().all())
    starts = [item.char_start for item in evidence if item.char_start is not None]
    ends = [item.char_end for item in evidence if item.char_end is not None]
    locator: dict[str, object] = {
        "batch_id": batch.id,
        "candidate_id": candidate.id,
        "source_hash": batch.source_hash,
        "complete": bool(starts),
    }
    excerpt = None
    if starts and ends:
        start, end = min(starts), max(ends)
        locator.update({"char_start": start, "char_end": end})
        excerpt = batch.source_text[start:end]
    has_overrides = bool(candidate.user_overrides)
    sources: list[dict[str, object]] = [
        {
            "kind": "system_extract",
            "reference": batch.id,
            "locator": locator,
            "excerpt": excerpt,
            "confirmation_status": "provided",
            "is_primary": not has_overrides,
        }
    ]
    if has_overrides:
        sources.append(
            {
                "kind": "manual_review",
                "reference": candidate.id,
                "locator": {
                    "batch_id": batch.id,
                    "candidate_id": candidate.id,
                    "revision": candidate.revision,
                },
                "excerpt": None,
                "confirmation_status": "provided",
                "is_primary": True,
            }
        )
    return sources


@router.post(
    "/{batch_id}/candidates/{candidate_id}/accept",
    response_model=LoreCandidateActionResponse,
)
async def accept_extraction_candidate(
    project_id: str,
    batch_id: str,
    candidate_id: str,
    body: LoreCandidateActionInput,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)
    ensure_project_writes_available()
    batch = await _load_batch(db, project_id, batch_id)
    candidate = await _load_candidate(db, project_id, batch_id, candidate_id)
    if candidate.status == "accepted":
        return await _build_action_response(
            db,
            candidate,
            project.lore_storage_mode or "legacy",
            "already_accepted",
            True,
        )
    if candidate.status == "rejected":
        raise _terminal_conflict(candidate, "接受")
    if (project.lore_storage_mode or "legacy") != "relational":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LORE_MODE_NOT_RELATIONAL",
                "message": "项目尚未切换到关系存储模式，无法接受候选",
                "current_mode": project.lore_storage_mode or "legacy",
            },
        )
    if candidate.status != "pending_review":
        raise HTTPException(status_code=409, detail="候选状态不允许接受")
    if not candidate.name:
        raise HTTPException(
            status_code=422,
            detail={"code": "LORE_CANDIDATE_NAME_REQUIRED", "message": "候选名称不能为空"},
        )
    if not candidate.type_key or candidate.type_key not in BUILTIN_TYPE_KEYS:
        raise HTTPException(
            status_code=422,
            detail={"code": "LORE_CANDIDATE_TYPE_REQUIRED", "message": "候选类型无效"},
        )
    pending_fields = sorted(
        key
        for key, state in (candidate.field_states or {}).items()
        if state == "needs_confirmation"
    )
    if pending_fields:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "LORE_CANDIDATE_FIELDS_NEED_CONFIRMATION",
                "message": "仍有字段需要确认",
                "fields": pending_fields,
            },
        )
    resolutions = _validate_resolutions(candidate, body.suggestion_resolutions)
    unresolved = sorted(
        item["suggestion_id"]
        for item in (candidate.duplicate_conflict_suggestions or [])
        if item.get("suggestion_id")
        and resolutions.get(item["suggestion_id"])
        not in ("accept_as_new", "dismissed")
    )
    if unresolved:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "LORE_CANDIDATE_SUGGESTIONS_UNRESOLVED",
                "message": "请先处理可能重复或冲突的提示",
                "suggestion_ids": unresolved,
            },
        )
    try:
        await _claim_candidate_revision(db, candidate, body.expected_version)
        candidate.suggestion_resolutions = resolutions
        element = await create_element(
            db=db,
            project_id=project_id,
            user_id=current_user.id,
            type_key=candidate.type_key,
            name=candidate.name,
            summary=candidate.summary or "",
            payload=candidate.payload or {},
            field_states=candidate.field_states or {},
            sources_input=await _candidate_sources(db, batch, candidate),
        )
        candidate.status = "accepted"
        candidate.needs_attention = False
        candidate.accepted_element_id = element.id
        await _record_candidate_revision(db, candidate, current_user.id, "accepted")
        ensure_project_writes_available()
        await db.commit()
        await db.refresh(candidate)
        return await _build_action_response(
            db, candidate, "relational", "accepted", False
        )
    except LoreWriteError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except ProjectWriteFrozenError:
        await db.rollback()
        raise
    except HTTPException as exc:
        await db.rollback()
        if (
            isinstance(exc.detail, dict)
            and exc.detail.get("code") == "LORE_CANDIDATE_VERSION_CONFLICT"
        ):
            latest = await _load_candidate(db, project_id, batch_id, candidate_id)
            if latest.status == "accepted":
                return await _build_action_response(
                    db, latest, "relational", "already_accepted", True
                )
            if latest.status == "rejected":
                raise _terminal_conflict(latest, "接受")
        raise
    except IntegrityError as exc:
        await db.rollback()
        latest = await _load_candidate(db, project_id, batch_id, candidate_id)
        if latest.status == "accepted":
            return await _build_action_response(
                db, latest, "relational", "already_accepted", True
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LORE_CANDIDATE_ACCEPT_CONFLICT",
                "message": "候选接受发生并发冲突，请重新加载",
            },
        ) from exc
    except Exception as exc:
        await db.rollback()
        latest = await _load_candidate(db, project_id, batch_id, candidate_id)
        if latest.status == "accepted":
            return await _build_action_response(
                db, latest, "relational", "already_accepted", True
            )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "LORE_CANDIDATE_ACCEPT_FAILED",
                "message": "候选接受失败，未创建正式设定",
            },
        ) from exc


@router.post(
    "/{batch_id}/candidates/{candidate_id}/reject",
    response_model=LoreCandidateActionResponse,
)
async def reject_extraction_candidate(
    project_id: str,
    batch_id: str,
    candidate_id: str,
    body: LoreCandidateActionInput,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)
    ensure_project_writes_available()
    await _load_batch(db, project_id, batch_id)
    candidate = await _load_candidate(db, project_id, batch_id, candidate_id)
    mode = project.lore_storage_mode or "legacy"
    if candidate.status == "rejected":
        return await _build_action_response(
            db, candidate, mode, "already_rejected", True
        )
    if candidate.status == "accepted":
        raise _terminal_conflict(candidate, "拒绝")
    if candidate.status != "pending_review":
        raise HTTPException(status_code=409, detail="候选状态不允许拒绝")
    resolutions = _validate_resolutions(candidate, body.suggestion_resolutions)
    try:
        await _claim_candidate_revision(db, candidate, body.expected_version)
        candidate.suggestion_resolutions = resolutions
        candidate.status = "rejected"
        candidate.needs_attention = False
        await _record_candidate_revision(db, candidate, current_user.id, "rejected")
        ensure_project_writes_available()
        await db.commit()
        await db.refresh(candidate)
        return await _build_action_response(
            db, candidate, mode, "rejected", False
        )
    except ProjectWriteFrozenError:
        await db.rollback()
        raise
    except HTTPException as exc:
        await db.rollback()
        if (
            isinstance(exc.detail, dict)
            and exc.detail.get("code") == "LORE_CANDIDATE_VERSION_CONFLICT"
        ):
            latest = await _load_candidate(db, project_id, batch_id, candidate_id)
            if latest.status == "rejected":
                return await _build_action_response(
                    db, latest, mode, "already_rejected", True
                )
            if latest.status == "accepted":
                raise _terminal_conflict(latest, "拒绝")
        raise
    except IntegrityError as exc:
        await db.rollback()
        latest = await _load_candidate(db, project_id, batch_id, candidate_id)
        if latest.status == "rejected":
            return await _build_action_response(
                db, latest, mode, "already_rejected", True
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LORE_CANDIDATE_REJECT_CONFLICT",
                "message": "候选拒绝发生并发冲突，请重新加载",
            },
        ) from exc
    except Exception as exc:
        await db.rollback()
        latest = await _load_candidate(db, project_id, batch_id, candidate_id)
        if latest.status == "rejected":
            return await _build_action_response(
                db, latest, mode, "already_rejected", True
            )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "LORE_CANDIDATE_REJECT_FAILED",
                "message": "候选拒绝失败，请重新加载后重试",
            },
        ) from exc
