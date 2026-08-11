"""Exactly-once, zero-LLM preparation for relational chapter generation."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Any
import uuid

from pydantic import ValidationError
from sqlalchemy import or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.maintenance import ensure_project_writes_available
from app.core.planning_assignment import ineligible_reasons
from app.core.planning_write import operation_fingerprint
from app.models.generation import ChapterGenerationRun
from app.models.lore import (
    ElementRelation,
    ElementRelationVersion,
    ElementVersion,
    SettingElement,
    SettingType,
)
from app.models.planning import (
    NovelPlan,
    PlanningChapter,
    PlanningLoreAssignment,
    PlanningPart,
)
from app.models.project import Project
from app.schemas.generation import GenerationContextManifest, GenerationRunResponse


CONTEXT_SCHEMA_VERSION = 1
MAX_CONTEXT_BYTES = 65_536
MAX_CONTEXT_ELEMENTS = 100
MAX_CONTEXT_RELATIONS = 300
_OPERATION_TYPE = "generation_prepare"


class GenerationPreparationError(Exception):
    """Stable failure for preparation without any paid model call."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        retryable: bool = False,
        recommended_action: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = {
            "code": code,
            "message": message,
            "retryable": retryable,
            "recommended_action": recommended_action,
            **(extra or {}),
        }


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _canonical_json(value: dict[str, Any]) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise GenerationPreparationError(
            "GENERATION_CONTEXT_INVALID",
            "生成上下文包含无法安全保存的数据。",
            recommended_action="review_lore_repository",
        ) from exc
    return text.encode("utf-8")


def _fingerprint(
    project_id: str,
    chapter_id: str,
    *,
    expected_structure_version: int,
    expected_assignment_version: int,
    expected_chapter_lock_version: int,
) -> str:
    return operation_fingerprint(
        project_id,
        _OPERATION_TYPE,
        chapter_id,
        {
            "expected_structure_version": expected_structure_version,
            "expected_assignment_version": expected_assignment_version,
            "expected_chapter_lock_version": expected_chapter_lock_version,
            "context_schema_version": CONTEXT_SCHEMA_VERSION,
        },
    )


async def find_generation_run_by_key(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    operation_key: str,
) -> ChapterGenerationRun | None:
    return await db.scalar(
        select(ChapterGenerationRun).where(
            ChapterGenerationRun.project_id == project_id,
            ChapterGenerationRun.requested_by == user_id,
            ChapterGenerationRun.operation_key == operation_key,
        )
    )


def generation_run_response(
    run: ChapterGenerationRun,
    *,
    replayed: bool,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    if expected_fingerprint is not None and run.request_fingerprint != expected_fingerprint:
        raise GenerationPreparationError(
            "GENERATION_OPERATION_KEY_REUSED",
            "该操作编号已用于不同的生成准备请求，系统没有重复写入。",
            recommended_action="retry_with_new_operation_key",
        )
    try:
        manifest = GenerationContextManifest.model_validate(run.context_manifest)
    except ValidationError as exc:
        raise GenerationPreparationError(
            "GENERATION_RUN_CORRUPT",
            "生成准备记录不完整，系统已停止自动处理。",
            recommended_action="contact_support",
        ) from exc
    manifest_snapshot = manifest.model_dump(mode="json")
    canonical = _canonical_json(manifest_snapshot)
    manifest_versions = manifest.versions
    if (
        manifest.schema_version != run.context_schema_version
        or manifest.project_id != run.project_id
        or manifest.plan_id != run.plan_id
        or manifest.chapter.id != run.planning_chapter_id
        or manifest_versions.structure != run.structure_version
        or manifest_versions.assignment != run.assignment_version
        or manifest_versions.chapter_lock != run.chapter_lock_version
    ):
        raise GenerationPreparationError(
            "GENERATION_RUN_CORRUPT",
            "生成准备记录不完整，系统已停止自动处理。",
            recommended_action="contact_support",
        )
    checksum = hashlib.sha256(canonical).hexdigest()
    if (
        run.status != "prepared"
        or run.execution_mode != "preflight_only"
        or run.ai_invoked is not False
        or run.billing_effect != "none"
        or run.context_schema_version != CONTEXT_SCHEMA_VERSION
        or checksum != run.context_checksum
        or len(canonical) != run.context_size_bytes
    ):
        raise GenerationPreparationError(
            "GENERATION_RUN_CORRUPT",
            "生成准备记录不完整，系统已停止自动处理。",
            recommended_action="contact_support",
        )
    snapshot = {
        "id": run.id,
        "project_id": run.project_id,
        "plan_id": run.plan_id,
        "planning_chapter_id": run.planning_chapter_id,
        "operation_key": run.operation_key,
        "replayed": replayed,
        "status": run.status,
        "execution_mode": run.execution_mode,
        "ai_invoked": run.ai_invoked,
        "billing_effect": run.billing_effect,
        "structure_version": run.structure_version,
        "assignment_version": run.assignment_version,
        "chapter_lock_version": run.chapter_lock_version,
        "context_schema_version": run.context_schema_version,
        "context_manifest": manifest_snapshot,
        "context_checksum": run.context_checksum,
        "context_size_bytes": run.context_size_bytes,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }
    return GenerationRunResponse.model_validate(snapshot).model_dump(mode="json")


def _version_conflict(code: str, message: str, current: int) -> GenerationPreparationError:
    return GenerationPreparationError(
        code,
        message,
        recommended_action="refresh_generation_preflight",
        extra={"current_version": current},
    )


async def _load_locked_scope(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    chapter_id: str,
    *,
    expected_structure_version: int,
    expected_assignment_version: int,
    expected_chapter_lock_version: int,
) -> tuple[NovelPlan, PlanningPart, PlanningChapter]:
    # Keep the project identity stable without conflicting with the key-share lock
    # PostgreSQL takes when Lore rows reference their parent project.
    project = await db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .with_for_update(read=True, key_share=True)
    )
    if project is None:
        raise GenerationPreparationError(
            "GENERATION_PROJECT_NOT_FOUND",
            "项目不存在。",
            status_code=404,
            recommended_action="return_to_projects",
        )
    if project.owner_id != user_id:
        raise GenerationPreparationError(
            "GENERATION_PROJECT_FORBIDDEN",
            "无权操作此项目。",
            status_code=403,
            recommended_action="return_to_projects",
        )
    if project.lore_storage_mode != "relational":
        raise GenerationPreparationError(
            "GENERATION_LORE_MIGRATION_REQUIRED",
            "请先将旧世界观安全升级为设定仓库。",
            recommended_action="open_lore_repository",
        )

    plan = await db.scalar(
        select(NovelPlan)
        .where(NovelPlan.project_id == project_id)
        .with_for_update()
    )
    if plan is None:
        raise GenerationPreparationError(
            "GENERATION_PLANNING_NOT_INITIALIZED",
            "章节规划尚未创建。",
            status_code=404,
            recommended_action="initialize_planning",
        )
    if plan.status != "active":
        raise GenerationPreparationError(
            "GENERATION_SCOPE_ARCHIVED",
            "章节规划已归档，不能创建生成准备。",
            recommended_action="restore_scope",
        )
    if plan.structure_version != expected_structure_version:
        raise _version_conflict(
            "GENERATION_STRUCTURE_VERSION_CONFLICT",
            "章节结构已更新，请刷新后重新检查生成准备。",
            plan.structure_version,
        )
    if plan.assignment_version != expected_assignment_version:
        raise _version_conflict(
            "GENERATION_ASSIGNMENT_VERSION_CONFLICT",
            "设定分配已更新，请刷新后重新检查生成准备。",
            plan.assignment_version,
        )

    parent_id = await db.scalar(
        select(PlanningChapter.part_id).where(
            PlanningChapter.project_id == project_id,
            PlanningChapter.plan_id == plan.id,
            PlanningChapter.id == chapter_id,
        )
    )
    if parent_id is None:
        raise GenerationPreparationError(
            "GENERATION_CHAPTER_NOT_FOUND",
            "章节不存在或不属于当前项目。",
            status_code=404,
            recommended_action="refresh_planning",
        )
    part = await db.scalar(
        select(PlanningPart)
        .where(
            PlanningPart.project_id == project_id,
            PlanningPart.plan_id == plan.id,
            PlanningPart.id == parent_id,
        )
        .with_for_update()
    )
    chapter = await db.scalar(
        select(PlanningChapter)
        .where(
            PlanningChapter.project_id == project_id,
            PlanningChapter.plan_id == plan.id,
            PlanningChapter.id == chapter_id,
            PlanningChapter.part_id == parent_id,
        )
        .with_for_update()
    )
    if part is None or chapter is None:
        raise GenerationPreparationError(
            "GENERATION_CHAPTER_NOT_FOUND",
            "章节结构不完整，系统已停止自动处理。",
            status_code=404,
            recommended_action="refresh_planning",
        )
    if part.status != "active" or chapter.status != "active":
        raise GenerationPreparationError(
            "GENERATION_SCOPE_ARCHIVED",
            "篇章或章节已归档，不能创建生成准备。",
            recommended_action="restore_scope",
        )
    if chapter.lock_version != expected_chapter_lock_version:
        raise _version_conflict(
            "GENERATION_CHAPTER_VERSION_CONFLICT",
            "章节内容计划已更新，请刷新后重新检查生成准备。",
            chapter.lock_version,
        )
    return plan, part, chapter


async def _build_context_manifest(
    db: AsyncSession,
    plan: NovelPlan,
    part: PlanningPart,
    chapter: PlanningChapter,
) -> tuple[dict[str, Any], bytes]:
    source_condition = or_(
        (
            (PlanningLoreAssignment.scope_type == "novel")
            & (PlanningLoreAssignment.scope_target_id == plan.project_id)
        ),
        (
            (PlanningLoreAssignment.scope_type == "part")
            & (PlanningLoreAssignment.scope_target_id == part.id)
        ),
        (
            (PlanningLoreAssignment.scope_type == "chapter")
            & (PlanningLoreAssignment.scope_target_id == chapter.id)
        ),
    )
    assignments = list(
        (
            await db.scalars(
                select(PlanningLoreAssignment)
                .where(
                    PlanningLoreAssignment.project_id == plan.project_id,
                    PlanningLoreAssignment.plan_id == plan.id,
                    PlanningLoreAssignment.status == "active",
                    source_condition,
                )
                .order_by(
                    PlanningLoreAssignment.element_id,
                    PlanningLoreAssignment.scope_type,
                    PlanningLoreAssignment.id,
                )
                .with_for_update()
            )
        ).all()
    )
    element_ids = sorted({item.element_id for item in assignments})
    if not element_ids:
        raise GenerationPreparationError(
            "GENERATION_CONTEXT_EMPTY",
            "本章没有可用的正式设定，请先分配设定再检查。",
            status_code=422,
            recommended_action="add_chapter_lore",
        )
    if len(element_ids) > MAX_CONTEXT_ELEMENTS:
        raise GenerationPreparationError(
            "GENERATION_CONTEXT_TOO_LARGE",
            "本章设定数量超过安全上限，系统没有截断或保存上下文。",
            status_code=413,
            recommended_action="reduce_chapter_lore",
            extra={
                "counts": {"elements": len(element_ids)},
                "limits": {"elements": MAX_CONTEXT_ELEMENTS},
            },
        )

    elements = list(
        (
            await db.scalars(
                select(SettingElement)
                .where(
                    SettingElement.project_id == plan.project_id,
                    SettingElement.id.in_(element_ids),
                )
                .order_by(SettingElement.id)
                .with_for_update()
            )
        ).all()
    )
    if [item.id for item in elements] != element_ids:
        raise GenerationPreparationError(
            "GENERATION_CONTEXT_INCOMPLETE",
            "设定引用不完整，系统已停止自动处理。",
            recommended_action="refresh_lore_repository",
        )
    type_ids = sorted({item.type_id for item in elements})
    setting_types = list(
        (
            await db.scalars(
                select(SettingType)
                .where(
                    SettingType.project_id == plan.project_id,
                    SettingType.id.in_(type_ids),
                )
                .order_by(SettingType.id)
            )
        ).all()
    )
    type_by_id = {item.id: item for item in setting_types}
    ineligible: list[dict[str, Any]] = []
    for element in elements:
        setting_type = type_by_id.get(element.type_id)
        reasons = (
            ["type_missing"]
            if setting_type is None
            else ineligible_reasons(element, setting_type)
        )
        if reasons:
            ineligible.append({"element_id": element.id, "reasons": reasons})
    if ineligible:
        raise GenerationPreparationError(
            "GENERATION_LORE_INELIGIBLE",
            "本章存在当前不可生成的已分配设定，请先处理后重新检查。",
            recommended_action="review_lore_assignments",
            extra={"ineligible_elements": ineligible},
        )

    version_pairs = [(item.id, item.content_version) for item in elements]
    versions = list(
        (
            await db.scalars(
                select(ElementVersion)
                .where(
                    tuple_(ElementVersion.element_id, ElementVersion.version_no).in_(
                        version_pairs
                    )
                )
                .order_by(ElementVersion.element_id)
            )
        ).all()
    )
    version_by_element = {item.element_id: item for item in versions}
    if len(version_by_element) != len(elements):
        raise GenerationPreparationError(
            "GENERATION_CONTEXT_INCOMPLETE",
            "设定的当前不可变版本缺失，系统已停止自动处理。",
            recommended_action="contact_support",
        )
    for element in elements:
        version = version_by_element[element.id]
        if (
            version.element_id != element.id
            or version.version_no != element.content_version
            or version.type_id != element.type_id
            or version.type_schema_revision != element.payload_schema_revision
            or version.name != element.name
            or (version.summary or "") != (element.summary or "")
            or dict(version.payload or {}) != dict(element.payload or {})
            or dict(version.field_states or {}) != dict(element.field_states or {})
        ):
            raise GenerationPreparationError(
                "GENERATION_CONTEXT_INCOMPLETE",
                "设定当前版本与正式设定不一致，系统已停止自动处理。",
                recommended_action="contact_support",
            )

    relations = list(
        (
            await db.scalars(
                select(ElementRelation)
                .where(
                    ElementRelation.project_id == plan.project_id,
                    ElementRelation.status == "active",
                    ElementRelation.source_element_id.in_(element_ids),
                    ElementRelation.target_element_id.in_(element_ids),
                )
                .order_by(ElementRelation.id)
                .with_for_update()
            )
        ).all()
    )
    if len(relations) > MAX_CONTEXT_RELATIONS:
        raise GenerationPreparationError(
            "GENERATION_CONTEXT_TOO_LARGE",
            "本章设定关系数量超过安全上限，系统没有截断或保存上下文。",
            status_code=413,
            recommended_action="reduce_chapter_lore",
            extra={
                "counts": {
                    "elements": len(element_ids),
                    "relations": len(relations),
                },
                "limits": {
                    "elements": MAX_CONTEXT_ELEMENTS,
                    "relations": MAX_CONTEXT_RELATIONS,
                },
            },
        )
    relation_pairs = [(item.id, item.version_no) for item in relations]
    relation_versions = list(
        (
            await db.scalars(
                select(ElementRelationVersion)
                .where(
                    tuple_(
                        ElementRelationVersion.relation_id,
                        ElementRelationVersion.version_no,
                    ).in_(relation_pairs)
                )
                .order_by(ElementRelationVersion.relation_id)
            )
        ).all()
    ) if relation_pairs else []
    relation_version_by_id = {item.relation_id: item for item in relation_versions}
    if len(relation_version_by_id) != len(relations):
        raise GenerationPreparationError(
            "GENERATION_CONTEXT_INCOMPLETE",
            "设定关系的当前不可变版本缺失，系统已停止自动处理。",
            recommended_action="contact_support",
        )
    for relation in relations:
        version = relation_version_by_id[relation.id]
        if (
            version.relation_id != relation.id
            or version.version_no != relation.version_no
            or version.source_element_id != relation.source_element_id
            or version.target_element_id != relation.target_element_id
            or version.relation_key != relation.relation_key
            or version.forward_label != relation.forward_label
            or version.reverse_label != relation.reverse_label
            or (version.description or "") != (relation.description or "")
            or dict(version.metadata_ or {}) != dict(relation.metadata_ or {})
            or version.status != relation.status
        ):
            raise GenerationPreparationError(
                "GENERATION_CONTEXT_INCOMPLETE",
                "设定关系当前版本与正式关系不一致，系统已停止自动处理。",
                recommended_action="contact_support",
            )

    assignments_by_element: dict[str, list[PlanningLoreAssignment]] = {}
    for assignment in assignments:
        assignments_by_element.setdefault(assignment.element_id, []).append(assignment)
    scope_order = {"novel": 0, "part": 1, "chapter": 2}
    scope_titles = {
        ("novel", plan.project_id): "整部小说",
        ("part", part.id): part.title,
        ("chapter", chapter.id): chapter.title,
    }
    warnings: list[dict[str, Any]] = []
    if not chapter.summary.strip():
        warnings.append({"code": "CHAPTER_SUMMARY_EMPTY"})

    element_snapshots: list[dict[str, Any]] = []
    for element in elements:
        setting_type = type_by_id[element.type_id]
        version = version_by_element[element.id]
        sources = sorted(
            assignments_by_element[element.id],
            key=lambda item: (scope_order[item.scope_type], item.id),
        )
        if any(item.element_content_version != element.content_version for item in sources):
            warnings.append(
                {
                    "code": "LORE_CHANGED_SINCE_ASSIGNMENT",
                    "element_id": element.id,
                }
            )
        element_snapshots.append(
            {
                "element_id": element.id,
                "type": {
                    "id": setting_type.id,
                    "key": setting_type.key,
                    "display_name": setting_type.display_name,
                    "schema_revision": version.type_schema_revision,
                },
                "version": {
                    "id": version.id,
                    "element_id": version.element_id,
                    "type_id": version.type_id,
                    "version_no": version.version_no,
                    "name": version.name,
                    "summary": version.summary or "",
                    "payload": version.payload or {},
                    "field_states": version.field_states or {},
                    "source_id": version.source_id,
                },
                "assignment_sources": [
                    {
                        "assignment_id": item.id,
                        "scope_type": item.scope_type,
                        "scope_target_id": item.scope_target_id,
                        "scope_title": scope_titles[
                            (item.scope_type, item.scope_target_id)
                        ],
                        "assignment_lock_version": item.lock_version,
                        "assigned_at_content_version": item.element_content_version,
                    }
                    for item in sources
                ],
            }
        )

    relation_snapshots = []
    for relation in relations:
        version = relation_version_by_id[relation.id]
        relation_snapshots.append(
            {
                "relation_id": relation.id,
                "version": {
                    "id": version.id,
                    "relation_id": version.relation_id,
                    "version_no": version.version_no,
                    "source_element_id": version.source_element_id,
                    "target_element_id": version.target_element_id,
                    "relation_key": version.relation_key,
                    "forward_label": version.forward_label,
                    "reverse_label": version.reverse_label,
                    "description": version.description or "",
                    "metadata": version.metadata_ or {},
                    "status": version.status,
                },
            }
        )

    manifest = {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "project_id": plan.project_id,
        "plan_id": plan.id,
        "versions": {
            "structure": plan.structure_version,
            "assignment": plan.assignment_version,
            "chapter_lock": chapter.lock_version,
        },
        "part": {
            "id": part.id,
            "title": part.title,
            "description": part.description or "",
            "position": part.position,
            "lock_version": part.lock_version,
        },
        "chapter": {
            "id": chapter.id,
            "title": chapter.title,
            "summary": chapter.summary or "",
            "target_word_count": chapter.target_word_count,
            "position": chapter.position,
            "lock_version": chapter.lock_version,
        },
        "elements": element_snapshots,
        "relations": relation_snapshots,
        "foreshadow_actions": {
            "supported": False,
            "items": [],
        },
        "warnings": warnings,
        "counts": {
            "elements": len(element_snapshots),
            "relations": len(relation_snapshots),
            "warnings": len(warnings),
        },
    }
    try:
        validated_manifest = GenerationContextManifest.model_validate(manifest)
    except ValidationError as exc:
        raise GenerationPreparationError(
            "GENERATION_CONTEXT_INVALID",
            "生成上下文结构不完整，系统没有保存。",
            recommended_action="contact_support",
        ) from exc
    manifest = validated_manifest.model_dump(mode="json")
    canonical = _canonical_json(manifest)
    if len(canonical) > MAX_CONTEXT_BYTES:
        raise GenerationPreparationError(
            "GENERATION_CONTEXT_TOO_LARGE",
            "本章生成上下文超过安全大小，系统没有截断或保存。",
            status_code=413,
            recommended_action="reduce_chapter_lore",
            extra={
                "counts": manifest["counts"],
                "context_size_bytes": len(canonical),
                "limits": {
                    "context_size_bytes": MAX_CONTEXT_BYTES,
                    "elements": MAX_CONTEXT_ELEMENTS,
                    "relations": MAX_CONTEXT_RELATIONS,
                },
            },
        )
    return manifest, canonical


async def prepare_generation_run(
    *,
    db: AsyncSession,
    project_id: str,
    user_id: str,
    chapter_id: str,
    operation_key: str,
    expected_structure_version: int,
    expected_assignment_version: int,
    expected_chapter_lock_version: int,
) -> dict[str, Any]:
    request_fingerprint = _fingerprint(
        project_id,
        chapter_id,
        expected_structure_version=expected_structure_version,
        expected_assignment_version=expected_assignment_version,
        expected_chapter_lock_version=expected_chapter_lock_version,
    )
    existing = await find_generation_run_by_key(
        db, project_id, user_id, operation_key
    )
    if existing is not None:
        return generation_run_response(
            existing, replayed=True, expected_fingerprint=request_fingerprint
        )

    try:
        ensure_project_writes_available()
        plan, part, chapter = await _load_locked_scope(
            db,
            project_id,
            user_id,
            chapter_id,
            expected_structure_version=expected_structure_version,
            expected_assignment_version=expected_assignment_version,
            expected_chapter_lock_version=expected_chapter_lock_version,
        )
        existing = await find_generation_run_by_key(
            db, project_id, user_id, operation_key
        )
        if existing is not None:
            response = generation_run_response(
                existing, replayed=True, expected_fingerprint=request_fingerprint
            )
            await db.rollback()
            return response

        manifest, canonical = await _build_context_manifest(db, plan, part, chapter)
        now = _utcnow()
        run = ChapterGenerationRun(
            id=uuid.uuid4().hex,
            project_id=project_id,
            plan_id=plan.id,
            planning_chapter_id=chapter.id,
            requested_by=user_id,
            operation_key=operation_key,
            request_fingerprint=request_fingerprint,
            status="prepared",
            execution_mode="preflight_only",
            ai_invoked=False,
            billing_effect="none",
            structure_version=plan.structure_version,
            assignment_version=plan.assignment_version,
            chapter_lock_version=chapter.lock_version,
            context_schema_version=CONTEXT_SCHEMA_VERSION,
            context_manifest=manifest,
            context_checksum=hashlib.sha256(canonical).hexdigest(),
            context_size_bytes=len(canonical),
            created_at=now,
            updated_at=now,
        )
        db.add(run)
        await db.flush()
        ensure_project_writes_available()
        await db.commit()
        return generation_run_response(run, replayed=False)
    except GenerationPreparationError:
        await db.rollback()
        raise
    except IntegrityError as exc:
        await db.rollback()
        existing = await find_generation_run_by_key(
            db, project_id, user_id, operation_key
        )
        if existing is not None:
            return generation_run_response(
                existing, replayed=True, expected_fingerprint=request_fingerprint
            )
        raise GenerationPreparationError(
            "GENERATION_PREPARE_CONFLICT",
            "生成准备发生并发冲突，请刷新后安全重试。",
            retryable=True,
            recommended_action="refresh_generation_preflight",
        ) from exc
    except Exception:
        await db.rollback()
        raise
