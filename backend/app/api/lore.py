"""Read-only lore API backed by a deterministic legacy worldview projection."""

import base64
import binascii
import hashlib
import hmac
import json
from collections import Counter
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import User, get_current_user, get_project_for_owner
from app.core.lore_migration import (
    LoreProjection,
    ProjectedLoreElement,
    normalize_lore_name,
    project_legacy_worldview,
    type_field_definitions,
    validate_projection,
)
from app.database import get_db
from app.models.project import Worldview
from app.schemas.lore import (
    LoreElementDetail,
    LoreElementListItem,
    LoreFacetCount,
    LoreFacets,
    LoreFieldDefinition,
    LoreListResponse,
    LoreMigrationStatus,
    LoreSourceSummary,
    LoreSourcesResponse,
    LoreTypeSummary,
    LoreVersionSummary,
    LoreVersionsResponse,
)

router = APIRouter(prefix="/api/projects/{project_id}/lore", tags=["lore"])

_CURSOR_VERSION = 1
_CURSOR_SECRET = (settings.JWT_SECRET or "development-lore-cursor-secret").encode()


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
) -> str:
    payload = json.dumps(
        {
            "q": normalize_lore_name(q or ""),
            "type": type_key or "",
            "confirmation": confirmation_status or "",
            "source": source_kind or "",
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
):
    project, projection = await _load_projection(project_id, db, current_user)
    filter_sig = _filter_signature(
        q,
        type_key,
        confirmation_status,
        source_kind,
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
        ),
        migration_status=_migration_status(project, projection),
    )


@router.get("/elements/{element_id}", response_model=LoreElementDetail)
async def get_lore_element(
    project_id: str,
    element_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    project, projection = await _load_projection(project_id, db, current_user)
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
    _project, projection = await _load_projection(project_id, db, current_user)
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
    _project, projection = await _load_projection(project_id, db, current_user)
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
