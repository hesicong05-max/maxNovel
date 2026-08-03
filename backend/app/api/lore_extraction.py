"""Persistent, idempotent lore extraction API."""

from __future__ import annotations

from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import User, get_current_user, get_project_for_owner
from app.core.llm_client import LLMSingleCallError, llm_client
from app.core.lore_extraction import (
    EXTRACTOR_VERSION,
    ExtractionValidationError,
    build_extraction_messages,
    field_display_label,
    prepare_candidates,
    source_hash,
    type_display_name,
)
from app.core.maintenance import (
    ProjectWriteFrozenError,
    ensure_project_writes_available,
)
from app.database import get_db
from app.models.extraction import (
    LoreCandidateFieldEvidence,
    LoreExtractionBatch,
    LoreExtractionCandidate,
)
from app.models.project import _utcnow
from app.schemas.extraction import (
    MAX_EXTRACTION_SOURCE_CHARS,
    LoreCandidateEvidenceResponse,
    LoreExtractionBatchResponse,
    LoreExtractionCandidateResponse,
    LoreExtractionCandidatesResponse,
    LoreExtractionCreate,
)


router = APIRouter(
    prefix="/api/projects/{project_id}/lore/extractions",
    tags=["lore-extraction"],
)


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
        disabled_reasons: list[str] = []
        if not candidate.name:
            disabled_reasons.append("name_missing")
        if not candidate.type_key:
            disabled_reasons.append("type_missing")
        if "needs_confirmation" in (candidate.field_states or {}).values():
            disabled_reasons.append("fields_need_confirmation")
        if candidate.status != "pending_review":
            disabled_reasons.append("candidate_not_pending")
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
                status=candidate.status,
                revision=candidate.revision,
                accepted_element_id=candidate.accepted_element_id,
                error_code=candidate.error_code,
                evidence=[
                    LoreCandidateEvidenceResponse(
                        id=item.id,
                        field_key=item.field_key,
                        label=field_display_label(
                            candidate.type_key,
                            item.field_key,
                        ),
                        value=item.value,
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
                can_accept=not disabled_reasons,
                disabled_reasons=disabled_reasons,
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
                status="pending_review",
            )
            db.add(candidate)
            await db.flush()
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
    await get_project_for_owner(project_id, current_user, db)
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
        items=await _candidate_responses(db, candidates),
        total=len(candidates),
    )
