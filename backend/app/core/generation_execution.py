"""At-most-once execution for prepared relational chapter generation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
import re
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit
import uuid

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.generation_preflight import (
    GenerationPreparationError,
    generation_run_response,
)
from app.core.llm_client import (
    LLMFrozenSingleCallConfig,
    LLMSingleCallError,
    llm_client,
)
from app.core.maintenance import ensure_project_writes_available
from app.core.planning_write import operation_fingerprint
from app.core.settings_store import load_settings
from app.models.generation import (
    ChapterGenerationAttempt,
    ChapterGenerationCandidate,
    ChapterGenerationRun,
)
from app.models.planning import NovelPlan, PlanningChapter, PlanningPart
from app.models.project import Project
from app.schemas.generation import (
    GenerationAttemptResponse,
    GenerationCandidateResponse,
    GenerationCapabilityResponse,
    GenerationCapabilitySnapshot,
)


PROMPT_SCHEMA_VERSION = 1
CAPABILITY_SCHEMA_VERSION = 1
MAX_CANDIDATE_BYTES = 262_144
_OPERATION_TYPE = "generation_execute"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class GenerationExecutionError(Exception):
    """Stable public failure that never exposes prompt, content, or credentials."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        retryable: bool = False,
        recommended_action: str,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = {
            "code": code,
            "message": message,
            "retryable": retryable,
            "recommended_action": recommended_action,
        }


class GenerationTransport(Protocol):
    """Injectable single-call boundary used by API code and counting fakes."""

    model_name: str
    capability_snapshot: dict[str, Any]
    capability_checksum: str
    execution_config_digest: str

    def ensure_ready(self) -> None: ...

    def verify_capability_current(self) -> None: ...

    async def generate(
        self, messages: list[dict[str, str]]
    ) -> GenerationTransportResult | str: ...


@dataclass(frozen=True)
class GenerationUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class GenerationTransportResult:
    content: str
    usage: GenerationUsage | None = None


class SingleCallGenerationTransport:
    """Production adapter that delegates once to the no-retry LLM client."""

    def __init__(self) -> None:
        snapshot = load_settings()
        self._capability_error: GenerationExecutionError | None = None
        try:
            self._frozen_config = _frozen_execution_config(snapshot)
            self.model_name = self._frozen_config.model
            self.execution_config_digest = _execution_config_digest(
                self._frozen_config
            )
            self.capability_snapshot = _capability_snapshot(
                self._frozen_config
            )
            self.capability_checksum = _capability_checksum(
                self.capability_snapshot,
                self.execution_config_digest,
            )
        except GenerationExecutionError as exc:
            self._capability_error = exc
            self._frozen_config = None
            self.model_name = ""
            self.execution_config_digest = ""
            self.capability_snapshot = {}
            self.capability_checksum = ""

    def ensure_ready(self) -> None:
        if self._capability_error is not None:
            raise self._capability_error
        if self._frozen_config is None or not self._frozen_config.api_key:
            raise GenerationExecutionError(
                "LLM_NOT_CONFIGURED",
                "LLM 尚未配置，系统未发起模型调用。",
                status_code=422,
                recommended_action="configure_model",
            )

    def verify_capability_current(self) -> None:
        try:
            current_digest = _execution_config_digest(
                _frozen_execution_config(load_settings())
            )
        except GenerationExecutionError as exc:
            raise GenerationExecutionError(
                "LLM_CONFIGURATION_CHANGED",
                "LLM 配置已变化，未发起本次调用。",
                recommended_action="refresh_generation_capability",
            ) from exc
        if current_digest != self.execution_config_digest:
            raise GenerationExecutionError(
                "LLM_CONFIGURATION_CHANGED",
                "LLM 配置已变化，未发起本次调用。",
                recommended_action="refresh_generation_capability",
            )

    async def generate(self, messages: list[dict[str, str]]) -> str:
        self.verify_capability_current()
        if self._frozen_config is None:
            self.ensure_ready()
            raise AssertionError("unreachable")
        return await llm_client.chat_once_frozen(
            messages, config=self._frozen_config
        )


def get_generation_transport() -> GenerationTransport:
    return SingleCallGenerationTransport()


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def execution_request_fingerprint(
    project_id: str,
    run_id: str,
    expected_context_checksum: str,
    expected_capability_checksum: str,
) -> str:
    return operation_fingerprint(
        project_id,
        _OPERATION_TYPE,
        run_id,
        {
            "expected_context_checksum": expected_context_checksum,
            "expected_capability_checksum": expected_capability_checksum,
            "confirm_model_call": True,
            "prompt_schema_version": PROMPT_SCHEMA_VERSION,
        },
    )


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GenerationExecutionError(
            "GENERATION_PROMPT_INVALID",
            "生成准备内容无法安全构建提示词。",
            recommended_action="refresh_generation_preflight",
        ) from exc


def _normalize_base_url(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url.strip())
        hostname = (parsed.hostname or "").lower()
        scheme = parsed.scheme.lower()
        port = parsed.port
    except ValueError as exc:
        raise GenerationExecutionError(
            "LLM_CONFIGURATION_INVALID",
            "LLM 服务商配置无法安全使用。",
            status_code=422,
            recommended_action="configure_model",
        ) from exc
    if (
        not hostname
        or scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise GenerationExecutionError(
            "LLM_CONFIGURATION_INVALID",
            "LLM 服务商配置无法安全使用。",
            status_code=422,
            recommended_action="configure_model",
        )
    default_port = 443 if scheme == "https" else 80
    host_for_url = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host_for_url if port in {None, default_port} else f"{host_for_url}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def _provider_identifier(base_url: str) -> str:
    parsed = urlsplit(base_url)
    hostname = (parsed.hostname or "").lower()
    scheme = parsed.scheme.lower()
    port = parsed.port
    known = {
        "api.deepseek.com": "deepseek",
        "api.openai.com": "openai",
    }
    if hostname in known:
        return known[hostname]
    origin = f"{scheme}://{hostname}:{port or ('443' if scheme == 'https' else '80')}"
    return f"openai_compatible_{hashlib.sha256(origin.encode()).hexdigest()[:12]}"


def _frozen_execution_config(
    settings: dict[str, Any],
) -> LLMFrozenSingleCallConfig:
    api_key_value = settings.get("api_key")
    api_key = api_key_value if isinstance(api_key_value, str) else ""
    model_name = str(settings.get("model") or "").strip()
    raw_max_tokens = settings.get("max_tokens")
    if isinstance(raw_max_tokens, bool):
        raw_max_tokens = None
    try:
        max_output_tokens = int(raw_max_tokens)
    except (TypeError, ValueError):
        max_output_tokens = 0
    raw_temperature = settings.get("temperature")
    if isinstance(raw_temperature, bool):
        raw_temperature = None
    try:
        temperature = float(raw_temperature)
    except (TypeError, ValueError):
        temperature = math.nan
    base_url = _normalize_base_url(
        str(settings.get("base_url") or "")
    )
    if (
        not api_key
        or not model_name
        or not 1 <= max_output_tokens <= 1_000_000
        or not math.isfinite(temperature)
        or not 0 <= temperature <= 2
    ):
        raise GenerationExecutionError(
            "LLM_CONFIGURATION_INVALID",
            "LLM 能力配置无法安全使用。",
            status_code=422,
            recommended_action="configure_model",
        )
    return LLMFrozenSingleCallConfig(
        api_key=api_key,
        base_url=base_url,
        model=model_name,
        max_tokens=max_output_tokens,
        temperature=temperature,
    )


def _execution_config_digest(config: LLMFrozenSingleCallConfig) -> str:
    credential_identity = hashlib.sha256(config.api_key.encode("utf-8")).hexdigest()
    return hashlib.sha256(
        _canonical_json(
            {
                "credential_identity": credential_identity,
                "base_url": config.base_url,
                "model": config.model,
                "max_tokens": config.max_tokens,
                "temperature": config.temperature,
            }
        )
    ).hexdigest()


def _capability_snapshot(config: LLMFrozenSingleCallConfig) -> dict[str, Any]:
    snapshot = {
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "provider_name": _provider_identifier(config.base_url),
        "model_name": config.model,
        "max_output_tokens": config.max_tokens,
        "input_limit_availability": "unavailable",
        "max_input_tokens": None,
        # No trusted pricing catalog exists in the current settings source. The
        # API must disclose that fact instead of fabricating an estimate.
        "price_availability": "unavailable",
    }
    try:
        return GenerationCapabilitySnapshot.model_validate(snapshot).model_dump(
            mode="json"
        )
    except ValidationError as exc:
        raise GenerationExecutionError(
            "LLM_CONFIGURATION_INVALID",
            "LLM 能力配置无法安全使用。",
            status_code=422,
            recommended_action="configure_model",
        ) from exc


def _capability_checksum(
    snapshot: dict[str, Any], execution_config_digest: str
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "capability": snapshot,
                "execution_config_digest": execution_config_digest,
            }
        )
    ).hexdigest()


def generation_capability_response(
    transport: GenerationTransport,
) -> dict[str, Any]:
    transport.ensure_ready()
    try:
        snapshot = GenerationCapabilitySnapshot.model_validate(
            transport.capability_snapshot
        ).model_dump(mode="json")
    except (AttributeError, ValidationError) as exc:
        raise GenerationExecutionError(
            "LLM_CAPABILITY_CORRUPT",
            "LLM 能力快照不完整，系统已停止生成。",
            recommended_action="contact_support",
        ) from exc
    execution_config_digest = getattr(transport, "execution_config_digest", "")
    if not _HEX_64.fullmatch(execution_config_digest):
        raise GenerationExecutionError(
            "LLM_CAPABILITY_CORRUPT",
            "LLM 能力快照不完整，系统已停止生成。",
            recommended_action="contact_support",
        )
    checksum = _capability_checksum(snapshot, execution_config_digest)
    if transport.capability_checksum != checksum:
        raise GenerationExecutionError(
            "LLM_CAPABILITY_CORRUPT",
            "LLM 能力快照不完整，系统已停止生成。",
            recommended_action="contact_support",
        )
    return GenerationCapabilityResponse.model_validate(
        {**snapshot, "capability_checksum": checksum}
    ).model_dump(mode="json")


def _messages_for_run(run: ChapterGenerationRun) -> list[dict[str, str]]:
    try:
        receipt = generation_run_response(run, replayed=True)
    except GenerationPreparationError as exc:
        raise GenerationExecutionError(
            exc.detail["code"],
            exc.detail["message"],
            status_code=exc.status_code,
            retryable=bool(exc.detail.get("retryable")),
            recommended_action=exc.detail["recommended_action"],
        ) from exc
    manifest_json = _canonical_json(receipt["context_manifest"]).decode("utf-8")
    return [
        {
            "role": "system",
            "content": (
                "你是 AI 小说创作平台的章节草稿生成器。"
                "仅根据用户已确认的结构化上下文生成一个章节候选，"
                "不得宣称修改了正文或确认了伏笔事实。"
                "只返回章节正文纯文本，不返回 JSON 或解释。"
            ),
        },
        {
            "role": "user",
            "content": "请使用以下冻结生成上下文创作章节候选：\n" + manifest_json,
        },
    ]


def _prompt_checksum(messages: list[dict[str, str]]) -> str:
    return hashlib.sha256(_canonical_json(messages)).hexdigest()


def _word_count(content: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]", content))


async def find_generation_attempt_by_key(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    operation_key: str,
) -> ChapterGenerationAttempt | None:
    return await db.scalar(
        select(ChapterGenerationAttempt).where(
            ChapterGenerationAttempt.project_id == project_id,
            ChapterGenerationAttempt.requested_by == user_id,
            ChapterGenerationAttempt.operation_key == operation_key,
        )
    )


def _corrupt() -> GenerationExecutionError:
    return GenerationExecutionError(
        "GENERATION_ATTEMPT_CORRUPT",
        "生成执行记录不完整，系统已停止自动处理。",
        recommended_action="contact_support",
    )


async def generation_attempt_response(
    db: AsyncSession,
    attempt: ChapterGenerationAttempt,
    *,
    replayed: bool,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    if expected_fingerprint is not None and attempt.request_fingerprint != expected_fingerprint:
        raise GenerationExecutionError(
            "GENERATION_OPERATION_KEY_REUSED",
            "该操作编号已用于不同的生成请求，系统没有重复调用模型。",
            recommended_action="retry_with_new_operation_key",
        )
    run = await db.scalar(
        select(ChapterGenerationRun).where(
            ChapterGenerationRun.project_id == attempt.project_id,
            ChapterGenerationRun.id == attempt.run_id,
            ChapterGenerationRun.requested_by == attempt.requested_by,
        )
    )
    if run is None:
        raise _corrupt()
    messages = _messages_for_run(run)
    candidate = await db.scalar(
        select(ChapterGenerationCandidate).where(
            ChapterGenerationCandidate.project_id == attempt.project_id,
            ChapterGenerationCandidate.source_attempt_id == attempt.id,
        )
    )
    try:
        capability_snapshot = GenerationCapabilitySnapshot.model_validate(
            attempt.capability_snapshot
        ).model_dump(mode="json")
    except ValidationError as exc:
        raise _corrupt() from exc
    execution_config_digest = str(attempt.execution_config_digest or "")
    capability_checksum = _capability_checksum(
        capability_snapshot, execution_config_digest
    )
    valid_usage = (
        (
            attempt.usage_status == "reported"
            and attempt.input_tokens is not None
            and attempt.output_tokens is not None
            and attempt.total_tokens is not None
            and attempt.input_tokens >= 0
            and attempt.output_tokens >= 0
            and attempt.total_tokens
            == attempt.input_tokens + attempt.output_tokens
        )
        or (
            attempt.usage_status in {"unavailable", "unknown"}
            and attempt.input_tokens is None
            and attempt.output_tokens is None
            and attempt.total_tokens is None
        )
    )
    valid_state = (
        (
            attempt.status == "reserved"
            and attempt.ai_invoked is False
            and attempt.billing_effect == "none"
            and attempt.claimed_at is None
            and attempt.completed_at is None
            and attempt.error_code is None
        )
        or (
            attempt.status == "calling"
            and attempt.ai_invoked is True
            and attempt.billing_effect == "possible"
            and attempt.claimed_at is not None
            and attempt.completed_at is None
            and attempt.error_code is None
        )
        or (
            attempt.status == "succeeded"
            and attempt.ai_invoked is True
            and attempt.billing_effect == "possible"
            and attempt.claimed_at is not None
            and attempt.completed_at is not None
            and attempt.error_code is None
            and candidate is not None
        )
        or (
            attempt.status == "failed"
            and attempt.completed_at is not None
            and attempt.error_code is not None
            and candidate is None
            and (
                (
                    attempt.ai_invoked is False
                    and attempt.billing_effect == "none"
                    and attempt.claimed_at is None
                )
                or (
                    attempt.ai_invoked is True
                    and attempt.billing_effect == "possible"
                    and attempt.claimed_at is not None
                )
            )
        )
        or (
            attempt.status == "outcome_unknown"
            and attempt.ai_invoked is True
            and attempt.billing_effect == "possible"
            and attempt.claimed_at is not None
            and attempt.completed_at is not None
            and attempt.error_code is not None
            and candidate is None
        )
    )
    if (
        not valid_state
        or not valid_usage
        or attempt.execution_mode != "single_call"
        or attempt.billing_confirmed is not True
        or attempt.prompt_schema_version != PROMPT_SCHEMA_VERSION
        or not attempt.model_name.strip()
        or attempt.capability_schema_version != CAPABILITY_SCHEMA_VERSION
        or not _HEX_64.fullmatch(execution_config_digest)
        or attempt.capability_checksum != capability_checksum
        or attempt.provider_name != capability_snapshot["provider_name"]
        or attempt.model_name != capability_snapshot["model_name"]
        or attempt.max_output_tokens != capability_snapshot["max_output_tokens"]
        or attempt.input_limit_availability
        != capability_snapshot["input_limit_availability"]
        or attempt.max_input_tokens != capability_snapshot["max_input_tokens"]
        or attempt.price_availability
        != capability_snapshot["price_availability"]
        or not _HEX_64.fullmatch(attempt.request_fingerprint)
        or attempt.context_checksum != run.context_checksum
        or attempt.prompt_checksum != _prompt_checksum(messages)
        or (
            attempt.status == "reserved"
            and attempt.usage_status != "unavailable"
        )
        or (
            attempt.status in {"calling", "outcome_unknown"}
            and attempt.usage_status != "unknown"
        )
        or (
            attempt.status == "failed"
            and attempt.usage_status != "unavailable"
        )
        or (
            attempt.status == "succeeded"
            and attempt.usage_status not in {"reported", "unavailable"}
        )
    ):
        raise _corrupt()
    if attempt.status != "succeeded" and candidate is not None:
        raise _corrupt()
    if candidate is not None:
        content_bytes = candidate.content.encode("utf-8")
        manifest = generation_run_response(run, replayed=True)["context_manifest"]
        if (
            candidate.project_id != attempt.project_id
            or candidate.run_id != attempt.run_id
            or candidate.origin_kind != "generated"
            or candidate.parent_candidate_id is not None
            or candidate.created_by != attempt.requested_by
            or candidate.content_format != "plain_text"
            or candidate.content_size_bytes != len(content_bytes)
            or not 1 <= candidate.content_size_bytes <= MAX_CANDIDATE_BYTES
            or candidate.content_checksum != hashlib.sha256(content_bytes).hexdigest()
            or candidate.word_count != _word_count(candidate.content)
            or candidate.word_count < 1
            or candidate.version_no < 1
            or candidate.title != manifest["chapter"]["title"]
        ):
            raise _corrupt()
    error = None
    if attempt.error_code is not None:
        error = {
            "code": attempt.error_code,
            "message": attempt.error_message or "生成执行未完成。",
            "retryable": False,
            "recommended_action": (
                "keep_unknown_result"
                if attempt.status == "outcome_unknown"
                else "inspect_failure"
            ),
        }
    snapshot = {
        "id": attempt.id,
        "project_id": attempt.project_id,
        "run_id": attempt.run_id,
        "planning_chapter_id": run.planning_chapter_id,
        "operation_key": attempt.operation_key,
        "replayed": replayed,
        "status": attempt.status,
        "execution_mode": attempt.execution_mode,
        "billing_confirmed": attempt.billing_confirmed,
        "ai_invoked": attempt.ai_invoked,
        "billing_effect": attempt.billing_effect,
        "capability": {
            **capability_snapshot,
            "capability_checksum": attempt.capability_checksum,
        },
        "model_name": attempt.model_name,
        "prompt_schema_version": attempt.prompt_schema_version,
        "prompt_checksum": attempt.prompt_checksum,
        "context_checksum": attempt.context_checksum,
        "lock_version": attempt.lock_version,
        "usage": {
            "status": attempt.usage_status,
            "input_tokens": attempt.input_tokens,
            "output_tokens": attempt.output_tokens,
            "total_tokens": attempt.total_tokens,
        },
        "candidate_id": candidate.id if candidate is not None else None,
        "error": error,
        "claimed_at": attempt.claimed_at,
        "completed_at": attempt.completed_at,
        "created_at": attempt.created_at,
        "updated_at": attempt.updated_at,
    }
    try:
        return GenerationAttemptResponse.model_validate(snapshot).model_dump(mode="json")
    except ValidationError as exc:
        raise _corrupt() from exc


async def generation_candidate_response(
    db: AsyncSession,
    candidate: ChapterGenerationCandidate,
    *,
    user_id: str,
) -> dict[str, Any]:
    run = await db.scalar(
        select(ChapterGenerationRun).where(
            ChapterGenerationRun.project_id == candidate.project_id,
            ChapterGenerationRun.id == candidate.run_id,
            ChapterGenerationRun.requested_by == user_id,
        )
    )
    if run is None:
        raise _corrupt()
    try:
        manifest = generation_run_response(run, replayed=True)["context_manifest"]
    except GenerationPreparationError as exc:
        raise _corrupt() from exc
    content_bytes = candidate.content.encode("utf-8")
    if (
        candidate.project_id != run.project_id
        or candidate.run_id != run.id
        or candidate.created_by != user_id
        or candidate.content_format != "plain_text"
        or candidate.content_size_bytes != len(content_bytes)
        or not 1 <= candidate.content_size_bytes <= MAX_CANDIDATE_BYTES
        or candidate.content_checksum
        != hashlib.sha256(content_bytes).hexdigest()
        or candidate.word_count != _word_count(candidate.content)
        or candidate.word_count < 1
        or candidate.version_no < 1
    ):
        raise _corrupt()
    if candidate.origin_kind == "generated":
        if candidate.source_attempt_id is None or candidate.parent_candidate_id is not None:
            raise _corrupt()
        attempt = await db.scalar(
            select(ChapterGenerationAttempt).where(
                ChapterGenerationAttempt.project_id == candidate.project_id,
                ChapterGenerationAttempt.id == candidate.source_attempt_id,
                ChapterGenerationAttempt.run_id == candidate.run_id,
                ChapterGenerationAttempt.requested_by == user_id,
            )
        )
        if attempt is None or attempt.status != "succeeded":
            raise _corrupt()
        attempt_response = await generation_attempt_response(
            db, attempt, replayed=True
        )
        if attempt_response["candidate_id"] != candidate.id:
            raise _corrupt()
    elif candidate.origin_kind == "manual_edit":
        if candidate.source_attempt_id is not None or candidate.parent_candidate_id is None:
            raise _corrupt()
        parent = await db.scalar(
            select(ChapterGenerationCandidate).where(
                ChapterGenerationCandidate.project_id == candidate.project_id,
                ChapterGenerationCandidate.id == candidate.parent_candidate_id,
                ChapterGenerationCandidate.run_id == candidate.run_id,
            )
        )
        if parent is None:
            raise _corrupt()
    else:
        raise _corrupt()
    snapshot = {
        "id": candidate.id,
        "project_id": candidate.project_id,
        "run_id": candidate.run_id,
        "planning_chapter_id": run.planning_chapter_id,
        "source_attempt_id": candidate.source_attempt_id,
        "parent_candidate_id": candidate.parent_candidate_id,
        "version_no": candidate.version_no,
        "origin_kind": candidate.origin_kind,
        "title": candidate.title,
        "content": candidate.content,
        "content_format": candidate.content_format,
        "content_checksum": candidate.content_checksum,
        "content_size_bytes": candidate.content_size_bytes,
        "word_count": candidate.word_count,
        "created_by": candidate.created_by,
        "created_at": candidate.created_at,
    }
    if candidate.origin_kind == "generated" and candidate.title != manifest["chapter"]["title"]:
        raise _corrupt()
    try:
        return GenerationCandidateResponse.model_validate(snapshot).model_dump(
            mode="json"
        )
    except ValidationError as exc:
        raise _corrupt() from exc


async def _load_locked_execution_scope(
    db: AsyncSession,
    project_id: str,
    user_id: str,
    run_id: str,
) -> ChapterGenerationRun:
    run_identity = await db.scalar(
        select(ChapterGenerationRun).where(
            ChapterGenerationRun.project_id == project_id,
            ChapterGenerationRun.id == run_id,
            ChapterGenerationRun.requested_by == user_id,
        )
    )
    if run_identity is None:
        raise GenerationExecutionError(
            "GENERATION_RUN_NOT_FOUND",
            "未找到可执行的生成准备记录。",
            status_code=404,
            recommended_action="refresh_generation_preflight",
        )
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
    if project.lore_storage_mode != "relational":
        raise GenerationExecutionError(
            "GENERATION_LORE_MIGRATION_REQUIRED",
            "请先将旧世界观安全升级为设定仓库。",
            recommended_action="open_lore_repository",
        )
    plan = await db.scalar(
        select(NovelPlan)
        .where(
            NovelPlan.project_id == project_id,
            NovelPlan.id == run_identity.plan_id,
        )
        .with_for_update()
    )
    chapter_identity = await db.scalar(
        select(PlanningChapter).where(
            PlanningChapter.project_id == project_id,
            PlanningChapter.plan_id == run_identity.plan_id,
            PlanningChapter.id == run_identity.planning_chapter_id,
        )
    )
    if plan is None or chapter_identity is None:
        raise GenerationExecutionError(
            "GENERATION_SCOPE_CORRUPT",
            "生成目标结构不完整，系统已停止执行。",
            recommended_action="contact_support",
        )
    part = await db.scalar(
        select(PlanningPart)
        .where(
            PlanningPart.project_id == project_id,
            PlanningPart.plan_id == plan.id,
            PlanningPart.id == chapter_identity.part_id,
        )
        .with_for_update()
    )
    chapter = await db.scalar(
        select(PlanningChapter)
        .where(
            PlanningChapter.project_id == project_id,
            PlanningChapter.plan_id == plan.id,
            PlanningChapter.id == chapter_identity.id,
            PlanningChapter.part_id == chapter_identity.part_id,
        )
        .with_for_update()
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
    if part is None or chapter is None or run is None:
        raise GenerationExecutionError(
            "GENERATION_SCOPE_CORRUPT",
            "生成目标结构不完整，系统已停止执行。",
            recommended_action="contact_support",
        )
    _messages_for_run(run)
    if plan.status != "active" or part.status != "active" or chapter.status != "active":
        raise GenerationExecutionError(
            "GENERATION_SCOPE_ARCHIVED",
            "篇章或章节规划已归档，不能发起生成。",
            recommended_action="restore_scope",
        )
    if (
        plan.structure_version != run.structure_version
        or plan.assignment_version != run.assignment_version
        or chapter.lock_version != run.chapter_lock_version
    ):
        raise GenerationExecutionError(
            "GENERATION_PREFLIGHT_STALE",
            "生成准备后规划已变化，请重新检查上下文。",
            recommended_action="refresh_generation_preflight",
        )
    return run


async def _claim_attempt(db: AsyncSession, attempt_id: str) -> bool:
    ensure_project_writes_available()
    now = _utcnow()
    result = await db.execute(
        update(ChapterGenerationAttempt)
        .where(
            ChapterGenerationAttempt.id == attempt_id,
            ChapterGenerationAttempt.status == "reserved",
            ChapterGenerationAttempt.lock_version == 1,
        )
        .values(
            status="calling",
            ai_invoked=True,
            billing_effect="possible",
            usage_status="unknown",
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            claimed_at=now,
            lock_version=2,
            updated_at=now,
        )
    )
    won = result.rowcount == 1
    await db.commit()
    return won


async def _mark_terminal_failure(
    db: AsyncSession,
    attempt_id: str,
    *,
    code: str,
    message: str,
    outcome_unknown: bool,
) -> ChapterGenerationAttempt:
    await db.rollback()
    attempt = await db.scalar(
        select(ChapterGenerationAttempt)
        .where(ChapterGenerationAttempt.id == attempt_id)
        .with_for_update()
    )
    if attempt is None:
        raise _corrupt()
    if attempt.status in {"reserved", "calling"}:
        now = _utcnow()
        attempt.status = "outcome_unknown" if outcome_unknown else "failed"
        if attempt.status == "outcome_unknown":
            attempt.usage_status = "unknown"
        else:
            attempt.usage_status = "unavailable"
        attempt.input_tokens = None
        attempt.output_tokens = None
        attempt.total_tokens = None
        attempt.completed_at = now
        attempt.error_code = code[:80]
        attempt.error_message = message[:500]
        attempt.lock_version += 1
        attempt.updated_at = now
        await db.commit()
    return attempt


async def _persist_success(
    db: AsyncSession,
    attempt_id: str,
    result: GenerationTransportResult,
) -> ChapterGenerationAttempt:
    attempt_identity = await db.scalar(
        select(ChapterGenerationAttempt).where(ChapterGenerationAttempt.id == attempt_id)
    )
    if attempt_identity is None:
        raise _corrupt()
    run = await db.scalar(
        select(ChapterGenerationRun)
        .where(
            ChapterGenerationRun.project_id == attempt_identity.project_id,
            ChapterGenerationRun.id == attempt_identity.run_id,
        )
        .with_for_update()
    )
    attempt = await db.scalar(
        select(ChapterGenerationAttempt)
        .where(ChapterGenerationAttempt.id == attempt_id)
        .with_for_update()
    )
    if run is None or attempt is None:
        raise _corrupt()
    if attempt.status != "calling":
        return attempt
    messages = _messages_for_run(run)
    if attempt.prompt_checksum != _prompt_checksum(messages):
        raise _corrupt()
    content = result.content.strip()
    content_bytes = content.encode("utf-8")
    words = _word_count(content)
    if not content or not words or len(content_bytes) > MAX_CANDIDATE_BYTES:
        return await _mark_terminal_failure(
            db,
            attempt_id,
            code="GENERATION_RESPONSE_INVALID",
            message="LLM 返回的章节候选为空或超出安全上限，未保存候选。",
            outcome_unknown=False,
        )
    next_version = (
        await db.scalar(
            select(func.max(ChapterGenerationCandidate.version_no)).where(
                ChapterGenerationCandidate.project_id == attempt.project_id,
                ChapterGenerationCandidate.run_id == attempt.run_id,
            )
        )
        or 0
    ) + 1
    manifest = generation_run_response(run, replayed=True)["context_manifest"]
    now = _utcnow()
    db.add(
        ChapterGenerationCandidate(
            id=uuid.uuid4().hex,
            project_id=attempt.project_id,
            run_id=attempt.run_id,
            source_attempt_id=attempt.id,
            parent_candidate_id=None,
            version_no=next_version,
            origin_kind="generated",
            title=manifest["chapter"]["title"],
            content=content,
            content_format="plain_text",
            content_checksum=hashlib.sha256(content_bytes).hexdigest(),
            content_size_bytes=len(content_bytes),
            word_count=words,
            created_by=attempt.requested_by,
            created_at=now,
        )
    )
    attempt.status = "succeeded"
    if result.usage is None:
        attempt.usage_status = "unavailable"
        attempt.input_tokens = None
        attempt.output_tokens = None
        attempt.total_tokens = None
    else:
        usage = result.usage
        if (
            isinstance(usage.input_tokens, bool)
            or isinstance(usage.output_tokens, bool)
            or isinstance(usage.total_tokens, bool)
            or usage.input_tokens < 0
            or usage.output_tokens < 0
            or usage.total_tokens != usage.input_tokens + usage.output_tokens
        ):
            return await _mark_terminal_failure(
                db,
                attempt_id,
                code="GENERATION_USAGE_INVALID",
                message="LLM 返回的用量信息无法安全核对，未保存候选。",
                outcome_unknown=False,
            )
        attempt.usage_status = "reported"
        attempt.input_tokens = usage.input_tokens
        attempt.output_tokens = usage.output_tokens
        attempt.total_tokens = usage.total_tokens
    attempt.completed_at = now
    attempt.lock_version += 1
    attempt.updated_at = now
    await db.flush()
    await db.commit()
    return attempt


async def execute_generation_attempt(
    *,
    db: AsyncSession,
    project_id: str,
    user_id: str,
    run_id: str,
    operation_key: str,
    expected_context_checksum: str,
    expected_capability_checksum: str,
    transport: GenerationTransport,
) -> dict[str, Any]:
    fingerprint = execution_request_fingerprint(
        project_id,
        run_id,
        expected_context_checksum,
        expected_capability_checksum,
    )
    existing = await find_generation_attempt_by_key(
        db, project_id, user_id, operation_key
    )
    if existing is not None:
        return await generation_attempt_response(
            db, existing, replayed=True, expected_fingerprint=fingerprint
        )

    try:
        ensure_project_writes_available()
        run = await _load_locked_execution_scope(db, project_id, user_id, run_id)
        if expected_context_checksum != run.context_checksum:
            raise GenerationExecutionError(
                "GENERATION_CONTEXT_CHECKSUM_CONFLICT",
                "生成上下文已与当前请求不一致。",
                recommended_action="refresh_generation_preflight",
            )
        messages = _messages_for_run(run)
        transport.ensure_ready()
        capability = generation_capability_response(transport)
        if expected_capability_checksum != capability["capability_checksum"]:
            raise GenerationExecutionError(
                "LLM_CAPABILITY_CHANGED",
                "LLM 能力已变化，未预约或发起本次调用。",
                recommended_action="refresh_generation_capability",
            )
        now = _utcnow()
        attempt = ChapterGenerationAttempt(
            id=uuid.uuid4().hex,
            project_id=project_id,
            run_id=run.id,
            requested_by=user_id,
            operation_key=operation_key,
            request_fingerprint=fingerprint,
            status="reserved",
            execution_mode="single_call",
            billing_confirmed=True,
            ai_invoked=False,
            billing_effect="none",
            capability_schema_version=capability["schema_version"],
            capability_snapshot={
                key: value
                for key, value in capability.items()
                if key != "capability_checksum"
            },
            capability_checksum=capability["capability_checksum"],
            execution_config_digest=transport.execution_config_digest,
            provider_name=capability["provider_name"],
            model_name=transport.model_name,
            max_output_tokens=capability["max_output_tokens"],
            input_limit_availability=capability[
                "input_limit_availability"
            ],
            max_input_tokens=capability["max_input_tokens"],
            price_availability=capability["price_availability"],
            prompt_schema_version=PROMPT_SCHEMA_VERSION,
            prompt_checksum=_prompt_checksum(messages),
            context_checksum=run.context_checksum,
            usage_status="unavailable",
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            lock_version=1,
            created_at=now,
            updated_at=now,
        )
        db.add(attempt)
        await db.flush()
        ensure_project_writes_available()
        await db.commit()
    except (GenerationExecutionError, GenerationPreparationError):
        await db.rollback()
        raise
    except IntegrityError as exc:
        await db.rollback()
        raced = await find_generation_attempt_by_key(
            db, project_id, user_id, operation_key
        )
        if raced is not None:
            return await generation_attempt_response(
                db, raced, replayed=True, expected_fingerprint=fingerprint
            )
        raise GenerationExecutionError(
            "GENERATION_EXECUTION_CONFLICT",
            "生成预约发生并发冲突，系统未重复调用模型。",
            retryable=True,
            recommended_action="check_execution_by_key",
        ) from exc

    try:
        try:
            transport.verify_capability_current()
        except GenerationExecutionError as exc:
            terminal = await _mark_terminal_failure(
                db,
                attempt.id,
                code=exc.detail["code"],
                message=exc.detail["message"],
                outcome_unknown=False,
            )
            return await generation_attempt_response(db, terminal, replayed=False)
        if not await _claim_attempt(db, attempt.id):
            current = await db.scalar(
                select(ChapterGenerationAttempt).where(
                    ChapterGenerationAttempt.id == attempt.id
                )
            )
            if current is None:
                raise _corrupt()
            return await generation_attempt_response(db, current, replayed=True)
        raw_content = await transport.generate(messages)
        if isinstance(raw_content, str):
            transport_result = GenerationTransportResult(content=raw_content)
        elif isinstance(raw_content, GenerationTransportResult):
            transport_result = raw_content
        else:
            raise LLMSingleCallError(
                "LLM_RESPONSE_INVALID", "LLM 返回了无法读取的响应。"
            )
        terminal = await _persist_success(db, attempt.id, transport_result)
    except LLMSingleCallError as exc:
        terminal = await _mark_terminal_failure(
            db,
            attempt.id,
            code=exc.code,
            message=exc.safe_message,
            outcome_unknown=exc.outcome_unknown,
        )
    except asyncio.CancelledError:
        await asyncio.shield(
            _mark_terminal_failure(
                db,
                attempt.id,
                code="GENERATION_OUTCOME_UNKNOWN",
                message="生成请求已中断，模型结果无法确认。",
                outcome_unknown=True,
            )
        )
        raise
    except GenerationExecutionError as exc:
        terminal = await _mark_terminal_failure(
            db,
            attempt.id,
            code=exc.detail["code"],
            message=exc.detail["message"],
            outcome_unknown=False,
        )
    except Exception:
        terminal = await _mark_terminal_failure(
            db,
            attempt.id,
            code="GENERATION_OUTCOME_UNKNOWN",
            message="生成请求的最终结果无法确认。",
            outcome_unknown=True,
        )
    return await generation_attempt_response(db, terminal, replayed=False)
