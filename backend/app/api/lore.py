"""Lore API — read projection and relational write endpoints."""

import base64
import binascii
import hashlib
import hmac
import json
from collections import Counter
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import User, get_current_user, get_project_for_owner
from app.core.lore_migration import (
    LoreProjection,
    ProjectedLoreElement,
    TYPE_DISPLAY_NAMES,
    normalize_lore_name,
    project_legacy_worldview,
    type_field_definitions,
    validate_projection,
)
from app.core.lore_write import (
    LoreStaleVersionError,
    LoreWriteError,
    change_element_state,
    change_relation_state,
    check_writes_available,
    create_custom_type,
    create_element,
    create_relation,
    field_schema_for_type,
    generation_eligible,
    restore_element_version_content,
    update_element_content,
    update_relation,
)
from app.database import get_db
from app.models.extraction import LoreExtractionCandidate
from app.models.lore import (
    ElementRelation,
    ElementRelationVersion,
    ElementSource,
    ElementVersion,
    LoreElementCreateOperation,
    SettingElement,
    SettingType,
)
from app.models.project import Worldview
from app.schemas.lore import (
    LoreElementCreate,
    LoreElementCreateResponse,
    LoreElementDetail,
    LoreElementListItem,
    LoreElementResponse,
    LoreElementStateInput,
    LoreElementUpdate,
    LoreFacetCount,
    LoreFacets,
    LoreFieldDefinition,
    LoreListResponse,
    LoreMigrationStatus,
    LoreRelationCreate,
    LoreRelationEndpoint,
    LoreRelationListResponse,
    LoreRelationResponse,
    LoreRelationStateInput,
    LoreRelationUpdate,
    LoreRelationVersionSummary,
    LoreRelationVersionsResponse,
    LoreRepositoryCapabilities,
    LoreRepositoryOverview,
    LoreSourceSummary,
    LoreSourcesResponse,
    LoreTypeSummary,
    LoreTypeCreate,
    LoreTypeResponse,
    LoreTypesResponse,
    LoreVersionConflictDetail,
    LoreVersionSummary,
    LoreVersionsResponse,
)

router = APIRouter(prefix="/api/projects/{project_id}/lore", tags=["lore"])

_CURSOR_VERSION = 1
_CURSOR_SECRET = (settings.JWT_SECRET or "development-lore-cursor-secret").encode()
_CREATE_FINGERPRINT_VERSION = "lore-element-create:v1"

_CONFIRMATION_LABELS = {
    "candidate": "待确认",
    "confirmed": "已确认",
    "rejected": "已拒绝",
}
_SOURCE_KIND_LABELS = {
    "manual": "手动创建",
    "manual_review": "人工复核",
    "document_import": "文档导入",
    "system_extract": "AI 提取",
    "migration": "旧数据迁移",
    "legacy_import": "旧数据导入",
}
_LIFECYCLE_LABELS = {
    "active": "活动",
    "archived": "已归档",
    "merged": "已合并",
}


def _source_kind_label(kind: str | None) -> str:
    if not kind:
        return "未记录来源"
    return _SOURCE_KIND_LABELS.get(kind, "其他来源")


def _cursor_signature(payload: bytes) -> str:
    return hmac.new(_CURSOR_SECRET, payload, hashlib.sha256).hexdigest()


def _encode_cursor(data: dict[str, Any]) -> str:
    payload = json.dumps(
        data,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    token = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{token}.{_cursor_signature(payload)}"


def _decode_cursor(value: str) -> dict[str, Any]:
    try:
        token, signature = value.split(".", 1)
        padded = token + "=" * (-len(token) % 4)
        payload = base64.urlsafe_b64decode(padded.encode())
        if not hmac.compare_digest(signature, _cursor_signature(payload)):
            raise ValueError("signature")
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("shape")
        if data.get("v") != _CURSOR_VERSION:
            raise ValueError("version")
        return data
    except (
        ValueError,
        TypeError,
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise HTTPException(status_code=400, detail="分页游标无效") from exc


def _filter_signature(
    q: str | None,
    type_key: str | None,
    confirmation_status: str | None,
    source_kind: str | None,
    lifecycle_status: str | None = None,
    enabled: bool | None = None,
    has_relation: bool | None = None,
) -> str:
    payload = json.dumps(
        {
            "q": normalize_lore_name(q or ""),
            "type": type_key or "",
            "confirmation": confirmation_status or "",
            "source": source_kind or "",
            "lifecycle": lifecycle_status or "",
            "enabled": enabled,
            "has_relation": has_relation,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


async def _load_projection(
    project_id: str,
    db: AsyncSession,
    current_user: User,
) -> tuple[Any, LoreProjection]:
    project = await get_project_for_owner(project_id, current_user, db)
    worldview = (
        await db.execute(select(Worldview).where(Worldview.project_id == project_id))
    ).scalar_one_or_none()
    return project, project_legacy_worldview(project_id, worldview)


def _migration_status(project: Any, projection: LoreProjection) -> LoreMigrationStatus:
    validation = validate_projection(projection)
    if not projection.elements:
        state = "not_started"
    elif validation["valid"]:
        state = "ready"
    else:
        state = "failed"
    return LoreMigrationStatus(
        storage_mode=project.lore_storage_mode or "legacy",
        state=state,
        read_only=True,
        processed_count=len(projection.elements),
        total_count=len(projection.elements),
        error_category=None if validation["valid"] else "legacy_validation",
        can_retry=False,
    )


def _relational_migration_status() -> LoreMigrationStatus:
    return LoreMigrationStatus(
        storage_mode="relational",
        state="ready",
        read_only=False,
        can_retry=False,
    )


async def _list_relational_elements(
    project: Any,
    project_id: str,
    db: AsyncSession,
    cursor: str | None,
    limit: int,
    q: str | None,
    type_key: str | None,
    confirmation_status: str | None,
    source_kind: str | None,
    lifecycle_status: str | None,
    enabled: bool | None,
    has_relation: bool | None,
) -> LoreListResponse:
    filter_sig = _filter_signature(
        q,
        type_key,
        confirmation_status,
        source_kind,
        lifecycle_status,
        enabled,
        has_relation,
    )
    filters = [SettingElement.project_id == project_id]
    normalized_query = normalize_lore_name(q or "")
    if normalized_query:
        filters.append(
            or_(
                SettingElement.normalized_name.contains(normalized_query),
                func.lower(SettingElement.summary).contains(normalized_query),
            )
        )
    if type_key:
        filters.append(SettingType.key == type_key)
    if confirmation_status:
        filters.append(SettingElement.confirmation_status == confirmation_status)
    if lifecycle_status:
        filters.append(SettingElement.lifecycle_status == lifecycle_status)
    if enabled is not None:
        filters.append(SettingElement.enabled == enabled)
    active_relation_exists = (
        select(ElementRelation.id)
        .where(
            ElementRelation.project_id == project_id,
            ElementRelation.status == "active",
            or_(
                ElementRelation.source_element_id == SettingElement.id,
                ElementRelation.target_element_id == SettingElement.id,
            ),
        )
        .correlate(SettingElement)
        .exists()
    )
    if has_relation is True:
        filters.append(active_relation_exists)
    elif has_relation is False:
        filters.append(~active_relation_exists)
    if source_kind:
        filters.append(
            select(ElementSource.id)
            .where(
                ElementSource.project_id == project_id,
                ElementSource.element_id == SettingElement.id,
                ElementSource.source_kind == source_kind,
            )
            .correlate(SettingElement)
            .exists()
        )

    page_filters = list(filters)
    if cursor:
        cursor_data = _decode_cursor(cursor)
        if (
            cursor_data.get("kind") != "relational_elements"
            or cursor_data.get("project_id") != project_id
            or cursor_data.get("filters") != filter_sig
        ):
            raise HTTPException(status_code=400, detail="分页游标与当前查询不匹配")
        after = cursor_data.get("after")
        if (
            not isinstance(after, list)
            or len(after) != 2
            or not all(isinstance(value, str) for value in after)
        ):
            raise HTTPException(status_code=400, detail="分页游标无效")
        page_filters.append(
            or_(
                SettingElement.normalized_name > after[0],
                (
                    (SettingElement.normalized_name == after[0])
                    & (SettingElement.id > after[1])
                ),
            )
        )

    total = await db.scalar(
        select(func.count())
        .select_from(SettingElement)
        .join(SettingType, SettingType.id == SettingElement.type_id)
        .where(*filters)
    )
    result = await db.execute(
        select(SettingElement, SettingType)
        .join(SettingType, SettingType.id == SettingElement.type_id)
        .where(*page_filters)
        .order_by(SettingElement.normalized_name.asc(), SettingElement.id.asc())
        .limit(limit + 1)
    )
    rows = list(result.all())
    has_more = len(rows) > limit
    page = rows[:limit]
    element_ids = [element.id for element, _setting_type in page]

    source_labels: dict[str, str] = {}
    relation_counts: Counter[str] = Counter()
    if element_ids:
        source_rows = await db.execute(
            select(
                ElementSource.element_id,
                ElementSource.source_kind,
                ElementSource.is_primary,
                ElementSource.created_at,
            )
            .where(
                ElementSource.project_id == project_id,
                ElementSource.element_id.in_(element_ids),
            )
            .order_by(ElementSource.is_primary.desc(), ElementSource.created_at.asc())
        )
        for element_id_value, kind, _is_primary, _created_at in source_rows.all():
            source_labels.setdefault(element_id_value, kind)
        relation_rows = await db.execute(
            select(
                ElementRelation.source_element_id,
                ElementRelation.target_element_id,
            ).where(
                ElementRelation.project_id == project_id,
                ElementRelation.status == "active",
                or_(
                    ElementRelation.source_element_id.in_(element_ids),
                    ElementRelation.target_element_id.in_(element_ids),
                ),
            )
        )
        for source_id, target_id in relation_rows.all():
            if source_id in element_ids:
                relation_counts[source_id] += 1
            if target_id in element_ids:
                relation_counts[target_id] += 1

    items = [
        LoreElementListItem(
            id=element.id,
            type=LoreTypeSummary(
                key=setting_type.key,
                display_name=setting_type.display_name,
            ),
            name=element.name,
            summary=element.summary or "",
            confirmation_status=element.confirmation_status,
            lifecycle_status=element.lifecycle_status,
            enabled=element.enabled,
            generation_eligible=generation_eligible(element),
            source_summary=_source_kind_label(source_labels.get(element.id)),
            current_version=element.content_version,
            revision=element.payload_schema_revision,
            lock_version=element.lock_version,
            updated_at=element.updated_at,
            relation_count=relation_counts[element.id],
            binding_count=0,
        )
        for element, setting_type in page
    ]
    next_cursor = None
    if has_more and page:
        last_element = page[-1][0]
        next_cursor = _encode_cursor(
            {
                "v": _CURSOR_VERSION,
                "kind": "relational_elements",
                "project_id": project_id,
                "filters": filter_sig,
                "after": [last_element.normalized_name, last_element.id],
            }
        )

    type_rows = await db.execute(
        select(SettingType.key, SettingType.display_name, func.count())
        .select_from(SettingElement)
        .join(SettingType, SettingType.id == SettingElement.type_id)
        .where(*filters)
        .group_by(SettingType.key, SettingType.display_name)
        .order_by(SettingType.key)
    )
    confirmation_rows = await db.execute(
        select(SettingElement.confirmation_status, func.count())
        .select_from(SettingElement)
        .join(SettingType, SettingType.id == SettingElement.type_id)
        .where(*filters)
        .group_by(SettingElement.confirmation_status)
    )
    source_facet_rows = await db.execute(
        select(
            ElementSource.source_kind,
            func.count(func.distinct(ElementSource.element_id)),
        )
        .select_from(ElementSource)
        .join(
            SettingElement,
            (SettingElement.id == ElementSource.element_id)
            & (SettingElement.project_id == ElementSource.project_id),
        )
        .join(SettingType, SettingType.id == SettingElement.type_id)
        .where(ElementSource.project_id == project_id, *filters)
        .group_by(ElementSource.source_kind)
        .order_by(ElementSource.source_kind)
    )
    lifecycle_rows = await db.execute(
        select(SettingElement.lifecycle_status, func.count())
        .select_from(SettingElement)
        .join(SettingType, SettingType.id == SettingElement.type_id)
        .where(*filters)
        .group_by(SettingElement.lifecycle_status)
    )
    enabled_rows = await db.execute(
        select(SettingElement.enabled, func.count())
        .select_from(SettingElement)
        .join(SettingType, SettingType.id == SettingElement.type_id)
        .where(*filters)
        .group_by(SettingElement.enabled)
    )
    related_count = await db.scalar(
        select(func.count())
        .select_from(SettingElement)
        .join(SettingType, SettingType.id == SettingElement.type_id)
        .where(*filters, active_relation_exists)
    )
    unrelated_count = await db.scalar(
        select(func.count())
        .select_from(SettingElement)
        .join(SettingType, SettingType.id == SettingElement.type_id)
        .where(*filters, ~active_relation_exists)
    )
    return LoreListResponse(
        items=items,
        next_cursor=next_cursor,
        has_more=has_more,
        total=total or 0,
        facets=LoreFacets(
            types=[
                LoreFacetCount(key=key, label=label, count=count)
                for key, label, count in type_rows.all()
            ],
            confirmation_statuses=[
                LoreFacetCount(
                    key=key,
                    label=_CONFIRMATION_LABELS.get(key, key),
                    count=count,
                )
                for key, count in confirmation_rows.all()
            ],
            sources=[
                LoreFacetCount(
                    key=key,
                    label=_source_kind_label(key),
                    count=count,
                )
                for key, count in source_facet_rows.all()
            ],
            lifecycle_statuses=[
                LoreFacetCount(
                    key=key,
                    label=_LIFECYCLE_LABELS.get(key, key),
                    count=count,
                )
                for key, count in lifecycle_rows.all()
            ],
            enabled_statuses=[
                LoreFacetCount(
                    key="enabled" if key else "disabled",
                    label="已启用" if key else "已停用",
                    count=count,
                )
                for key, count in enabled_rows.all()
            ],
            relation_statuses=[
                LoreFacetCount(
                    key="with_relations",
                    label="有关联",
                    count=int(related_count or 0),
                ),
                LoreFacetCount(
                    key="without_relations",
                    label="无关联",
                    count=int(unrelated_count or 0),
                ),
            ],
        ),
        migration_status=_relational_migration_status(),
    )


def _list_item(element: ProjectedLoreElement) -> LoreElementListItem:
    return LoreElementListItem(
        id=element.id,
        type=LoreTypeSummary(
            key=element.type_key,
            display_name=element.type_display_name,
        ),
        name=element.name,
        summary=element.summary,
        confirmation_status="confirmed",
        lifecycle_status="active",
        source_summary=element.source_label,
        current_version=1,
        revision=1,
        updated_at=element.updated_at,
        relation_count=0,
        binding_count=0,
    )


def _source(element: ProjectedLoreElement) -> LoreSourceSummary:
    return LoreSourceSummary(
        kind=element.source_kind,
        label=element.source_label,
        is_primary=True,
        created_at=element.created_at,
    )


def _find_element(
    projection: LoreProjection,
    element_id: str,
) -> ProjectedLoreElement:
    for element in projection.elements:
        if element.id == element_id:
            return element
    raise HTTPException(status_code=404, detail="设定不存在")


@router.get("/elements", response_model=LoreListResponse)
async def list_lore_elements(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    q: Annotated[str | None, Query(max_length=200)] = None,
    type_key: Annotated[
        str | None,
        Query(alias="type", max_length=50),
    ] = None,
    confirmation_status: str | None = None,
    source_kind: str | None = None,
    lifecycle_status: Annotated[
        str | None,
        Query(pattern="^(active|archived|merged)$"),
    ] = None,
    enabled: bool | None = None,
    has_relation: bool | None = None,
):
    project = await get_project_for_owner(project_id, current_user, db)
    if project.lore_storage_mode == "relational":
        return await _list_relational_elements(
            project,
            project_id,
            db,
            cursor,
            limit,
            q,
            type_key,
            confirmation_status,
            source_kind,
            lifecycle_status,
            enabled,
            has_relation,
        )
    worldview = await db.scalar(
        select(Worldview).where(Worldview.project_id == project_id)
    )
    projection = project_legacy_worldview(project_id, worldview)
    filter_sig = _filter_signature(
        q,
        type_key,
        confirmation_status,
        source_kind,
        lifecycle_status,
        enabled,
        has_relation,
    )
    elements = projection.elements
    normalized_query = normalize_lore_name(q or "")
    if normalized_query:
        elements = [
            element
            for element in elements
            if normalized_query in normalize_lore_name(element.name)
            or normalized_query in normalize_lore_name(element.summary)
            or normalized_query
            in normalize_lore_name(
                json.dumps(element.payload, ensure_ascii=False, sort_keys=True)
            )
        ]
    if type_key:
        elements = [element for element in elements if element.type_key == type_key]
    if confirmation_status and confirmation_status != "confirmed":
        elements = []
    if source_kind:
        elements = [
            element for element in elements if element.source_kind == source_kind
        ]
    if lifecycle_status and lifecycle_status != "active":
        elements = []
    if enabled is False:
        elements = []
    if has_relation is True:
        elements = []

    elements = sorted(
        elements,
        key=lambda element: (normalize_lore_name(element.name), element.id),
    )
    start = 0
    if cursor:
        cursor_data = _decode_cursor(cursor)
        if cursor_data.get("project_id") != project_id:
            raise HTTPException(status_code=400, detail="分页游标不属于当前项目")
        if cursor_data.get("checksum") != projection.checksum:
            raise HTTPException(status_code=409, detail="设定数据已更新，请重新加载列表")
        if cursor_data.get("filters") != filter_sig:
            raise HTTPException(status_code=400, detail="分页游标与当前筛选条件不匹配")
        after_values = cursor_data.get("after", [])
        if (
            not isinstance(after_values, list)
            or len(after_values) != 2
            or not all(isinstance(item, str) for item in after_values)
        ):
            raise HTTPException(status_code=400, detail="分页游标无效")
        after = tuple(after_values)
        while start < len(elements):
            key = (normalize_lore_name(elements[start].name), elements[start].id)
            if key > after:
                break
            start += 1

    page = elements[start : start + limit]
    has_more = start + limit < len(elements)
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor(
            {
                "v": _CURSOR_VERSION,
                "project_id": project_id,
                "checksum": projection.checksum,
                "filters": filter_sig,
                "after": [normalize_lore_name(last.name), last.id],
            }
        )

    type_counts = Counter(element.type_key for element in elements)
    source_counts = Counter(element.source_kind for element in elements)
    return LoreListResponse(
        items=[_list_item(element) for element in page],
        next_cursor=next_cursor,
        has_more=has_more,
        total=len(elements),
        facets=LoreFacets(
            types=[
                LoreFacetCount(
                    key=key,
                    label=next(
                        element.type_display_name
                        for element in elements
                        if element.type_key == key
                    ),
                    count=count,
                )
                for key, count in sorted(type_counts.items())
            ],
            confirmation_statuses=[
                LoreFacetCount(key="confirmed", label="已确认", count=len(elements))
            ]
            if elements
            else [],
            sources=[
                LoreFacetCount(
                    key=key,
                    label=next(
                        element.source_label
                        for element in elements
                        if element.source_kind == key
                    ),
                    count=count,
                )
                for key, count in sorted(source_counts.items())
            ],
            lifecycle_statuses=[
                LoreFacetCount(key="active", label="活动", count=len(elements))
            ]
            if elements
            else [],
            enabled_statuses=[
                LoreFacetCount(key="enabled", label="已启用", count=len(elements))
            ]
            if elements
            else [],
            relation_statuses=[
                LoreFacetCount(
                    key="without_relations",
                    label="无关联",
                    count=len(elements),
                )
            ]
            if elements
            else [],
        ),
        migration_status=_migration_status(project, projection),
    )


@router.get("/overview", response_model=LoreRepositoryOverview)
async def get_lore_repository_overview(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)
    pending_count = await db.scalar(
        select(func.count())
        .select_from(LoreExtractionCandidate)
        .where(
            LoreExtractionCandidate.project_id == project_id,
            LoreExtractionCandidate.status == "pending_review",
        )
    )
    attention_count = await db.scalar(
        select(func.count())
        .select_from(LoreExtractionCandidate)
        .where(
            LoreExtractionCandidate.project_id == project_id,
            LoreExtractionCandidate.status == "pending_review",
            LoreExtractionCandidate.needs_attention.is_(True),
        )
    )
    if project.lore_storage_mode == "relational":
        formal_total = await db.scalar(
            select(func.count())
            .select_from(SettingElement)
            .where(SettingElement.project_id == project_id)
        )
        confirmed_active = await db.scalar(
            select(func.count())
            .select_from(SettingElement)
            .where(
                SettingElement.project_id == project_id,
                SettingElement.confirmation_status == "confirmed",
                SettingElement.lifecycle_status == "active",
            )
        )
        disabled = await db.scalar(
            select(func.count())
            .select_from(SettingElement)
            .where(
                SettingElement.project_id == project_id,
                SettingElement.lifecycle_status == "active",
                SettingElement.enabled.is_(False),
            )
        )
        archived = await db.scalar(
            select(func.count())
            .select_from(SettingElement)
            .where(
                SettingElement.project_id == project_id,
                SettingElement.lifecycle_status == "archived",
            )
        )
        migration_status = _relational_migration_status()
    else:
        worldview = await db.scalar(
            select(Worldview).where(Worldview.project_id == project_id)
        )
        projection = project_legacy_worldview(project_id, worldview)
        formal_total = len(projection.elements)
        confirmed_active = len(projection.elements)
        disabled = 0
        archived = 0
        migration_status = _migration_status(project, projection)
    return LoreRepositoryOverview(
        formal_total=int(formal_total or 0),
        confirmed_active=int(confirmed_active or 0),
        pending_review=int(pending_count or 0),
        needs_attention=int(attention_count or 0),
        disabled=int(disabled or 0),
        archived=int(archived or 0),
        migration_status=migration_status,
        capabilities=LoreRepositoryCapabilities(
            candidate_accept=(project.lore_storage_mode == "relational"),
            formal_create=(
                project.lore_storage_mode == "relational"
                and not migration_status.read_only
            ),
        ),
        count_definitions={
            "formal_total": {"entity": "formal_lore"},
            "confirmed_active": {
                "entity": "formal_lore",
                "confirmation_status": "confirmed",
                "lifecycle_status": "active",
            },
            "pending_review": {
                "entity": "extraction_candidate",
                "status": "pending_review",
            },
            "needs_attention": {
                "entity": "extraction_candidate",
                "status": "pending_review",
                "needs_attention": True,
            },
            "disabled": {
                "entity": "formal_lore",
                "lifecycle_status": "active",
                "enabled": False,
            },
            "archived": {
                "entity": "formal_lore",
                "lifecycle_status": "archived",
            },
        },
    )


@router.get("/elements/{element_id}", response_model=LoreElementDetail)
async def get_lore_element(
    project_id: str,
    element_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)
    if project.lore_storage_mode == "relational":
        element = await db.scalar(
            select(SettingElement).where(
                SettingElement.id == element_id,
                SettingElement.project_id == project_id,
            )
        )
        if element is None:
            raise HTTPException(status_code=404, detail="设定不存在")
        return await _build_relational_element_detail(element, db)
    worldview = await db.scalar(
        select(Worldview).where(Worldview.project_id == project_id)
    )
    projection = project_legacy_worldview(project_id, worldview)
    element = _find_element(projection, element_id)
    base = _list_item(element).model_dump()
    return LoreElementDetail(
        **base,
        payload=element.payload,
        field_definitions=[
            LoreFieldDefinition(**definition)
            for definition in type_field_definitions(element.type_key)
        ],
        sources=[_source(element)],
        created_at=element.created_at,
        version_count=1,
        merged_to=None,
        redirected_from=None,
        read_only=True,
        migration_status=_migration_status(project, projection),
    )


@router.get(
    "/elements/{element_id}/sources",
    response_model=LoreSourcesResponse,
)
async def list_lore_sources(
    project_id: str,
    element_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)
    if project.lore_storage_mode == "relational":
        element = await db.scalar(
            select(SettingElement.id).where(
                SettingElement.id == element_id,
                SettingElement.project_id == project_id,
            )
        )
        if element is None:
            raise HTTPException(status_code=404, detail="设定不存在")
        result = await db.execute(
            select(ElementSource)
            .where(
                ElementSource.project_id == project_id,
                ElementSource.element_id == element_id,
            )
            .order_by(ElementSource.is_primary.desc(), ElementSource.created_at.asc())
        )
        sources = list(result.scalars().all())
        return LoreSourcesResponse(
            items=[
                LoreSourceSummary(
                    id=source.id,
                    kind=source.source_kind,
                    label=source.source_kind,
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
            total=len(sources),
            read_only=False,
        )
    worldview = await db.scalar(
        select(Worldview).where(Worldview.project_id == project_id)
    )
    projection = project_legacy_worldview(project_id, worldview)
    element = _find_element(projection, element_id)
    return LoreSourcesResponse(items=[_source(element)], total=1, read_only=True)


@router.get(
    "/elements/{element_id}/versions",
    response_model=LoreVersionsResponse,
)
async def list_lore_versions(
    project_id: str,
    element_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)
    if project.lore_storage_mode == "relational":
        element = await db.scalar(
            select(SettingElement.id).where(
                SettingElement.id == element_id,
                SettingElement.project_id == project_id,
            )
        )
        if element is None:
            raise HTTPException(status_code=404, detail="设定不存在")
        result = await db.execute(
            select(ElementVersion, SettingType)
            .join(SettingType, SettingType.id == ElementVersion.type_id)
            .where(ElementVersion.element_id == element_id)
            .order_by(ElementVersion.version_no.desc())
        )
        rows = list(result.all())
        return LoreVersionsResponse(
            items=[
                LoreVersionSummary(
                    version_no=version.version_no,
                    name=version.name,
                    summary=version.summary or "",
                    payload=version.payload or {},
                    field_states=version.field_states or {},
                    type_schema_revision=version.type_schema_revision,
                    type=LoreTypeSummary(
                        key=setting_type.key,
                        display_name=setting_type.display_name,
                    ),
                    created_at=version.created_at,
                    change_reason=version.change_reason or "",
                    created_by=version.created_by,
                    read_only=False,
                )
                for version, setting_type in rows
            ],
            total=len(rows),
            read_only=False,
        )
    worldview = await db.scalar(
        select(Worldview).where(Worldview.project_id == project_id)
    )
    projection = project_legacy_worldview(project_id, worldview)
    element = _find_element(projection, element_id)
    return LoreVersionsResponse(
        items=[
            LoreVersionSummary(
                version_no=1,
                name=element.name,
                summary=element.summary,
                payload=element.payload,
                type=LoreTypeSummary(
                    key=element.type_key,
                    display_name=element.type_display_name,
                ),
                created_at=element.created_at,
                change_reason="旧版世界观只读投影",
                read_only=True,
            )
        ],
        total=1,
        read_only=True,
    )


@router.get(
    "/elements/{element_id}/versions/{version_no}",
    response_model=LoreVersionSummary,
)
async def get_lore_version(
    project_id: str,
    element_id: str,
    version_no: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)
    if project.lore_storage_mode == "relational":
        result = await db.execute(
            select(ElementVersion, SettingType)
            .join(SettingType, SettingType.id == ElementVersion.type_id)
            .join(SettingElement, SettingElement.id == ElementVersion.element_id)
            .where(
                SettingElement.project_id == project_id,
                ElementVersion.element_id == element_id,
                ElementVersion.version_no == version_no,
            )
        )
        row = result.one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="该版本不存在")
        version, setting_type = row
        return LoreVersionSummary(
            version_no=version.version_no,
            name=version.name,
            summary=version.summary or "",
            payload=version.payload or {},
            field_states=version.field_states or {},
            type_schema_revision=version.type_schema_revision,
            type=LoreTypeSummary(
                key=setting_type.key,
                display_name=setting_type.display_name,
            ),
            created_at=version.created_at,
            change_reason=version.change_reason or "",
            created_by=version.created_by,
            read_only=False,
        )
    if version_no != 1:
        raise HTTPException(status_code=404, detail="该版本不存在")
    worldview = await db.scalar(
        select(Worldview).where(Worldview.project_id == project_id)
    )
    projection = project_legacy_worldview(project_id, worldview)
    element = _find_element(projection, element_id)
    return LoreVersionSummary(
        version_no=1,
        name=element.name,
        summary=element.summary,
        payload=element.payload,
        type=LoreTypeSummary(
            key=element.type_key,
            display_name=element.type_display_name,
        ),
        created_at=element.created_at,
        change_reason="旧版世界观只读投影",
        read_only=True,
    )


# ─── Write helpers ────────────────────────────────────────────────


_LORE_MODE_CONFLICT_CODE = "LORE_MODE_NOT_RELATIONAL"


def _require_relational_mode(project: Any) -> None:
    """Fail-closed: only relational projects may write to relational tables."""
    if (project.lore_storage_mode or "legacy") != "relational":
        raise HTTPException(
            status_code=409,
            detail={
                "code": _LORE_MODE_CONFLICT_CODE,
                "message": "项目尚未切换到关系存储模式，无法写入设定",
                "current_mode": project.lore_storage_mode or "legacy",
            },
        )


async def _load_relational_element(
    project_id: str,
    element_id: str,
    db: AsyncSession,
    current_user: User,
) -> SettingElement:
    """Load a relational SettingElement with project ownership verified.

    Uses SELECT … FOR UPDATE so the row is locked until commit, preventing
    concurrent updates from racing past the optimistic-lock check.
    """
    project = await get_project_for_owner(project_id, current_user, db)
    _require_relational_mode(project)
    result = await db.execute(
        select(SettingElement)
        .where(
            SettingElement.id == element_id,
            SettingElement.project_id == project_id,
        )
        .with_for_update()
    )
    element = result.scalar_one_or_none()
    if element is None:
        raise HTTPException(status_code=404, detail="设定不存在")
    return element


async def _load_relational_read_element(
    project_id: str,
    element_id: str,
    db: AsyncSession,
    current_user: User,
) -> SettingElement:
    project = await get_project_for_owner(project_id, current_user, db)
    _require_relational_mode(project)
    element = await db.scalar(
        select(SettingElement).where(
            SettingElement.id == element_id,
            SettingElement.project_id == project_id,
        )
    )
    if element is None:
        raise HTTPException(status_code=404, detail="设定不存在")
    return element


async def _build_element_response(
    element: SettingElement,
    db: AsyncSession,
) -> LoreElementResponse:
    """Build the minimal write response for an element."""
    setting_type = await db.scalar(
        select(SettingType).where(SettingType.id == element.type_id)
    )

    source_result = await db.execute(
        select(ElementSource).where(
            ElementSource.element_id == element.id,
            ElementSource.project_id == element.project_id,
        ).order_by(ElementSource.is_primary.desc(), ElementSource.created_at.asc())
    )
    sources = source_result.scalars().all()

    relation_count_result = await db.scalar(
        select(func.count()).select_from(ElementRelation).where(
            ElementRelation.project_id == element.project_id,
            ElementRelation.status == "active",
            or_(
                ElementRelation.source_element_id == element.id,
                ElementRelation.target_element_id == element.id,
            ),
        )
    )

    return LoreElementResponse(
        id=element.id,
        type=LoreTypeSummary(
            key=setting_type.key if setting_type else "unknown",
            display_name=setting_type.display_name if setting_type else "未知",
        ),
        name=element.name,
        summary=element.summary or "",
        confirmation_status=element.confirmation_status,
        lifecycle_status=element.lifecycle_status,
        enabled=element.enabled,
        generation_eligible=generation_eligible(element),
        lock_version=element.lock_version,
        content_version=element.content_version,
        payload_schema_revision=element.payload_schema_revision,
        payload=element.payload or {},
        field_states=element.field_states or {},
        field_definitions=[
            LoreFieldDefinition(**f)
            for f in (
                field_schema_for_type(setting_type)
                if setting_type
                else []
            )
        ],
        sources=[
            LoreSourceSummary(
                id=src.id,
                kind=src.source_kind,
                label=src.source_kind,
                is_primary=src.is_primary,
                created_at=src.created_at,
                reference=src.source_ref,
                locator=src.locator or {},
                excerpt=src.excerpt,
                excerpt_hash=src.excerpt_hash,
                confirmation_status=src.confirmation_status,
            )
            for src in sources
        ],
        relation_count=relation_count_result or 0,
        binding_count=0,
        created_at=element.created_at,
        updated_at=element.updated_at,
    )


async def _build_create_response(
    element: SettingElement,
    db: AsyncSession,
    *,
    replayed: bool,
) -> LoreElementCreateResponse:
    response = await _build_element_response(element, db)
    return LoreElementCreateResponse(
        **response.model_dump(),
        replayed=replayed,
    )


def _create_request_fingerprint(body: LoreElementCreate) -> str:
    payload = body.model_dump(mode="json", exclude={"operation_key"})
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "LORE_CREATE_PAYLOAD_INVALID",
                "message": "设定内容无法生成稳定请求指纹",
            },
        ) from exc
    return hashlib.sha256(
        f"{_CREATE_FINGERPRINT_VERSION}\n{canonical}".encode("utf-8")
    ).hexdigest()


async def _find_create_operation(
    project_id: str,
    user_id: str,
    operation_key: str,
    db: AsyncSession,
) -> LoreElementCreateOperation | None:
    return await db.scalar(
        select(LoreElementCreateOperation).where(
            LoreElementCreateOperation.project_id == project_id,
            LoreElementCreateOperation.requested_by == user_id,
            LoreElementCreateOperation.operation_key == operation_key,
        )
    )


async def _replay_create_operation(
    operation: LoreElementCreateOperation,
    request_fingerprint: str,
    db: AsyncSession,
) -> LoreElementCreateResponse:
    if operation.request_fingerprint != request_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LORE_CREATE_IDEMPOTENCY_CONFLICT",
                "message": "这次创建与先前请求内容不一致，为避免重复，系统没有再次创建",
                "retryable": False,
            },
        )
    element = await db.scalar(
        select(SettingElement).where(
            SettingElement.project_id == operation.project_id,
            SettingElement.id == operation.element_id,
        )
    )
    if element is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LORE_CREATE_OPERATION_CORRUPT",
                "message": "创建记录与设定不一致，已停止自动处理",
                "retryable": False,
            },
        )
    return await _build_create_response(element, db, replayed=True)


async def _build_relational_element_detail(
    element: SettingElement,
    db: AsyncSession,
) -> LoreElementDetail:
    response = await _build_element_response(element, db)
    version_count = await db.scalar(
        select(func.count()).select_from(ElementVersion).where(
            ElementVersion.element_id == element.id
        )
    )
    return LoreElementDetail(
        id=response.id,
        type=response.type,
        name=response.name,
        summary=response.summary,
        confirmation_status=response.confirmation_status,
        lifecycle_status=response.lifecycle_status,
        enabled=response.enabled,
        generation_eligible=response.generation_eligible,
        source_summary=(response.sources[0].kind if response.sources else "未记录来源"),
        current_version=response.content_version,
        revision=response.payload_schema_revision,
        lock_version=response.lock_version,
        updated_at=response.updated_at,
        relation_count=response.relation_count,
        binding_count=response.binding_count,
        payload=response.payload,
        field_states=response.field_states,
        payload_schema_revision=response.payload_schema_revision,
        field_definitions=response.field_definitions,
        sources=response.sources,
        created_at=response.created_at,
        version_count=version_count or 0,
        merged_to=None,
        redirected_from=None,
        read_only=False,
        migration_status=_relational_migration_status(),
    )


def _stale_version_response(exc: LoreStaleVersionError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=LoreVersionConflictDetail(
            current_lock_version=exc.current_lock_version,
            updated_at=exc.updated_at,
        ).model_dump(mode="json"),
    )


def _build_type_response(setting_type: SettingType) -> LoreTypeResponse:
    return LoreTypeResponse(
        id=setting_type.id,
        key=setting_type.key,
        display_name=setting_type.display_name,
        description=setting_type.description or "",
        is_builtin=setting_type.is_builtin,
        schema_revision=setting_type.schema_revision,
        field_schema=[
            LoreFieldDefinition(**field)
            for field in field_schema_for_type(setting_type)
        ],
        status=setting_type.status,
        created_at=setting_type.created_at,
        updated_at=setting_type.updated_at,
    )


def _build_virtual_builtin_type_response(type_key: str) -> LoreTypeResponse:
    epoch = datetime(1970, 1, 1)
    return LoreTypeResponse(
        id=f"builtin:{type_key}",
        key=type_key,
        display_name=TYPE_DISPLAY_NAMES[type_key],
        description="平台内建设定类型",
        is_builtin=True,
        schema_revision=1,
        field_schema=[
            LoreFieldDefinition(**field)
            for field in type_field_definitions(type_key)
        ],
        status="active",
        created_at=epoch,
        updated_at=epoch,
    )


async def _load_relational_relation(
    project_id: str,
    relation_id: str,
    db: AsyncSession,
    current_user: User,
    *,
    for_update: bool = True,
) -> ElementRelation:
    project = await get_project_for_owner(project_id, current_user, db)
    _require_relational_mode(project)
    statement = (
        select(ElementRelation)
        .where(
            ElementRelation.id == relation_id,
            ElementRelation.project_id == project_id,
        )
    )
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    relation = result.scalar_one_or_none()
    if relation is None:
        raise HTTPException(status_code=404, detail="关系不存在")
    return relation


async def _lock_relation_endpoints(
    relation: ElementRelation,
    db: AsyncSession,
) -> None:
    """Lock relation endpoints before the relation using a stable global order.

    Element archival already locks an endpoint before its incident relations.
    Relation state changes must use the same order, otherwise a concurrent
    restore can deadlock with archival and surface an avoidable HTTP 500.
    """
    await db.execute(
        select(SettingElement.id)
        .where(
            SettingElement.project_id == relation.project_id,
            SettingElement.id.in_(
                [relation.source_element_id, relation.target_element_id]
            ),
        )
        .order_by(SettingElement.id.asc())
        .with_for_update()
    )


async def _build_relation_response(
    relation: ElementRelation,
    db: AsyncSession,
) -> LoreRelationResponse:
    rows = await db.execute(
        select(SettingElement, SettingType)
        .join(SettingType, SettingType.id == SettingElement.type_id)
        .where(
            SettingElement.project_id == relation.project_id,
            SettingType.project_id == relation.project_id,
            SettingElement.id.in_(
                [relation.source_element_id, relation.target_element_id]
            ),
        )
    )
    endpoints = {element.id: (element, setting_type) for element, setting_type in rows}
    if (
        relation.source_element_id not in endpoints
        or relation.target_element_id not in endpoints
    ):
        raise HTTPException(status_code=409, detail="关系引用的设定不存在")
    source, source_type = endpoints[relation.source_element_id]
    target, target_type = endpoints[relation.target_element_id]
    return LoreRelationResponse(
        id=relation.id,
        source=LoreRelationEndpoint(
            id=source.id,
            name=source.name,
            type=LoreTypeSummary(
                key=source_type.key,
                display_name=source_type.display_name,
            ),
            summary=source.summary or "",
            lifecycle_status=source.lifecycle_status,
            enabled=source.enabled,
        ),
        target=LoreRelationEndpoint(
            id=target.id,
            name=target.name,
            type=LoreTypeSummary(
                key=target_type.key,
                display_name=target_type.display_name,
            ),
            summary=target.summary or "",
            lifecycle_status=target.lifecycle_status,
            enabled=target.enabled,
        ),
        relation_key=relation.relation_key,
        forward_label=relation.forward_label,
        reverse_label=relation.reverse_label,
        description=relation.description or "",
        metadata=relation.metadata_ or {},
        status=relation.status,
        version_no=relation.version_no,
        lock_version=relation.lock_version,
        created_at=relation.created_at,
        updated_at=relation.updated_at,
    )


# ─── Write endpoints ──────────────────────────────────────────────


@router.get("/types", response_model=LoreTypesResponse)
async def list_lore_types(
    project_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)
    _require_relational_mode(project)
    result = await db.execute(
        select(SettingType)
        .where(SettingType.project_id == project_id)
        .order_by(SettingType.is_builtin.desc(), SettingType.display_name.asc())
    )
    items = list(result.scalars().all())
    by_key = {item.key: _build_type_response(item) for item in items}
    builtin_items = [
        by_key.pop(type_key, None) or _build_virtual_builtin_type_response(type_key)
        for type_key in TYPE_DISPLAY_NAMES
    ]
    custom_items = sorted(
        by_key.values(),
        key=lambda item: (item.display_name, item.key),
    )
    response_items = [*builtin_items, *custom_items]
    return LoreTypesResponse(
        items=response_items,
        total=len(response_items),
    )


@router.post(
    "/types",
    response_model=LoreTypeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_lore_type(
    project_id: str,
    body: LoreTypeCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)
    _require_relational_mode(project)
    try:
        setting_type = await create_custom_type(
            db=db,
            project_id=project_id,
            key=body.key,
            display_name=body.display_name,
            description=body.description,
            field_schema=[field.model_dump() for field in body.field_schema],
        )
        await db.commit()
        await db.refresh(setting_type)
        return _build_type_response(setting_type)
    except LoreWriteError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LORE_TYPE_CONFLICT",
                "message": "类型保存冲突，请重新加载后重试",
            },
        ) from exc


@router.post(
    "/elements",
    response_model=LoreElementCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_lore_element(
    project_id: str,
    body: LoreElementCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)
    _require_relational_mode(project)
    user_id = current_user.id
    request_fingerprint = _create_request_fingerprint(body)
    existing_operation = await _find_create_operation(
        project_id,
        user_id,
        body.operation_key,
        db,
    )
    if existing_operation is not None:
        return await _replay_create_operation(
            existing_operation,
            request_fingerprint,
            db,
        )
    try:
        check_writes_available()
        element = await create_element(
            db=db,
            project_id=project_id,
            user_id=user_id,
            type_key=body.type_key,
            name=body.name,
            summary=body.summary,
            payload=body.payload,
            field_states=body.field_states,
            sources_input=[s.model_dump() for s in body.sources],
        )
        db.add(
            LoreElementCreateOperation(
                project_id=project_id,
                requested_by=user_id,
                operation_key=body.operation_key,
                request_fingerprint=request_fingerprint,
                element_id=element.id,
            )
        )
        await db.flush()
        check_writes_available()
        await db.commit()
        await db.refresh(element)
        return await _build_create_response(element, db, replayed=False)
    except LoreStaleVersionError as exc:
        await db.rollback()
        raise _stale_version_response(exc)
    except LoreWriteError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except IntegrityError as exc:
        await db.rollback()
        existing_operation = await _find_create_operation(
            project_id,
            user_id,
            body.operation_key,
            db,
        )
        if existing_operation is not None:
            return await _replay_create_operation(
                existing_operation,
                request_fingerprint,
                db,
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LORE_ELEMENT_CONFLICT",
                "message": "设定保存发生并发冲突，请安全重试",
                "retryable": True,
            },
        ) from exc


@router.patch("/elements/{element_id}", response_model=LoreElementResponse)
async def update_lore_element(
    project_id: str,
    element_id: str,
    body: LoreElementUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    element = await _load_relational_element(project_id, element_id, db, current_user)
    try:
        await update_element_content(
            db=db,
            element=element,
            user_id=current_user.id,
            expected_version=body.expected_version,
            name=body.name,
            summary=body.summary,
            payload=body.payload,
            field_states=body.field_states,
        )
        await db.commit()
        await db.refresh(element)
        return await _build_element_response(element, db)
    except LoreStaleVersionError as exc:
        await db.rollback()
        raise _stale_version_response(exc)
    except LoreWriteError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/elements/{element_id}/confirm", response_model=LoreElementResponse)
async def confirm_lore_element(
    project_id: str,
    element_id: str,
    body: LoreElementStateInput,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    element = await _load_relational_element(project_id, element_id, db, current_user)
    try:
        await change_element_state(
            db=db,
            element=element,
            user_id=current_user.id,
            expected_version=body.expected_version,
            event_kind="confirm",
            reason=body.reason,
        )
        await db.commit()
        await db.refresh(element)
        return await _build_element_response(element, db)
    except LoreStaleVersionError as exc:
        await db.rollback()
        raise _stale_version_response(exc)
    except LoreWriteError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/elements/{element_id}/reject", response_model=LoreElementResponse)
async def reject_lore_element(
    project_id: str,
    element_id: str,
    body: LoreElementStateInput,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    element = await _load_relational_element(project_id, element_id, db, current_user)
    try:
        await change_element_state(
            db=db,
            element=element,
            user_id=current_user.id,
            expected_version=body.expected_version,
            event_kind="reject",
            reason=body.reason,
        )
        await db.commit()
        await db.refresh(element)
        return await _build_element_response(element, db)
    except LoreStaleVersionError as exc:
        await db.rollback()
        raise _stale_version_response(exc)
    except LoreWriteError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/elements/{element_id}/enable", response_model=LoreElementResponse)
async def enable_lore_element(
    project_id: str,
    element_id: str,
    body: LoreElementStateInput,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    element = await _load_relational_element(project_id, element_id, db, current_user)
    try:
        await change_element_state(
            db=db,
            element=element,
            user_id=current_user.id,
            expected_version=body.expected_version,
            event_kind="enable",
            reason=body.reason,
        )
        await db.commit()
        await db.refresh(element)
        return await _build_element_response(element, db)
    except LoreStaleVersionError as exc:
        await db.rollback()
        raise _stale_version_response(exc)
    except LoreWriteError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/elements/{element_id}/disable", response_model=LoreElementResponse)
async def disable_lore_element(
    project_id: str,
    element_id: str,
    body: LoreElementStateInput,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    element = await _load_relational_element(project_id, element_id, db, current_user)
    try:
        await change_element_state(
            db=db,
            element=element,
            user_id=current_user.id,
            expected_version=body.expected_version,
            event_kind="disable",
            reason=body.reason,
        )
        await db.commit()
        await db.refresh(element)
        return await _build_element_response(element, db)
    except LoreStaleVersionError as exc:
        await db.rollback()
        raise _stale_version_response(exc)
    except LoreWriteError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/elements/{element_id}/archive", response_model=LoreElementResponse)
async def archive_lore_element(
    project_id: str,
    element_id: str,
    body: LoreElementStateInput,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    element = await _load_relational_element(project_id, element_id, db, current_user)
    check_writes_available()
    relation_result = await db.execute(
        select(ElementRelation).where(
            ElementRelation.project_id == project_id,
            or_(
                ElementRelation.source_element_id == element.id,
                ElementRelation.target_element_id == element.id,
            ),
        ).order_by(ElementRelation.id.asc()).with_for_update()
    )
    active_relation_count = sum(
        1 for relation in relation_result.scalars().all()
        if relation.status == "active"
    )
    if active_relation_count:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LORE_ELEMENT_ACTIVE_RELATIONS",
                "message": "该设定仍有启用中的关系，请先归档相关关系",
                "active_relation_count": active_relation_count,
            },
        )
    try:
        await change_element_state(
            db=db,
            element=element,
            user_id=current_user.id,
            expected_version=body.expected_version,
            event_kind="archive",
            reason=body.reason,
        )
        await db.commit()
        await db.refresh(element)
        return await _build_element_response(element, db)
    except LoreStaleVersionError as exc:
        await db.rollback()
        raise _stale_version_response(exc)
    except LoreWriteError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post(
    "/elements/{element_id}/restore-archive",
    response_model=LoreElementResponse,
)
async def restore_archive_lore_element(
    project_id: str,
    element_id: str,
    body: LoreElementStateInput,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    element = await _load_relational_element(project_id, element_id, db, current_user)
    try:
        await change_element_state(
            db=db,
            element=element,
            user_id=current_user.id,
            expected_version=body.expected_version,
            event_kind="restore_archive",
            reason=body.reason,
        )
        await db.commit()
        await db.refresh(element)
        return await _build_element_response(element, db)
    except LoreStaleVersionError as exc:
        await db.rollback()
        raise _stale_version_response(exc)
    except LoreWriteError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post(
    "/elements/{element_id}/versions/{version_no}/restore",
    response_model=LoreElementResponse,
)
async def restore_lore_element_version(
    project_id: str,
    element_id: str,
    version_no: int,
    body: LoreElementStateInput,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    element = await _load_relational_element(project_id, element_id, db, current_user)

    target_result = await db.execute(
        select(ElementVersion).where(
            ElementVersion.element_id == element.id,
            ElementVersion.version_no == version_no,
        )
    )
    target_version = target_result.scalar_one_or_none()
    if target_version is None:
        raise HTTPException(status_code=404, detail="该版本不存在")

    try:
        await restore_element_version_content(
            db=db,
            element=element,
            user_id=current_user.id,
            target_version=target_version,
            expected_version=body.expected_version,
            reason=body.reason or f"恢复版本 {version_no}",
        )
        await db.commit()
        await db.refresh(element)
        return await _build_element_response(element, db)
    except LoreStaleVersionError as exc:
        await db.rollback()
        raise _stale_version_response(exc)
    except LoreWriteError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


# ─── Relation endpoints ───────────────────────────────────────────


@router.get(
    "/elements/{element_id}/relations",
    response_model=LoreRelationListResponse,
)
async def list_lore_relations(
    project_id: str,
    element_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    relation_status: Annotated[
        str | None,
        Query(alias="status", pattern="^(active|archived)$"),
    ] = None,
):
    project = await get_project_for_owner(project_id, current_user, db)
    _require_relational_mode(project)
    element = await db.scalar(
        select(SettingElement).where(
            SettingElement.id == element_id,
            SettingElement.project_id == project_id,
        )
    )
    if element is None:
        raise HTTPException(status_code=404, detail="设定不存在")

    filters = [
        ElementRelation.project_id == project_id,
        or_(
            ElementRelation.source_element_id == element_id,
            ElementRelation.target_element_id == element_id,
        ),
    ]
    if relation_status:
        filters.append(ElementRelation.status == relation_status)

    after_id = ""
    if cursor:
        cursor_data = _decode_cursor(cursor)
        if (
            cursor_data.get("kind") != "relations"
            or cursor_data.get("project_id") != project_id
            or cursor_data.get("element_id") != element_id
            or cursor_data.get("status") != (relation_status or "")
        ):
            raise HTTPException(status_code=400, detail="关系分页游标与当前查询不匹配")
        after_id = cursor_data.get("after_id", "")
        if not isinstance(after_id, str):
            raise HTTPException(status_code=400, detail="分页游标无效")
        filters.append(ElementRelation.id > after_id)

    total = await db.scalar(
        select(func.count()).select_from(ElementRelation).where(*filters[:2])
        .where(
            ElementRelation.status == relation_status
            if relation_status
            else True
        )
    )
    result = await db.execute(
        select(ElementRelation)
        .where(*filters)
        .order_by(ElementRelation.id.asc())
        .limit(limit + 1)
    )
    relations = list(result.scalars().all())
    has_more = len(relations) > limit
    page = relations[:limit]
    next_cursor = None
    if has_more and page:
        next_cursor = _encode_cursor(
            {
                "v": _CURSOR_VERSION,
                "kind": "relations",
                "project_id": project_id,
                "element_id": element_id,
                "status": relation_status or "",
                "after_id": page[-1].id,
            }
        )
    return LoreRelationListResponse(
        items=[await _build_relation_response(item, db) for item in page],
        next_cursor=next_cursor,
        has_more=has_more,
        total=total or 0,
    )


@router.post(
    "/elements/{element_id}/relations",
    response_model=LoreRelationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_lore_relation(
    project_id: str,
    element_id: str,
    body: LoreRelationCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project = await get_project_for_owner(project_id, current_user, db)
    _require_relational_mode(project)
    endpoint_ids = sorted({element_id, body.target_element_id})
    result = await db.execute(
        select(SettingElement)
        .where(
            SettingElement.project_id == project_id,
            SettingElement.id.in_(endpoint_ids),
        )
        .order_by(SettingElement.id.asc())
        .with_for_update()
    )
    endpoints = {item.id: item for item in result.scalars().all()}
    source = endpoints.get(element_id)
    target = endpoints.get(body.target_element_id)
    if source is None:
        raise HTTPException(status_code=404, detail="设定不存在")
    if target is None:
        raise HTTPException(status_code=404, detail="目标设定不存在")
    endpoint_conflicts = []
    if source.lock_version != body.source_expected_version:
        endpoint_conflicts.append(
            {"endpoint": "source", "current_lock_version": source.lock_version}
        )
    if target.lock_version != body.target_expected_version:
        endpoint_conflicts.append(
            {"endpoint": "target", "current_lock_version": target.lock_version}
        )
    if endpoint_conflicts:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LORE_RELATION_ENDPOINT_CHANGED",
                "message": "关系端点已变化，请重新加载后重试",
                "endpoint_conflicts": endpoint_conflicts,
            },
        )
    try:
        relation = await create_relation(
            db=db,
            project_id=project_id,
            source=source,
            target=target,
            user_id=current_user.id,
            relation_key=body.relation_key,
            forward_label=body.forward_label,
            reverse_label=body.reverse_label,
            description=body.description,
            metadata=body.metadata,
        )
        await db.commit()
        await db.refresh(relation)
        return await _build_relation_response(relation, db)
    except LoreWriteError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LORE_RELATION_CONFLICT",
                "message": "关系保存冲突，请重新加载后重试",
            },
        ) from exc


@router.patch("/relations/{relation_id}", response_model=LoreRelationResponse)
async def update_lore_relation(
    project_id: str,
    relation_id: str,
    body: LoreRelationUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    relation = await _load_relational_relation(
        project_id, relation_id, db, current_user
    )
    try:
        await update_relation(
            db=db,
            relation=relation,
            user_id=current_user.id,
            expected_version=body.expected_version,
            forward_label=body.forward_label,
            reverse_label=body.reverse_label,
            description=body.description,
            metadata=body.metadata,
        )
        await db.commit()
        await db.refresh(relation)
        return await _build_relation_response(relation, db)
    except LoreStaleVersionError as exc:
        await db.rollback()
        raise _stale_version_response(exc)
    except LoreWriteError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


async def _set_lore_relation_status(
    project_id: str,
    relation_id: str,
    body: LoreRelationStateInput,
    new_status: str,
    db: AsyncSession,
    current_user: User,
) -> LoreRelationResponse:
    check_writes_available()
    relation = await _load_relational_relation(
        project_id, relation_id, db, current_user, for_update=False
    )
    await _lock_relation_endpoints(relation, db)
    relation = await _load_relational_relation(
        project_id, relation_id, db, current_user
    )
    try:
        await change_relation_state(
            db=db,
            relation=relation,
            user_id=current_user.id,
            expected_version=body.expected_version,
            status=new_status,
            reason=body.reason,
        )
        await db.commit()
        await db.refresh(relation)
        return await _build_relation_response(relation, db)
    except LoreStaleVersionError as exc:
        await db.rollback()
        raise _stale_version_response(exc)
    except LoreWriteError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post("/relations/{relation_id}/archive", response_model=LoreRelationResponse)
async def archive_lore_relation(
    project_id: str,
    relation_id: str,
    body: LoreRelationStateInput,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    return await _set_lore_relation_status(
        project_id, relation_id, body, "archived", db, current_user
    )


@router.post("/relations/{relation_id}/restore", response_model=LoreRelationResponse)
async def restore_lore_relation(
    project_id: str,
    relation_id: str,
    body: LoreRelationStateInput,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    return await _set_lore_relation_status(
        project_id, relation_id, body, "active", db, current_user
    )


@router.get(
    "/relations/{relation_id}/versions",
    response_model=LoreRelationVersionsResponse,
)
async def list_lore_relation_versions(
    project_id: str,
    relation_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    relation = await _load_relational_relation(
        project_id, relation_id, db, current_user
    )
    result = await db.execute(
        select(ElementRelationVersion)
        .where(ElementRelationVersion.relation_id == relation.id)
        .order_by(ElementRelationVersion.version_no.desc())
    )
    versions = list(result.scalars().all())
    return LoreRelationVersionsResponse(
        items=[
            LoreRelationVersionSummary(
                version_no=item.version_no,
                source_element_id=item.source_element_id,
                target_element_id=item.target_element_id,
                relation_key=item.relation_key,
                forward_label=item.forward_label,
                reverse_label=item.reverse_label,
                description=item.description or "",
                metadata=item.metadata_ or {},
                status=item.status,
                change_reason=item.change_reason or "",
                created_at=item.created_at,
            )
            for item in versions
        ],
        total=len(versions),
    )
