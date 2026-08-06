"""Read-only, deterministic preview planning for confirmed lore duplicates."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.lore_relation_types import RELATION_TYPES
from app.core.lore_write import (
    LoreWriteError,
    check_writes_available,
    field_schema_for_type,
    generation_eligible,
    validate_element_content,
)
from app.models.lore import (
    ElementRelation,
    ElementSource,
    LoreReviewSuggestion,
    SettingElement,
    SettingType,
)
from app.schemas.lore import (
    LoreMergePreviewInput,
    LoreMergePreviewResponse,
    LoreMergeRelationPlan,
    LoreMergeSourceImpact,
    LoreReviewEndpoint,
    LoreSourceSummary,
    LoreTypeSummary,
)

_TOKEN_VERSION = 1
_TOKEN_TTL = timedelta(minutes=15)
_TOKEN_CONTEXT = b"lore-merge-preview:v1\n"
_SOURCE_KIND_LABELS = {
    "manual": "手动创建",
    "manual_review": "人工复核",
    "document_import": "文档导入",
    "system_extract": "AI 提取",
    "migration": "旧数据迁移",
    "legacy_import": "旧数据导入",
}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _token_secret() -> bytes:
    return hashlib.sha256(
        _TOKEN_CONTEXT + (settings.JWT_SECRET or "development-secret").encode()
    ).digest()


def _encode_token(claims: dict[str, Any]) -> str:
    payload = _canonical(claims).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(_token_secret(), payload, hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def decode_merge_preview_token(token: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Verify a preview token for the future commit endpoint."""
    try:
        encoded, signature = token.split(".", 1)
        payload = base64.urlsafe_b64decode((encoded + "=" * (-len(encoded) % 4)).encode())
        expected = hmac.new(_token_secret(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        claims = json.loads(payload)
        if not isinstance(claims, dict) or claims.get("v") != _TOKEN_VERSION:
            raise ValueError("version")
        current = int((now or datetime.now(UTC)).timestamp())
        if current >= int(claims["exp"]):
            raise ValueError("expired")
        return claims
    except (
        ValueError,
        TypeError,
        KeyError,
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise LoreWriteError(
            {
                "code": "LORE_MERGE_PREVIEW_TOKEN_INVALID",
                "message": "合并预览已失效，请重新预览后再继续",
                "retryable": False,
            },
            status_code=409,
        ) from exc


def _conflict(code: str, message: str) -> LoreWriteError:
    return LoreWriteError(
        {"code": code, "message": message, "retryable": False},
        status_code=409,
    )


def _source_anchor(source: ElementSource) -> dict[str, Any]:
    current_excerpt_hash = (
        hashlib.sha256(source.excerpt.encode("utf-8")).hexdigest()
        if source.excerpt
        else None
    )
    return {
        "id": source.id,
        "element_id": source.element_id,
        "kind": source.source_kind,
        "reference": source.source_ref,
        "locator": source.locator or {},
        "stored_excerpt_hash": source.excerpt_hash,
        "current_excerpt_hash": current_excerpt_hash,
        "confirmation_status": source.confirmation_status,
        "is_primary": source.is_primary,
    }


def _source_identity(source: ElementSource) -> str:
    return _fingerprint(
        {
            key: value
            for key, value in _source_anchor(source).items()
            if key not in {"id", "element_id"}
        }
    )


def _relation_anchor(relation: ElementRelation) -> dict[str, Any]:
    return {
        "id": relation.id,
        "source_element_id": relation.source_element_id,
        "target_element_id": relation.target_element_id,
        "relation_key": relation.relation_key,
        "forward_label": relation.forward_label or "",
        "reverse_label": relation.reverse_label or "",
        "description": relation.description or "",
        "metadata": relation.metadata_ or {},
        "status": relation.status,
        "version_no": relation.version_no,
        "lock_version": relation.lock_version,
    }


def _relation_semantics(relation: ElementRelation) -> tuple[Any, ...]:
    return (
        relation.forward_label or "",
        relation.reverse_label or "",
        relation.description or "",
        _canonical(relation.metadata_ or {}),
        relation.status,
    )


def _relation_identity(
    relation: ElementRelation,
    source_element_id: str,
    target_element_id: str,
) -> tuple[str, str, str]:
    """Canonicalize both new and historical symmetric relation endpoints."""
    definition = RELATION_TYPES.get(relation.relation_key)
    if (
        definition
        and definition.get("symmetric")
        and source_element_id > target_element_id
    ):
        source_element_id, target_element_id = (
            target_element_id,
            source_element_id,
        )
    return source_element_id, target_element_id, relation.relation_key


def _plan_relations(
    relations: list[ElementRelation],
    survivor_id: str,
    merged_id: str,
) -> tuple[list[LoreMergeRelationPlan], list[str]]:
    plans: list[LoreMergeRelationPlan] = []
    blockers: list[str] = []
    unaffected = [
        relation
        for relation in relations
        if merged_id not in (relation.source_element_id, relation.target_element_id)
    ]
    occupied: dict[tuple[str, str, str], list[ElementRelation]] = {}
    for relation in sorted(unaffected, key=lambda item: item.id):
        identity = _relation_identity(
            relation,
            relation.source_element_id,
            relation.target_element_id,
        )
        occupied.setdefault(identity, []).append(relation)
    affected = sorted(
        (
            relation
            for relation in relations
            if merged_id in (relation.source_element_id, relation.target_element_id)
        ),
        key=lambda relation: relation.id,
    )
    for relation in affected:
        planned_source = (
            survivor_id
            if relation.source_element_id == merged_id
            else relation.source_element_id
        )
        planned_target = (
            survivor_id
            if relation.target_element_id == merged_id
            else relation.target_element_id
        )
        planned_source, planned_target, _ = _relation_identity(
            relation,
            planned_source,
            planned_target,
        )
        common = {
            "relation_id": relation.id,
            "current_source_element_id": relation.source_element_id,
            "current_target_element_id": relation.target_element_id,
            "planned_source_element_id": planned_source,
            "planned_target_element_id": planned_target,
            "relation_key": relation.relation_key,
        }
        if planned_source == planned_target:
            plans.append(
                LoreMergeRelationPlan(
                    **common,
                    action="self_loop_archive",
                    reason="合并后会形成自指关系，计划归档原关系",
                )
            )
            continue
        identity = _relation_identity(relation, planned_source, planned_target)
        retained_candidates = occupied.get(identity, [])
        if not retained_candidates:
            occupied[identity] = [relation]
            plans.append(
                LoreMergeRelationPlan(
                    **common,
                    action="rewire",
                    reason="保留关系并将合并端点重定向至保留设定",
                )
            )
            continue
        retained = min(retained_candidates, key=lambda item: item.id)
        if all(
            _relation_semantics(candidate) == _relation_semantics(relation)
            for candidate in retained_candidates
        ):
            plans.append(
                LoreMergeRelationPlan(
                    **common,
                    action="exact_duplicate_archive",
                    retained_relation_id=retained.id,
                    reason=(
                        "相同端点的关系语义完全一致，"
                        "计划保留已有关系并归档重复项"
                    ),
                )
            )
            continue
        collision_ids = "、".join(
            candidate.id for candidate in sorted(
                retained_candidates, key=lambda item: item.id
            )
        )
        reason = (
            f"关系 {relation.id} 合并后与 {collision_ids} "
            "占用相同端点和类型，但标签、描述、元数据或状态不一致"
        )
        blockers.append(reason)
        plans.append(
            LoreMergeRelationPlan(
                **common,
                action="blocker",
                retained_relation_id=retained.id,
                reason=reason,
            )
        )
    return plans, blockers


def _validate_choice(
    choice: str,
    final_value: Any,
    left_value: Any,
    right_value: Any,
    field: str,
) -> None:
    expected = (
        left_value
        if choice == "survivor"
        else right_value if choice == "merged" else final_value
    )
    if choice != "manual" and final_value != expected:
        raise LoreWriteError(
            {
                "code": "LORE_MERGE_SELECTION_INVALID",
                "message": "合并字段选择与预览内容不一致",
                "field_errors": [
                    {
                        "field": field,
                        "message": f"选择 {choice} 时必须使用对应原值",
                    }
                ],
            },
            status_code=422,
        )


async def _endpoint(
    db: AsyncSession,
    element: SettingElement,
    setting_type: SettingType,
    sources: list[ElementSource],
) -> LoreReviewEndpoint:
    del db
    return LoreReviewEndpoint(
        id=element.id,
        name=element.name,
        type=LoreTypeSummary(key=setting_type.key, display_name=setting_type.display_name),
        summary=element.summary or "",
        payload=element.payload or {},
        field_states=element.field_states or {},
        content_version=element.content_version,
        lifecycle_status=element.lifecycle_status,
        enabled=element.enabled,
        sources=[
            LoreSourceSummary(
                id=source.id,
                kind=source.source_kind,
                label=_SOURCE_KIND_LABELS.get(source.source_kind, "其他来源"),
                is_primary=source.is_primary,
                created_at=source.created_at,
                reference=source.source_ref,
                locator=source.locator or {},
                excerpt=source.excerpt,
                excerpt_hash=source.excerpt_hash,
                confirmation_status=source.confirmation_status,
            )
            for source in sources
        ],
    )


async def build_merge_preview(
    db: AsyncSession,
    *,
    project_id: str,
    user_id: str,
    suggestion_id: str,
    body: LoreMergePreviewInput,
) -> LoreMergePreviewResponse:
    """Build a zero-write merge plan tied to current evidence and versions."""
    check_writes_available()
    suggestion = await db.scalar(
        select(LoreReviewSuggestion).where(
            LoreReviewSuggestion.project_id == project_id,
            LoreReviewSuggestion.id == suggestion_id,
        )
    )
    if suggestion is None:
        raise LoreWriteError("设定线索不存在", status_code=404)
    if (
        suggestion.kind != "possible_duplicate"
        or suggestion.review_status != "confirmed_duplicate"
        or suggestion.detection_state != "active"
        or suggestion.decided_evidence_revision != suggestion.evidence_revision
    ):
        raise _conflict(
            "LORE_MERGE_REVIEW_NOT_CONFIRMED",
            "只能预览已按当前证据确认为重复的设定",
        )
    if (
        suggestion.lock_version != body.suggestion_expected_version
        or suggestion.evidence_revision != body.expected_evidence_revision
    ):
        raise _conflict("LORE_MERGE_REVIEW_STALE", "设定线索已变化，请重新查看")
    endpoint_ids = {suggestion.left_element_id, suggestion.right_element_id}
    if (
        body.survivor_element_id == body.merged_element_id
        or {body.survivor_element_id, body.merged_element_id} != endpoint_ids
    ):
        raise LoreWriteError(
            {
                "code": "LORE_MERGE_ENDPOINT_INVALID",
                "message": "保留项和合并项必须是当前线索的两个设定",
            },
            status_code=422,
        )
    rows = await db.execute(
        select(SettingElement)
        .where(
            SettingElement.project_id == project_id,
            SettingElement.id.in_(endpoint_ids),
        )
        .order_by(SettingElement.id)
    )
    elements = {element.id: element for element in rows.scalars().all()}
    survivor = elements.get(body.survivor_element_id)
    merged = elements.get(body.merged_element_id)
    if survivor is None or merged is None:
        raise _conflict("LORE_MERGE_ENDPOINT_STALE", "合并对象已变化，请重新查看")
    expected_versions = (
        survivor.lock_version == body.survivor_expected_lock_version
        and survivor.content_version == body.survivor_expected_content_version
        and merged.lock_version == body.merged_expected_lock_version
        and merged.content_version == body.merged_expected_content_version
        and suggestion.left_content_version == elements[suggestion.left_element_id].content_version
        and suggestion.right_content_version
        == elements[suggestion.right_element_id].content_version
    )
    if not expected_versions:
        raise _conflict("LORE_MERGE_ENDPOINT_STALE", "合并对象已变化，请重新预览")
    if any(
        element.confirmation_status != "confirmed"
        or element.lifecycle_status != "active"
        or element.merged_into_element_id is not None
        for element in (survivor, merged)
    ):
        raise _conflict(
            "LORE_MERGE_ENDPOINT_UNAVAILABLE",
            "合并对象必须是未合并的已确认活动设定",
        )
    if survivor.type_id != merged.type_id:
        raise _conflict("LORE_MERGE_TYPE_MISMATCH", "不同类型的设定不能直接合并")
    setting_type = await db.scalar(
        select(SettingType).where(
            SettingType.project_id == project_id,
            SettingType.id == survivor.type_id,
            SettingType.status == "active",
        )
    )
    if setting_type is None:
        raise _conflict("LORE_MERGE_TYPE_UNAVAILABLE", "设定类型已不可用")

    schema_keys = [field["key"] for field in field_schema_for_type(setting_type)]
    if set(body.field_choices) != set(schema_keys):
        raise LoreWriteError(
            {
                "code": "LORE_MERGE_SELECTION_INCOMPLETE",
                "message": "必须为当前类型的每个字段选择保留值",
                "expected_fields": schema_keys,
            },
            status_code=422,
        )
    if not body.final_name.strip():
        raise LoreWriteError(
            {
                "code": "LORE_MERGE_SELECTION_INVALID",
                "message": "合并后的设定名称不能为空",
                "field_errors": [{"field": "name", "message": "请填写设定名称"}],
            },
            status_code=422,
        )
    _validate_choice(body.name_choice, body.final_name, survivor.name, merged.name, "name")
    _validate_choice(
        body.summary_choice,
        body.final_summary,
        survivor.summary or "",
        merged.summary or "",
        "summary",
    )
    for key in schema_keys:
        choice = body.field_choices[key]
        source = survivor if choice == "survivor" else merged
        if choice != "manual":
            _validate_choice(
                choice,
                body.final_payload.get(key),
                (survivor.payload or {}).get(key),
                (merged.payload or {}).get(key),
                key,
            )
            expected_state = (source.field_states or {}).get(key, "unknown")
            if body.final_field_states.get(key) != expected_state:
                raise LoreWriteError(
                    {
                        "code": "LORE_MERGE_SELECTION_INVALID",
                        "message": "字段状态必须与所选原值保持一致",
                        "field_errors": [{"field": key, "message": "字段状态不匹配"}],
                    },
                    status_code=422,
                )
    derived_states = validate_element_content(
        setting_type, body.final_payload, body.final_field_states
    )
    if set(body.final_field_states) != set(schema_keys):
        raise LoreWriteError(
            {
                "code": "LORE_MERGE_STATE_INCOMPLETE",
                "message": "合并后的字段状态不完整",
            },
            status_code=422,
        )

    source_rows = await db.execute(
        select(ElementSource)
        .where(
            ElementSource.project_id == project_id,
            ElementSource.element_id.in_(endpoint_ids),
        )
        .order_by(ElementSource.element_id, ElementSource.id)
    )
    sources = list(source_rows.scalars().all())
    sources_by_element = {
        element_id: [source for source in sources if source.element_id == element_id]
        for element_id in endpoint_ids
    }
    survivor_sources = sources_by_element[survivor.id]
    merged_sources = sources_by_element[merged.id]
    survivor_source_identities = {_source_identity(source) for source in survivor_sources}
    exact_duplicate_pairs = sum(
        1 for source in merged_sources if _source_identity(source) in survivor_source_identities
    )
    source_fingerprint = _fingerprint([_source_anchor(source) for source in sources])

    relation_rows = await db.execute(
        select(ElementRelation)
        .where(
            ElementRelation.project_id == project_id,
            or_(
                ElementRelation.source_element_id.in_(endpoint_ids),
                ElementRelation.target_element_id.in_(endpoint_ids),
            ),
        )
        .order_by(ElementRelation.id)
    )
    relations = list(relation_rows.scalars().all())
    relation_fingerprint = _fingerprint([_relation_anchor(relation) for relation in relations])
    relation_plan, blockers = _plan_relations(relations, survivor.id, merged.id)
    selection_snapshot = {
        "name": body.name_choice,
        "summary": body.summary_choice,
        "fields": dict(sorted(body.field_choices.items())),
        "final_content_fingerprint": _fingerprint(
            {
                "name": body.final_name,
                "summary": body.final_summary,
                "payload": body.final_payload,
                "field_states": derived_states,
            }
        ),
    }
    plan_fingerprint = _fingerprint(
        [plan.model_dump(mode="json") for plan in relation_plan]
    )
    now = datetime.now(UTC)
    expires_at = now + _TOKEN_TTL
    claims = {
        "v": _TOKEN_VERSION,
        "sub": user_id,
        "project_id": project_id,
        "suggestion_id": suggestion.id,
        "suggestion_version": suggestion.lock_version,
        "evidence_revision": suggestion.evidence_revision,
        "evidence_fingerprint": suggestion.evidence_fingerprint,
        "survivor_id": survivor.id,
        "merged_id": merged.id,
        "survivor_lock_version": survivor.lock_version,
        "survivor_content_version": survivor.content_version,
        "merged_lock_version": merged.lock_version,
        "merged_content_version": merged.content_version,
        "source_fingerprint": source_fingerprint,
        "relation_fingerprint": relation_fingerprint,
        "selection_fingerprint": _fingerprint(selection_snapshot),
        "plan_fingerprint": plan_fingerprint,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    preview_element = SettingElement(
        confirmation_status=survivor.confirmation_status,
        lifecycle_status=survivor.lifecycle_status,
        enabled=survivor.enabled,
        field_states=derived_states,
    )
    check_writes_available()
    return LoreMergePreviewResponse(
        suggestion_id=suggestion.id,
        survivor=await _endpoint(db, survivor, setting_type, survivor_sources),
        merged=await _endpoint(db, merged, setting_type, merged_sources),
        final_name=body.final_name,
        final_summary=body.final_summary,
        final_payload=body.final_payload,
        final_field_states=derived_states,
        selection_snapshot=selection_snapshot,
        source_impact=LoreMergeSourceImpact(
            survivor_source_count=len(survivor_sources),
            merged_source_count=len(merged_sources),
            preserved_total=len(sources),
            exact_duplicate_pairs=exact_duplicate_pairs,
        ),
        relation_plan=relation_plan,
        blockers=blockers,
        would_be_generation_eligible=generation_eligible(preview_element),
        preview_token=_encode_token(claims),
        expires_at=expires_at,
        commit_available=False,
    )
