import type { GenerationRunPrepareInput, GenerationRunResponse } from "@/types/generation";

export interface ExpectedGenerationRun {
  projectId: string;
  chapterId: string;
  runId?: string;
  operationKey?: string;
  payload?: GenerationRunPrepareInput;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function positiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 1;
}

function stableId(value: unknown): value is string {
  return typeof value === "string" && value.length === 32;
}

export function generationRunContractError(
  value: unknown,
  expected: ExpectedGenerationRun
): string | null {
  if (!isRecord(value)) return "服务端检查记录格式无效。";
  if (
    value.project_id !== expected.projectId ||
    value.planning_chapter_id !== expected.chapterId ||
    (expected.runId !== undefined && value.id !== expected.runId) ||
    (expected.operationKey !== undefined && value.operation_key !== expected.operationKey)
  ) return "服务端检查记录与当前项目、章节或操作编号不一致。";
  if (
    value.status !== "prepared" ||
    value.execution_mode !== "preflight_only" ||
    value.ai_invoked !== false ||
    value.billing_effect !== "none"
  ) return "服务端未返回零 AI、零费用的仅检查记录，已停止展示。";
  if (
    !stableId(value.id) ||
    !stableId(value.plan_id) ||
    typeof value.operation_key !== "string" || value.operation_key.length < 8 || value.operation_key.length > 128 || !/^[A-Za-z0-9._:-]+$/.test(value.operation_key) ||
    typeof value.replayed !== "boolean" ||
    value.context_schema_version !== 1 ||
    !positiveInteger(value.structure_version) ||
    !positiveInteger(value.assignment_version) ||
    !positiveInteger(value.chapter_lock_version) ||
    typeof value.context_checksum !== "string" || !/^[a-f0-9]{64}$/.test(value.context_checksum) ||
    typeof value.context_size_bytes !== "number" || !Number.isInteger(value.context_size_bytes) || value.context_size_bytes < 0 || value.context_size_bytes > 65_536 ||
    typeof value.created_at !== "string" || !Number.isFinite(Date.parse(value.created_at)) ||
    typeof value.updated_at !== "string" || !Number.isFinite(Date.parse(value.updated_at))
  ) return "服务端检查记录的身份、版本或校验信息无效。";
  if (expected.payload && (
    value.structure_version !== expected.payload.expected_structure_version ||
    value.assignment_version !== expected.payload.expected_assignment_version ||
    value.chapter_lock_version !== expected.payload.expected_chapter_lock_version
  )) return "服务端检查记录与原请求版本不一致。";

  const manifest = value.context_manifest;
  if (!isRecord(manifest) || manifest.schema_version !== 1 || manifest.project_id !== expected.projectId || manifest.plan_id !== value.plan_id) {
    return "服务端上下文清单与检查记录身份不一致。";
  }
  if (!isRecord(manifest.chapter) || manifest.chapter.id !== expected.chapterId || manifest.chapter.lock_version !== value.chapter_lock_version) {
    return "服务端上下文清单引用了错误的章节或章节版本。";
  }
  if (
    !isRecord(manifest.part) || !stableId(manifest.part.id) ||
    typeof manifest.part.title !== "string" || typeof manifest.part.description !== "string" ||
    !positiveInteger(manifest.part.position) || !positiveInteger(manifest.part.lock_version) ||
    typeof manifest.chapter.title !== "string" || typeof manifest.chapter.summary !== "string" ||
    !positiveInteger(manifest.chapter.position) ||
    (manifest.chapter.target_word_count !== null && (
      !positiveInteger(manifest.chapter.target_word_count) || manifest.chapter.target_word_count < 500 || manifest.chapter.target_word_count > 10_000
    ))
  ) return "服务端上下文中的篇章或章节快照无效。";
  if (!isRecord(manifest.versions) || manifest.versions.structure !== value.structure_version || manifest.versions.assignment !== value.assignment_version || manifest.versions.chapter_lock !== value.chapter_lock_version) {
    return "服务端上下文清单版本与检查记录不一致。";
  }
  if (!Array.isArray(manifest.elements) || manifest.elements.length < 1 || manifest.elements.length > 100 || !Array.isArray(manifest.relations) || manifest.relations.length > 300 || !Array.isArray(manifest.warnings)) {
    return "服务端上下文清单的设定、关系或提醒数量无效。";
  }
  if (!isRecord(manifest.counts) || manifest.counts.elements !== manifest.elements.length || manifest.counts.relations !== manifest.relations.length || manifest.counts.warnings !== manifest.warnings.length) {
    return "服务端上下文清单计数不一致。";
  }
  const elementIds = new Set<string>();
  const scopeTargets: Record<string, unknown> = {
    novel: expected.projectId,
    part: manifest.part.id,
    chapter: expected.chapterId,
  };
  for (const raw of manifest.elements) {
    if (!isRecord(raw) || !stableId(raw.element_id) || elementIds.has(raw.element_id) || !isRecord(raw.version) || !stableId(raw.version.id) || raw.version.element_id !== raw.element_id || !isRecord(raw.type) || !stableId(raw.type.id) || raw.version.type_id !== raw.type.id || typeof raw.type.key !== "string" || typeof raw.type.display_name !== "string" || !positiveInteger(raw.type.schema_revision) || !Array.isArray(raw.assignment_sources) || raw.assignment_sources.length < 1 || typeof raw.version.name !== "string" || typeof raw.version.summary !== "string" || !positiveInteger(raw.version.version_no) || !isRecord(raw.version.payload) || !isRecord(raw.version.field_states) || (raw.version.source_id !== null && !stableId(raw.version.source_id))) {
      return "服务端上下文包含无效或重复的设定记录。";
    }
    for (const source of raw.assignment_sources) {
      if (!isRecord(source) || !stableId(source.assignment_id) || !["novel", "part", "chapter"].includes(String(source.scope_type)) || source.scope_target_id !== scopeTargets[String(source.scope_type)] || typeof source.scope_title !== "string" || !positiveInteger(source.assignment_lock_version) || !positiveInteger(source.assigned_at_content_version)) {
        return "服务端上下文包含无效或越界的设定来源。";
      }
    }
    elementIds.add(raw.element_id);
  }
  const relationIds = new Set<string>();
  for (const raw of manifest.relations) {
    if (!isRecord(raw) || !stableId(raw.relation_id) || relationIds.has(raw.relation_id) || !isRecord(raw.version) || !stableId(raw.version.id) || raw.version.relation_id !== raw.relation_id || !elementIds.has(String(raw.version.source_element_id)) || !elementIds.has(String(raw.version.target_element_id)) || raw.version.status !== "active" || !positiveInteger(raw.version.version_no) || typeof raw.version.relation_key !== "string" || typeof raw.version.forward_label !== "string" || typeof raw.version.reverse_label !== "string" || typeof raw.version.description !== "string" || !isRecord(raw.version.metadata)) {
      return "服务端上下文包含无效关系或上下文外端点。";
    }
    relationIds.add(raw.relation_id);
  }
  if (!isRecord(manifest.foreshadow_actions) || manifest.foreshadow_actions.supported !== false || !Array.isArray(manifest.foreshadow_actions.items) || manifest.foreshadow_actions.items.length !== 0) {
    return "服务端检查记录意外包含当前阶段不支持的伏笔动作。";
  }
  for (const warning of manifest.warnings) {
    if (!isRecord(warning) || !["CHAPTER_SUMMARY_EMPTY", "LORE_CHANGED_SINCE_ASSIGNMENT"].includes(String(warning.code)) || (warning.code === "CHAPTER_SUMMARY_EMPTY" && warning.element_id !== null) || (warning.code === "LORE_CHANGED_SINCE_ASSIGNMENT" && !elementIds.has(String(warning.element_id)))) {
      return "服务端上下文包含无效或越界的提醒。";
    }
  }
  return null;
}

export function assertGenerationRun(
  value: unknown,
  expected: ExpectedGenerationRun
): GenerationRunResponse {
  const error = generationRunContractError(value, expected);
  if (error) throw new Error(error);
  return value as GenerationRunResponse;
}
