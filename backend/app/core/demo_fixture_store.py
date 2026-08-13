"""Deterministic persistence graph for non-production demo fixture v1."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.demo_fixture_content import (
    ASSIGNMENT_SPECS,
    CHAPTER_SPECS,
    ELEMENT_SPECS,
    FORESHADOW_PLANT_NOTE,
    FORESHADOW_RESOLVE_CONDITION,
    FORESHADOW_RESOLVE_NOTE,
    PART_DESCRIPTION,
    PART_TITLE,
    PROJECT_TITLE,
    RELATION_SPECS,
    SOURCE_REFERENCE,
    TYPE_BY_KEY,
    TYPE_SPECS,
)
from app.models.foreshadow import (
    ForeshadowFact,
    ForeshadowLifecycle,
    ForeshadowLifecycleEvent,
    ForeshadowPlanItem,
)
from app.models.lore import (
    ElementRelation,
    ElementRelationVersion,
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


class DemoFixtureDivergedError(Exception):
    """A deterministic fixture identity exists but no longer matches v1."""


EXPECTED_FIXTURE_COUNTS = {
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


def _stable_id(user_id: str, name: str) -> str:
    return hashlib.sha256(f"demo-v1:{user_id}:{name}".encode()).hexdigest()[:32]


@dataclass(frozen=True)
class DemoFixtureIds:
    user_id: str

    def named(self, name: str) -> str:
        return _stable_id(self.user_id, name)

    @property
    def project(self) -> str:
        return self.named("project")

    @property
    def plan(self) -> str:
        return self.named("plan")

    @property
    def part(self) -> str:
        return self.named("part")

    @property
    def chapter(self) -> str:
        return self.named("chapter")

    @property
    def second_chapter(self) -> str:
        return self.named("chapter:chapter_two")

    @property
    def element(self) -> str:
        return self.named("element")

    @property
    def assignment(self) -> str:
        return self.named("assignment")

    @property
    def foreshadow_element(self) -> str:
        return self.element_id("faded_beacon")

    @property
    def foreshadow_lifecycle(self) -> str:
        return self.named("foreshadow:lifecycle:faded_beacon")

    def type_id(self, key: str) -> str:
        return self.named("setting_type" if key == "character" else f"type:{key}")

    def type_revision_id(self, key: str) -> str:
        return self.named(
            "type_revision" if key == "character" else f"type_revision:{key}"
        )

    def element_id(self, slug: str) -> str:
        return self.named("element" if slug == "shen_xing" else f"element:{slug}")

    def source_id(self, slug: str) -> str:
        return self.named("source" if slug == "shen_xing" else f"source:{slug}")

    def element_version_id(self, slug: str) -> str:
        return self.named(
            "element_version" if slug == "shen_xing" else f"element_version:{slug}"
        )

    def element_event_id(self, slug: str) -> str:
        return self.named(
            "element_event" if slug == "shen_xing" else f"element_event:{slug}"
        )

    def chapter_id(self, slug: str) -> str:
        return self.chapter if slug == "chapter_one" else self.second_chapter

    def assignment_id(self, slug: str) -> str:
        return self.named(
            "assignment" if slug == "shen_xing_chapter" else f"assignment:{slug}"
        )

    def assignment_event_id(self, slug: str) -> str:
        return self.named(
            "assignment_event"
            if slug == "shen_xing_chapter"
            else f"assignment_event:{slug}"
        )

    def relation_id(self, slug: str) -> str:
        return self.named(f"relation:{slug}")

    def relation_version_id(self, slug: str) -> str:
        return self.named(f"relation_version:{slug}")

    def foreshadow_plan_id(self, action: str) -> str:
        return self.named(f"foreshadow:plan:{action}")

    def foreshadow_event_id(self, event: str) -> str:
        return self.named(f"foreshadow:event:{event}")


def fixture_ids(user_id: str) -> DemoFixtureIds:
    return DemoFixtureIds(user_id=user_id)


def _field_states(type_key: str, payload: dict[str, Any]) -> dict[str, str]:
    return {
        field["key"]: "provided" if field["key"] in payload else "unknown"
        for field in TYPE_BY_KEY[type_key].fields
    }


def _excerpt_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FixtureRowSpec:
    name: str
    model: type[Any]
    row_id: str
    expected: dict[str, Any]


def fixture_row_specs(user_id: str) -> tuple[FixtureRowSpec, ...]:
    ids = fixture_ids(user_id)
    rows: list[FixtureRowSpec] = [
        FixtureRowSpec(
            "project",
            Project,
            ids.project,
            {
                "owner_id": user_id,
                "title": PROJECT_TITLE,
                "genre": NovelGenre.SCIFI,
                "status": ProjectStatus.DRAFT,
                "total_chapters": 2,
                "chapter_word_count": 1800,
                "style_intensity": "standard",
                "lore_storage_mode": "relational",
            },
        ),
        FixtureRowSpec(
            "plan",
            NovelPlan,
            ids.plan,
            {
                "project_id": ids.project,
                "status": "active",
                "structure_version": 1,
                "assignment_version": 1,
            },
        ),
        FixtureRowSpec(
            "part",
            PlanningPart,
            ids.part,
            {
                "project_id": ids.project,
                "plan_id": ids.plan,
                "title": PART_TITLE,
                "description": PART_DESCRIPTION,
                "position": 1,
                "status": "active",
                "lock_version": 1,
            },
        ),
    ]
    for type_spec in TYPE_SPECS:
        type_id = ids.type_id(type_spec.key)
        rows.extend(
            (
                FixtureRowSpec(
                    f"type:{type_spec.key}",
                    SettingType,
                    type_id,
                    {
                        "project_id": ids.project,
                        "key": type_spec.key,
                        "display_name": type_spec.display_name,
                        "description": f"内置{type_spec.display_name}类型",
                        "is_builtin": True,
                        "schema_revision": 1,
                        "field_schema": list(type_spec.fields),
                        "status": "active",
                    },
                ),
                FixtureRowSpec(
                    f"type_revision:{type_spec.key}",
                    SettingTypeRevision,
                    ids.type_revision_id(type_spec.key),
                    {
                        "type_id": type_id,
                        "revision": 1,
                        "display_name": type_spec.display_name,
                        "field_schema": list(type_spec.fields),
                        "change_summary": "初始化非生产固定样例类型",
                    },
                ),
            )
        )
    for chapter_spec in CHAPTER_SPECS:
        rows.append(
            FixtureRowSpec(
                f"chapter:{chapter_spec.slug}",
                PlanningChapter,
                ids.chapter_id(chapter_spec.slug),
                {
                    "project_id": ids.project,
                    "plan_id": ids.plan,
                    "part_id": ids.part,
                    "title": chapter_spec.title,
                    "summary": chapter_spec.summary,
                    "target_word_count": 1800,
                    "position": chapter_spec.position,
                    "status": "active",
                    "lock_version": 1,
                },
            )
        )
    for element_spec in ELEMENT_SPECS:
        element_id = ids.element_id(element_spec.slug)
        type_id = ids.type_id(element_spec.type_key)
        states = _field_states(element_spec.type_key, element_spec.payload)
        rows.extend(
            (
                FixtureRowSpec(
                    f"element:{element_spec.slug}",
                    SettingElement,
                    element_id,
                    {
                        "project_id": ids.project,
                        "type_id": type_id,
                        "name": element_spec.name,
                        "normalized_name": element_spec.name.casefold(),
                        "summary": element_spec.summary,
                        "payload": element_spec.payload,
                        "payload_schema_revision": 1,
                        "field_states": states,
                        "confirmation_status": "confirmed",
                        "lifecycle_status": "active",
                        "enabled": True,
                        "content_version": 1,
                        "lock_version": 1,
                    },
                ),
                FixtureRowSpec(
                    f"source:{element_spec.slug}",
                    ElementSource,
                    ids.source_id(element_spec.slug),
                    {
                        "project_id": ids.project,
                        "element_id": element_id,
                        "source_kind": "manual",
                        "source_ref": SOURCE_REFERENCE,
                        "locator": {
                            "fixture_version": 1,
                            "section": element_spec.source_section,
                        },
                        "excerpt": element_spec.excerpt,
                        "excerpt_hash": _excerpt_hash(element_spec.excerpt),
                        "confirmation_status": "provided",
                        "is_primary": True,
                    },
                ),
                FixtureRowSpec(
                    f"element_version:{element_spec.slug}",
                    ElementVersion,
                    ids.element_version_id(element_spec.slug),
                    {
                        "element_id": element_id,
                        "version_no": 1,
                        "type_id": type_id,
                        "type_schema_revision": 1,
                        "name": element_spec.name,
                        "summary": element_spec.summary,
                        "payload": element_spec.payload,
                        "field_states": states,
                        "change_reason": "初始化非生产固定样例",
                        "source_id": ids.source_id(element_spec.slug),
                        "created_by": user_id,
                    },
                ),
                FixtureRowSpec(
                    f"element_event:{element_spec.slug}",
                    ElementStateEvent,
                    ids.element_event_id(element_spec.slug),
                    {
                        "element_id": element_id,
                        "event_kind": "create",
                        "previous_lock_version": 0,
                        "new_lock_version": 1,
                        "performed_by": user_id,
                        "metadata_": {"fixture_version": 1},
                    },
                ),
            )
        )
    for assignment_spec in ASSIGNMENT_SPECS:
        assignment_id = ids.assignment_id(assignment_spec.slug)
        if assignment_spec.scope_type == "novel":
            target_id, part_id, chapter_id = ids.project, None, None
        elif assignment_spec.scope_type == "part":
            target_id, part_id, chapter_id = ids.part, ids.part, None
        else:
            target_id, part_id, chapter_id = ids.chapter, None, ids.chapter
        rows.extend(
            (
                FixtureRowSpec(
                    f"assignment:{assignment_spec.slug}",
                    PlanningLoreAssignment,
                    assignment_id,
                    {
                        "project_id": ids.project,
                        "plan_id": ids.plan,
                        "element_id": ids.element_id(assignment_spec.element_slug),
                        "scope_type": assignment_spec.scope_type,
                        "scope_target_id": target_id,
                        "part_id": part_id,
                        "chapter_id": chapter_id,
                        "element_content_version": 1,
                        "status": "active",
                        "lock_version": 1,
                        "created_by": user_id,
                        "updated_by": user_id,
                    },
                ),
                FixtureRowSpec(
                    f"assignment_event:{assignment_spec.slug}",
                    PlanningLoreAssignmentEvent,
                    ids.assignment_event_id(assignment_spec.slug),
                    {
                        "project_id": ids.project,
                        "assignment_id": assignment_id,
                        "performed_by": user_id,
                        "action": "assign",
                        "previous_status": None,
                        "new_status": "active",
                        "previous_lock_version": 0,
                        "new_lock_version": 1,
                        "element_content_version": 1,
                    },
                ),
            )
        )
    for relation_spec in RELATION_SPECS:
        relation_id = ids.relation_id(relation_spec.slug)
        expected = {
            "source_element_id": ids.element_id(relation_spec.source_slug),
            "target_element_id": ids.element_id(relation_spec.target_slug),
            "relation_key": relation_spec.relation_key,
            "forward_label": relation_spec.forward_label,
            "reverse_label": relation_spec.reverse_label,
            "description": relation_spec.description,
            "metadata_": {},
            "status": "active",
        }
        rows.extend(
            (
                FixtureRowSpec(
                    f"relation:{relation_spec.slug}",
                    ElementRelation,
                    relation_id,
                    {
                        "project_id": ids.project,
                        **expected,
                        "version_no": 1,
                        "lock_version": 1,
                    },
                ),
                FixtureRowSpec(
                    f"relation_version:{relation_spec.slug}",
                    ElementRelationVersion,
                    ids.relation_version_id(relation_spec.slug),
                    {
                        "relation_id": relation_id,
                        "version_no": 1,
                        **expected,
                        "change_reason": "初始化非生产固定样例",
                        "created_by": user_id,
                    },
                ),
            )
        )
    rows.extend(
        (
            FixtureRowSpec(
                "foreshadow:lifecycle",
                ForeshadowLifecycle,
                ids.foreshadow_lifecycle,
                {
                    "project_id": ids.project,
                    "plan_id": ids.plan,
                    "element_id": ids.foreshadow_element,
                    "status": "active",
                    "lock_version": 3,
                    "created_by": user_id,
                    "updated_by": user_id,
                },
            ),
            FixtureRowSpec(
                "foreshadow:plant",
                ForeshadowPlanItem,
                ids.foreshadow_plan_id("plant"),
                {
                    "project_id": ids.project,
                    "plan_id": ids.plan,
                    "lifecycle_id": ids.foreshadow_lifecycle,
                    "action_kind": "plant",
                    "target_type": "chapter",
                    "target_id": ids.chapter,
                    "part_id": None,
                    "chapter_id": ids.chapter,
                    "condition_text": "",
                    "note": FORESHADOW_PLANT_NOTE,
                    "status": "active",
                    "lock_version": 1,
                    "created_by": user_id,
                    "updated_by": user_id,
                },
            ),
            FixtureRowSpec(
                "foreshadow:resolve",
                ForeshadowPlanItem,
                ids.foreshadow_plan_id("resolve"),
                {
                    "project_id": ids.project,
                    "plan_id": ids.plan,
                    "lifecycle_id": ids.foreshadow_lifecycle,
                    "action_kind": "resolve",
                    "target_type": "chapter",
                    "target_id": ids.second_chapter,
                    "part_id": None,
                    "chapter_id": ids.second_chapter,
                    "condition_text": FORESHADOW_RESOLVE_CONDITION,
                    "note": FORESHADOW_RESOLVE_NOTE,
                    "status": "active",
                    "lock_version": 1,
                    "created_by": user_id,
                    "updated_by": user_id,
                },
            ),
        )
    )
    event_specs = (
        ("create", "create", None, 0, 1),
        ("plant", "plan_create", ids.foreshadow_plan_id("plant"), 1, 2),
        ("resolve", "plan_create", ids.foreshadow_plan_id("resolve"), 2, 3),
    )
    for slug, kind, plan_item_id, previous, current in event_specs:
        rows.append(
            FixtureRowSpec(
                f"foreshadow:event:{slug}",
                ForeshadowLifecycleEvent,
                ids.foreshadow_event_id(slug),
                {
                    "project_id": ids.project,
                    "lifecycle_id": ids.foreshadow_lifecycle,
                    "performed_by": user_id,
                    "event_kind": kind,
                    "plan_item_id": plan_item_id,
                    "fact_id": None,
                    "previous_lifecycle_version": previous,
                    "new_lifecycle_version": current,
                    "metadata_json": {"fixture_version": 1},
                },
            )
        )
    return tuple(rows)


async def load_fixture_rows(db: AsyncSession, user_id: str) -> dict[str, Any | None]:
    return {
        spec.name: await db.get(spec.model, spec.row_id)
        for spec in fixture_row_specs(user_id)
    }


def _assert_values(row: Any, expected: dict[str, Any]) -> None:
    if row is None or any(
        getattr(row, key) != value for key, value in expected.items()
    ):
        raise DemoFixtureDivergedError


async def fixture_graph_counts(db: AsyncSession, project_id: str) -> dict[str, int]:
    models = {
        "setting_type_count": SettingType,
        "element_count": SettingElement,
        "source_count": ElementSource,
        "relation_count": ElementRelation,
        "part_count": PlanningPart,
        "chapter_count": PlanningChapter,
        "assignment_count": PlanningLoreAssignment,
        "foreshadow_lifecycle_count": ForeshadowLifecycle,
        "foreshadow_plan_count": ForeshadowPlanItem,
        "foreshadow_fact_count": ForeshadowFact,
    }
    return {
        name: int(
            await db.scalar(
                select(func.count())
                .select_from(model)
                .where(model.project_id == project_id)
            )
            or 0
        )
        for name, model in models.items()
    }


async def validate_fixture_rows(db: AsyncSession, user_id: str) -> dict[str, int]:
    rows = await load_fixture_rows(db, user_id)
    for spec in fixture_row_specs(user_id):
        _assert_values(rows[spec.name], spec.expected)
    counts = await fixture_graph_counts(db, fixture_ids(user_id).project)
    if counts != EXPECTED_FIXTURE_COUNTS:
        raise DemoFixtureDivergedError
    return counts


async def add_fixture_rows(db: AsyncSession, user_id: str) -> None:
    ids = fixture_ids(user_id)
    db.add(
        Project(
            id=ids.project,
            title=PROJECT_TITLE,
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

    db.add_all(
        [
            SettingType(
                id=ids.type_id(spec.key),
                project_id=ids.project,
                key=spec.key,
                display_name=spec.display_name,
                description=f"内置{spec.display_name}类型",
                is_builtin=True,
                schema_revision=1,
                field_schema=list(spec.fields),
                status="active",
            )
            for spec in TYPE_SPECS
        ]
    )
    await db.flush()

    db.add_all(
        [
            SettingTypeRevision(
                id=ids.type_revision_id(spec.key),
                type_id=ids.type_id(spec.key),
                revision=1,
                display_name=spec.display_name,
                field_schema=list(spec.fields),
                change_summary="初始化非生产固定样例类型",
            )
            for spec in TYPE_SPECS
        ]
        + [
            NovelPlan(
                id=ids.plan,
                project_id=ids.project,
                status="active",
                structure_version=1,
                assignment_version=1,
            )
        ]
    )
    await db.flush()

    db.add(
        PlanningPart(
            id=ids.part,
            project_id=ids.project,
            plan_id=ids.plan,
            title=PART_TITLE,
            description=PART_DESCRIPTION,
            position=1,
            status="active",
            lock_version=1,
        )
    )
    await db.flush()
    db.add_all(
        [
            PlanningChapter(
                id=ids.chapter_id(spec.slug),
                project_id=ids.project,
                plan_id=ids.plan,
                part_id=ids.part,
                title=spec.title,
                summary=spec.summary,
                target_word_count=1800,
                position=spec.position,
                status="active",
                lock_version=1,
            )
            for spec in CHAPTER_SPECS
        ]
    )
    await db.flush()

    db.add_all(
        [
            SettingElement(
                id=ids.element_id(spec.slug),
                project_id=ids.project,
                type_id=ids.type_id(spec.type_key),
                name=spec.name,
                normalized_name=spec.name.casefold(),
                summary=spec.summary,
                payload=spec.payload,
                payload_schema_revision=1,
                field_states=_field_states(spec.type_key, spec.payload),
                confirmation_status="confirmed",
                lifecycle_status="active",
                enabled=True,
                content_version=1,
                lock_version=1,
            )
            for spec in ELEMENT_SPECS
        ]
    )
    await db.flush()
    db.add_all(
        [
            ElementSource(
                id=ids.source_id(spec.slug),
                project_id=ids.project,
                element_id=ids.element_id(spec.slug),
                source_kind="manual",
                source_ref=SOURCE_REFERENCE,
                locator={"fixture_version": 1, "section": spec.source_section},
                excerpt=spec.excerpt,
                excerpt_hash=_excerpt_hash(spec.excerpt),
                confirmation_status="provided",
                is_primary=True,
            )
            for spec in ELEMENT_SPECS
        ]
    )
    await db.flush()
    db.add_all(
        [
            row
            for spec in ELEMENT_SPECS
            for row in (
                ElementVersion(
                    id=ids.element_version_id(spec.slug),
                    element_id=ids.element_id(spec.slug),
                    version_no=1,
                    type_id=ids.type_id(spec.type_key),
                    type_schema_revision=1,
                    name=spec.name,
                    summary=spec.summary,
                    payload=spec.payload,
                    field_states=_field_states(spec.type_key, spec.payload),
                    change_reason="初始化非生产固定样例",
                    source_id=ids.source_id(spec.slug),
                    created_by=user_id,
                ),
                ElementStateEvent(
                    id=ids.element_event_id(spec.slug),
                    element_id=ids.element_id(spec.slug),
                    event_kind="create",
                    previous_lock_version=0,
                    new_lock_version=1,
                    performed_by=user_id,
                    metadata_={"fixture_version": 1},
                ),
            )
        ]
    )
    await db.flush()

    assignments: list[PlanningLoreAssignment] = []
    for spec in ASSIGNMENT_SPECS:
        if spec.scope_type == "novel":
            target_id, part_id, chapter_id = ids.project, None, None
        elif spec.scope_type == "part":
            target_id, part_id, chapter_id = ids.part, ids.part, None
        else:
            target_id, part_id, chapter_id = ids.chapter, None, ids.chapter
        assignments.append(
            PlanningLoreAssignment(
                id=ids.assignment_id(spec.slug),
                project_id=ids.project,
                plan_id=ids.plan,
                element_id=ids.element_id(spec.element_slug),
                scope_type=spec.scope_type,
                scope_target_id=target_id,
                part_id=part_id,
                chapter_id=chapter_id,
                element_content_version=1,
                status="active",
                lock_version=1,
                created_by=user_id,
                updated_by=user_id,
            )
        )
    db.add_all(assignments)
    await db.flush()
    db.add_all(
        [
            PlanningLoreAssignmentEvent(
                id=ids.assignment_event_id(spec.slug),
                project_id=ids.project,
                assignment_id=ids.assignment_id(spec.slug),
                performed_by=user_id,
                action="assign",
                previous_status=None,
                new_status="active",
                previous_lock_version=0,
                new_lock_version=1,
                element_content_version=1,
            )
            for spec in ASSIGNMENT_SPECS
        ]
    )
    await db.flush()

    db.add_all(
        [
            ElementRelation(
                id=ids.relation_id(spec.slug),
                project_id=ids.project,
                source_element_id=ids.element_id(spec.source_slug),
                target_element_id=ids.element_id(spec.target_slug),
                relation_key=spec.relation_key,
                forward_label=spec.forward_label,
                reverse_label=spec.reverse_label,
                description=spec.description,
                metadata_={},
                status="active",
                version_no=1,
                lock_version=1,
            )
            for spec in RELATION_SPECS
        ]
    )
    await db.flush()
    db.add_all(
        [
            ElementRelationVersion(
                id=ids.relation_version_id(spec.slug),
                relation_id=ids.relation_id(spec.slug),
                version_no=1,
                source_element_id=ids.element_id(spec.source_slug),
                target_element_id=ids.element_id(spec.target_slug),
                relation_key=spec.relation_key,
                forward_label=spec.forward_label,
                reverse_label=spec.reverse_label,
                description=spec.description,
                metadata_={},
                status="active",
                change_reason="初始化非生产固定样例",
                created_by=user_id,
            )
            for spec in RELATION_SPECS
        ]
    )
    await db.flush()

    db.add(
        ForeshadowLifecycle(
            id=ids.foreshadow_lifecycle,
            project_id=ids.project,
            plan_id=ids.plan,
            element_id=ids.foreshadow_element,
            status="active",
            lock_version=3,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    await db.flush()
    db.add_all(
        [
            ForeshadowPlanItem(
                id=ids.foreshadow_plan_id("plant"),
                project_id=ids.project,
                plan_id=ids.plan,
                lifecycle_id=ids.foreshadow_lifecycle,
                action_kind="plant",
                target_type="chapter",
                target_id=ids.chapter,
                chapter_id=ids.chapter,
                condition_text="",
                note=FORESHADOW_PLANT_NOTE,
                status="active",
                lock_version=1,
                created_by=user_id,
                updated_by=user_id,
            ),
            ForeshadowPlanItem(
                id=ids.foreshadow_plan_id("resolve"),
                project_id=ids.project,
                plan_id=ids.plan,
                lifecycle_id=ids.foreshadow_lifecycle,
                action_kind="resolve",
                target_type="chapter",
                target_id=ids.second_chapter,
                chapter_id=ids.second_chapter,
                condition_text=FORESHADOW_RESOLVE_CONDITION,
                note=FORESHADOW_RESOLVE_NOTE,
                status="active",
                lock_version=1,
                created_by=user_id,
                updated_by=user_id,
            ),
        ]
    )
    await db.flush()
    event_specs = (
        ("create", "create", None, 0, 1),
        ("plant", "plan_create", ids.foreshadow_plan_id("plant"), 1, 2),
        ("resolve", "plan_create", ids.foreshadow_plan_id("resolve"), 2, 3),
    )
    db.add_all(
        [
            ForeshadowLifecycleEvent(
                id=ids.foreshadow_event_id(slug),
                project_id=ids.project,
                lifecycle_id=ids.foreshadow_lifecycle,
                performed_by=user_id,
                event_kind=kind,
                plan_item_id=plan_item_id,
                fact_id=None,
                previous_lifecycle_version=previous,
                new_lifecycle_version=current,
                metadata_json={"fixture_version": 1},
                created_at=datetime(2026, 1, 1, 0, 0, current, tzinfo=UTC).replace(
                    tzinfo=None
                ),
            )
            for slug, kind, plan_item_id, previous, current in event_specs
        ]
    )
