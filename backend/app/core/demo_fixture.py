"""Transactional, owner-scoped bootstrap for the non-production demo fixture."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR, settings
from app.core.lore_migration import TYPE_FIELD_SCHEMAS
from app.core.maintenance import ensure_project_writes_available
from app.core.settings_store import load_settings
from app.models.lore import (
    ElementSource,
    ElementStateEvent,
    ElementVersion,
    SettingElement,
    SettingType,
    SettingTypeRevision,
)
from app.models.planning import (
    NovelPlan,
    PlanningChapter,
    PlanningLoreAssignment,
    PlanningLoreAssignmentEvent,
    PlanningPart,
)
from app.models.project import NovelGenre, Project, ProjectStatus
from app.schemas.demo import DemoFixtureBootstrapResponse, DemoFixtureCurrentResponse


class DemoFixtureUnavailableError(Exception):
    """The server is not an explicitly isolated demo/test environment."""


class DemoFixtureDivergedError(Exception):
    """A deterministic fixture identity exists but no longer matches v1."""


@dataclass(frozen=True)
class DemoFixtureIds:
    project: str
    setting_type: str
    type_revision: str
    element: str
    source: str
    element_version: str
    element_event: str
    plan: str
    part: str
    chapter: str
    assignment: str
    assignment_event: str


def _stable_id(user_id: str, name: str) -> str:
    return hashlib.sha256(f"demo-v1:{user_id}:{name}".encode("utf-8")).hexdigest()[:32]


def fixture_ids(user_id: str) -> DemoFixtureIds:
    return DemoFixtureIds(
        **{
            field: _stable_id(user_id, field)
            for field in DemoFixtureIds.__dataclass_fields__
        }
    )


_PROJECT_TITLE = "雾港回声（技术模拟样例）"
_PART_TITLE = "雾潮初临"
_CHAPTER_TITLE = "停摆的星港"
_ELEMENT_NAME = "沈星"
_ELEMENT_SUMMARY = "守夜司调查员，负责追查星港停摆事件。"
_SOURCE_EXCERPT = (
    "沈星是守夜司调查员。她谨慎、善于观察，目标是查明星港停摆的原因；"
    "她没有越过潮汐门限的能力。"
)
_ELEMENT_PAYLOAD = {
    "identity": "守夜司调查员",
    "personality": "谨慎，善于观察",
    "background": "受命调查星港停摆事件",
    "limitations": "无法越过潮汐门限",
    "goals": "查明星港停摆的原因",
    "motivations": "保护港区居民并完成守夜司委托",
    "possible_plots": "在停摆的星港调查褪色航标",
}
_CHARACTER_SCHEMA = [dict(field) for field in TYPE_FIELD_SCHEMAS["character"]]
_FIELD_STATES = {
    field["key"]: (
        "provided" if field["key"] in _ELEMENT_PAYLOAD else "unknown"
    )
    for field in _CHARACTER_SCHEMA
}


def _excerpt_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _active_database_url(db: AsyncSession) -> str:
    bind = db.get_bind()
    return str(bind.url)


def ensure_demo_fixture_environment(db: AsyncSession) -> None:
    """Fail closed unless the active database and server are explicitly isolated."""

    if (
        not settings.DEMO_FIXTURE_ENABLED
        or settings.APP_ENVIRONMENT not in {"demo", "test"}
        or not settings.DEBUG
        or bool(load_settings().get("api_key"))
    ):
        raise DemoFixtureUnavailableError

    url = make_url(_active_database_url(db))
    if url.get_backend_name() != "sqlite":
        raise DemoFixtureUnavailableError

    database = url.database
    if settings.APP_ENVIRONMENT == "test":
        # Test mode is intentionally narrower than demo mode: only the
        # disposable in-memory database is accepted.
        if database in {None, "", ":memory:"}:
            return
        raise DemoFixtureUnavailableError

    if not database or database == ":memory:":
        raise DemoFixtureUnavailableError

    candidate = Path(database)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    resolved_candidate = candidate.resolve(strict=False)
    resolved_demo_root = (DATA_DIR / "demo").resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_demo_root)
    except ValueError as exc:
        raise DemoFixtureUnavailableError from exc


async def _load_rows(db: AsyncSession, ids: DemoFixtureIds) -> dict[str, Any]:
    return {
        "project": await db.get(Project, ids.project),
        "setting_type": await db.get(SettingType, ids.setting_type),
        "type_revision": await db.get(SettingTypeRevision, ids.type_revision),
        "element": await db.get(SettingElement, ids.element),
        "source": await db.get(ElementSource, ids.source),
        "element_version": await db.get(ElementVersion, ids.element_version),
        "element_event": await db.get(ElementStateEvent, ids.element_event),
        "plan": await db.get(NovelPlan, ids.plan),
        "part": await db.get(PlanningPart, ids.part),
        "chapter": await db.get(PlanningChapter, ids.chapter),
        "assignment": await db.get(PlanningLoreAssignment, ids.assignment),
        "assignment_event": await db.get(
            PlanningLoreAssignmentEvent, ids.assignment_event
        ),
    }


def _assert_values(row: Any, expected: dict[str, Any]) -> None:
    if row is None or any(getattr(row, key) != value for key, value in expected.items()):
        raise DemoFixtureDivergedError


async def _validate_existing(
    db: AsyncSession, user_id: str, ids: DemoFixtureIds
) -> None:
    rows = await _load_rows(db, ids)
    _assert_values(
        rows["project"],
        {
            "owner_id": user_id,
            "title": _PROJECT_TITLE,
            "genre": NovelGenre.SCIFI,
            "status": ProjectStatus.DRAFT,
            "total_chapters": 2,
            "chapter_word_count": 1800,
            "style_intensity": "standard",
            "lore_storage_mode": "relational",
        },
    )
    _assert_values(
        rows["setting_type"],
        {
            "project_id": ids.project,
            "key": "character",
            "display_name": "角色",
            "description": "内置角色类型",
            "is_builtin": True,
            "schema_revision": 1,
            "field_schema": _CHARACTER_SCHEMA,
            "status": "active",
        },
    )
    _assert_values(
        rows["type_revision"],
        {
            "type_id": ids.setting_type,
            "revision": 1,
            "display_name": "角色",
            "field_schema": _CHARACTER_SCHEMA,
            "change_summary": "初始化技术模拟样例类型",
        },
    )
    _assert_values(
        rows["element"],
        {
            "project_id": ids.project,
            "type_id": ids.setting_type,
            "name": _ELEMENT_NAME,
            "normalized_name": _ELEMENT_NAME.casefold(),
            "summary": _ELEMENT_SUMMARY,
            "payload": _ELEMENT_PAYLOAD,
            "payload_schema_revision": 1,
            "field_states": _FIELD_STATES,
            "confirmation_status": "confirmed",
            "lifecycle_status": "active",
            "enabled": True,
            "content_version": 1,
            "lock_version": 1,
        },
    )
    _assert_values(
        rows["source"],
        {
            "project_id": ids.project,
            "element_id": ids.element,
            "source_kind": "manual",
            "source_ref": "技术模拟样例原稿",
            "locator": {"fixture_version": 1},
            "excerpt": _SOURCE_EXCERPT,
            "excerpt_hash": _excerpt_hash(_SOURCE_EXCERPT),
            "confirmation_status": "provided",
            "is_primary": True,
        },
    )
    _assert_values(
        rows["element_version"],
        {
            "element_id": ids.element,
            "version_no": 1,
            "type_id": ids.setting_type,
            "type_schema_revision": 1,
            "name": _ELEMENT_NAME,
            "summary": _ELEMENT_SUMMARY,
            "payload": _ELEMENT_PAYLOAD,
            "field_states": _FIELD_STATES,
            "change_reason": "创建技术模拟样例",
            "source_id": ids.source,
            "created_by": user_id,
        },
    )
    _assert_values(
        rows["element_event"],
        {
            "element_id": ids.element,
            "event_kind": "create",
            "previous_lock_version": 0,
            "new_lock_version": 1,
            "performed_by": user_id,
            "metadata_": {"fixture_version": 1},
        },
    )
    _assert_values(
        rows["plan"],
        {
            "project_id": ids.project,
            "status": "active",
            "structure_version": 1,
            "assignment_version": 1,
        },
    )
    _assert_values(
        rows["part"],
        {
            "project_id": ids.project,
            "plan_id": ids.plan,
            "title": _PART_TITLE,
            "description": "雾潮逼近，星港在异常停摆中失去对外联络。",
            "position": 1,
            "status": "active",
            "lock_version": 1,
        },
    )
    _assert_values(
        rows["chapter"],
        {
            "project_id": ids.project,
            "plan_id": ids.plan,
            "part_id": ids.part,
            "title": _CHAPTER_TITLE,
            "summary": "沈星抵达停摆的星港，开始调查褪色航标。",
            "target_word_count": 1800,
            "position": 1,
            "status": "active",
            "lock_version": 1,
        },
    )
    _assert_values(
        rows["assignment"],
        {
            "project_id": ids.project,
            "plan_id": ids.plan,
            "element_id": ids.element,
            "scope_type": "chapter",
            "scope_target_id": ids.chapter,
            "part_id": None,
            "chapter_id": ids.chapter,
            "element_content_version": 1,
            "status": "active",
            "lock_version": 1,
            "created_by": user_id,
            "updated_by": user_id,
        },
    )
    _assert_values(
        rows["assignment_event"],
        {
            "project_id": ids.project,
            "assignment_id": ids.assignment,
            "performed_by": user_id,
            "action": "assign",
            "previous_status": None,
            "new_status": "active",
            "previous_lock_version": 0,
            "new_lock_version": 1,
            "element_content_version": 1,
        },
    )


def _response(ids: DemoFixtureIds, replayed: bool) -> DemoFixtureBootstrapResponse:
    return DemoFixtureBootstrapResponse(
        replayed=replayed,
        project_id=ids.project,
        plan_id=ids.plan,
        part_id=ids.part,
        chapter_id=ids.chapter,
        element_id=ids.element,
        assignment_id=ids.assignment,
        next_path=f"/project/{ids.project}/lore",
    )


async def get_demo_fixture_current(
    db: AsyncSession, user_id: str
) -> DemoFixtureCurrentResponse:
    """Describe the current owner-scoped fixture without creating or repairing it."""

    ids = fixture_ids(user_id)
    rows = await _load_rows(db, ids)
    if all(row is None for row in rows.values()):
        return DemoFixtureCurrentResponse(
            state="missing",
            can_bootstrap=True,
            preserved=False,
            recommended_action="bootstrap_fixture",
        )

    try:
        await _validate_existing(db, user_id, ids)
    except DemoFixtureDivergedError:
        project = rows["project"]
        preserved_project_id = (
            ids.project
            if project is not None and project.owner_id == user_id
            else None
        )
        return DemoFixtureCurrentResponse(
            state="diverged",
            can_bootstrap=False,
            preserved=True,
            project_id=preserved_project_id,
            recommended_action="preserve_existing_fixture",
        )

    return DemoFixtureCurrentResponse(
        state="ready",
        can_bootstrap=False,
        preserved=False,
        project_id=ids.project,
        plan_id=ids.plan,
        part_id=ids.part,
        chapter_id=ids.chapter,
        element_id=ids.element,
        assignment_id=ids.assignment,
        next_path=f"/project/{ids.project}/lore",
        recommended_action="open_fixture",
    )


async def _add_fixture_rows(
    db: AsyncSession, user_id: str, ids: DemoFixtureIds
) -> None:
    db.add(
        Project(
            id=ids.project,
            title=_PROJECT_TITLE,
            genre=NovelGenre.SCIFI,
            status=ProjectStatus.DRAFT,
            total_chapters=2,
            chapter_word_count=1800,
            style_intensity="standard",
            owner_id=user_id,
            lore_storage_mode="relational",
        )
    )
    await db.flush()

    db.add(
        SettingType(
            id=ids.setting_type,
            project_id=ids.project,
            key="character",
            display_name="角色",
            description="内置角色类型",
            is_builtin=True,
            schema_revision=1,
            field_schema=_CHARACTER_SCHEMA,
            status="active",
        )
    )
    await db.flush()

    db.add_all(
        [
            SettingTypeRevision(
                id=ids.type_revision,
                type_id=ids.setting_type,
                revision=1,
                display_name="角色",
                field_schema=_CHARACTER_SCHEMA,
                change_summary="初始化技术模拟样例类型",
            ),
            SettingElement(
                id=ids.element,
                project_id=ids.project,
                type_id=ids.setting_type,
                name=_ELEMENT_NAME,
                normalized_name=_ELEMENT_NAME.casefold(),
                summary=_ELEMENT_SUMMARY,
                payload=_ELEMENT_PAYLOAD,
                payload_schema_revision=1,
                field_states=_FIELD_STATES,
                confirmation_status="confirmed",
                lifecycle_status="active",
                enabled=True,
                content_version=1,
                lock_version=1,
            ),
            NovelPlan(
                id=ids.plan,
                project_id=ids.project,
                status="active",
                structure_version=1,
                assignment_version=1,
            ),
        ]
    )
    await db.flush()

    db.add(
        ElementSource(
            id=ids.source,
            project_id=ids.project,
            element_id=ids.element,
            source_kind="manual",
            source_ref="技术模拟样例原稿",
            locator={"fixture_version": 1},
            excerpt=_SOURCE_EXCERPT,
            excerpt_hash=_excerpt_hash(_SOURCE_EXCERPT),
            confirmation_status="provided",
            is_primary=True,
        )
    )
    await db.flush()

    db.add_all(
        [
            ElementVersion(
                id=ids.element_version,
                element_id=ids.element,
                version_no=1,
                type_id=ids.setting_type,
                type_schema_revision=1,
                name=_ELEMENT_NAME,
                summary=_ELEMENT_SUMMARY,
                payload=_ELEMENT_PAYLOAD,
                field_states=_FIELD_STATES,
                change_reason="创建技术模拟样例",
                source_id=ids.source,
                created_by=user_id,
            ),
            ElementStateEvent(
                id=ids.element_event,
                element_id=ids.element,
                event_kind="create",
                previous_lock_version=0,
                new_lock_version=1,
                performed_by=user_id,
                metadata_={"fixture_version": 1},
            ),
            PlanningPart(
                id=ids.part,
                project_id=ids.project,
                plan_id=ids.plan,
                title=_PART_TITLE,
                description="雾潮逼近，星港在异常停摆中失去对外联络。",
                position=1,
                status="active",
                lock_version=1,
            ),
        ]
    )
    await db.flush()

    db.add(
        PlanningChapter(
            id=ids.chapter,
            project_id=ids.project,
            plan_id=ids.plan,
            part_id=ids.part,
            title=_CHAPTER_TITLE,
            summary="沈星抵达停摆的星港，开始调查褪色航标。",
            target_word_count=1800,
            position=1,
            status="active",
            lock_version=1,
        )
    )
    await db.flush()

    db.add(
        PlanningLoreAssignment(
            id=ids.assignment,
            project_id=ids.project,
            plan_id=ids.plan,
            element_id=ids.element,
            scope_type="chapter",
            scope_target_id=ids.chapter,
            chapter_id=ids.chapter,
            element_content_version=1,
            status="active",
            lock_version=1,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    await db.flush()

    db.add(
        PlanningLoreAssignmentEvent(
            id=ids.assignment_event,
            project_id=ids.project,
            assignment_id=ids.assignment,
            performed_by=user_id,
            action="assign",
            previous_status=None,
            new_status="active",
            previous_lock_version=0,
            new_lock_version=1,
            element_content_version=1,
        )
    )


async def bootstrap_demo_fixture(
    db: AsyncSession, user_id: str
) -> DemoFixtureBootstrapResponse:
    """Create or strictly replay fixture v1 without repairing divergent data."""

    ensure_project_writes_available()
    ids = fixture_ids(user_id)
    existing = await _load_rows(db, ids)
    if existing["project"] is not None:
        await _validate_existing(db, user_id, ids)
        return _response(ids, replayed=True)
    if any(row is not None for row in existing.values()):
        raise DemoFixtureDivergedError

    try:
        await _add_fixture_rows(db, user_id, ids)
        await db.flush()
        ensure_project_writes_available()
        await db.commit()
    except IntegrityError:
        await db.rollback()
        try:
            await _validate_existing(db, user_id, ids)
        except DemoFixtureDivergedError:
            raise
        return _response(ids, replayed=True)
    except Exception:
        await db.rollback()
        raise

    return _response(ids, replayed=False)
