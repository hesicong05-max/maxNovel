import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import PlanningGenerationPreflight from "./PlanningGenerationPreflight";
import type { GenerationAttemptResponse, GenerationCandidateAuditResponse, GenerationCandidateResponse, GenerationCapabilityResponse, GenerationRunResponse } from "@/types/generation";
import type { NovelPlan, PlanningChapter, PlanningPart } from "@/types/planning";

const id = (value: string) => value.padEnd(32, value).slice(0, 32);
const projectId = id("project");
const planId = id("plan");
const partId = id("part");
const chapterId = id("chapter");
const firstId = id("first");
const secondId = id("second");
const typeId = id("type");
const now = "2026-08-11T05:00:00Z";

const chapter: PlanningChapter = { id: chapterId, project_id: projectId, plan_id: planId, part_id: partId, title: "第一章", summary: "雨夜相遇", target_word_count: 2000, position: 1, status: "active", lock_version: 4, created_at: now, updated_at: now };
const part: PlanningPart = { id: partId, project_id: projectId, plan_id: planId, title: "第一篇", description: "", position: 1, status: "active", lock_version: 1, created_at: now, updated_at: now, chapters: [chapter] };
const plan: NovelPlan = { id: planId, project_id: projectId, status: "active", structure_version: 2, assignment_version: 3, created_at: now, updated_at: now, parts: [part] };

function element(elementId: string, name: string, scope: "novel" | "part" | "chapter", scopeId: string, scopeTitle: string) {
  return {
    element_id: elementId,
    type: { id: typeId, key: "character", display_name: "角色", schema_revision: 1 },
    version: { id: id(`${name}-version`), element_id: elementId, type_id: typeId, version_no: 1, name, summary: `${name}摘要`, payload: { identity: `${name}身份`, long_text: "设定".repeat(80) }, field_states: { identity: "confirmed" }, source_id: id(`${name}-source`) },
    assignment_sources: [{ assignment_id: id(`${name}-assignment`), scope_type: scope, scope_target_id: scopeId, scope_title: scopeTitle, assignment_lock_version: 1, assigned_at_content_version: 1 }],
  };
}

const run: GenerationRunResponse = {
  id: id("run"), project_id: projectId, plan_id: planId, planning_chapter_id: chapterId,
  operation_key: "planning:generation_prepare:12345678", replayed: false,
  status: "prepared", execution_mode: "preflight_only", ai_invoked: false, billing_effect: "none",
  structure_version: 2, assignment_version: 3, chapter_lock_version: 4, context_schema_version: 1,
  context_checksum: "b".repeat(64), context_size_bytes: 2048, created_at: now, updated_at: now,
  context_manifest: {
    schema_version: 1, project_id: projectId, plan_id: planId,
    versions: { structure: 2, assignment: 3, chapter_lock: 4 },
    part: { id: partId, title: "第一篇", description: "", position: 1, lock_version: 1 },
    chapter: { id: chapterId, title: "第一章", summary: "雨夜相遇", target_word_count: 2000, position: 1, lock_version: 4 },
    elements: [element(firstId, "沈星", "novel", projectId, "整部小说"), element(secondId, "林夜", "chapter", chapterId, "第一章")],
    relations: [{ relation_id: id("relation"), version: { id: id("relation-version"), relation_id: id("relation"), version_no: 2, source_element_id: firstId, target_element_id: secondId, relation_key: "ally", forward_label: "信任", reverse_label: "信任", description: "共同调查", metadata: { since: "第一章" }, status: "active" } }],
    warnings: [{ code: "LORE_CHANGED_SINCE_ASSIGNMENT", element_id: firstId }],
    foreshadow_actions: { supported: false, items: [] }, counts: { elements: 2, relations: 1, warnings: 1 },
  },
};

const capability: GenerationCapabilityResponse = {
  schema_version: 1,
  provider_name: "provider-internal-hash",
  model_name: "demo-model",
  max_output_tokens: 4096,
  input_limit_availability: "unavailable",
  max_input_tokens: null,
  price_availability: "unavailable",
  capability_checksum: "a".repeat(64),
};

function attempt(status: GenerationAttemptResponse["status"], aiInvoked = true): GenerationAttemptResponse {
  return {
    id: id("attempt"), project_id: projectId, run_id: run.id, planning_chapter_id: chapterId,
    operation_key: "generation:execute:12345678", replayed: false, status,
    execution_mode: "single_call", billing_confirmed: true, ai_invoked: aiInvoked,
    billing_effect: aiInvoked ? "possible" : "none", capability, model_name: capability.model_name,
    prompt_schema_version: 1, prompt_checksum: "c".repeat(64), context_checksum: run.context_checksum,
    lock_version: 1,
    usage: status === "calling" || status === "outcome_unknown"
      ? { status: "unknown", input_tokens: null, output_tokens: null, total_tokens: null }
      : { status: "unavailable", input_tokens: null, output_tokens: null, total_tokens: null },
    candidate_id: null,
    error: status === "failed" || status === "outcome_unknown" ? { code: "PROVIDER_ERROR", message: "failed", retryable: false, recommended_action: status === "failed" ? "inspect_failure" : "keep_unknown_result" } : null,
    claimed_at: aiInvoked ? now : null,
    completed_at: status === "failed" || status === "outcome_unknown" ? now : null,
    created_at: now, updated_at: now,
  };
}

function props(overrides: Record<string, unknown> = {}) {
  return {
    plan, part, chapter, run, busy: false, loadingSaved: false, disabled: false,
    disabledReason: "", error: "", recoveryState: "idle" as const, stale: false,
    recovered: false, focusResultToken: 0, focusFeedbackToken: 0, hasPendingRecovery: false,
    capability: null, attempt: null, candidate: null, candidateAudit: null, executionBusy: false,
    candidateLoading: false, auditLoading: false, auditError: "", executionError: "", executionDisabledReason: "", confirmationOpen: false,
    runActionsDisabledReason: "", confirmationUsesOriginalRequest: false,
    originalRetryAllowed: false,
    newAttemptDisabled: false,
    onPrepare: vi.fn(), onCheckPending: vi.fn(),
    onRetryOriginal: vi.fn(), onFocusAssignments: vi.fn(), onClearSavedPointer: vi.fn(),
    onAbandonPending: vi.fn(),
    onOpenGenerationConfirmation: vi.fn(), onCancelGenerationConfirmation: vi.fn(),
    onConfirmGeneration: vi.fn(), onCheckGenerationAttempt: vi.fn(), onReadGenerationCandidate: vi.fn(), onReadGenerationCandidateAudit: vi.fn(),
    onRetryOriginalGeneration: vi.fn(), onStartNewAfterFailure: vi.fn(),
    ...overrides,
  };
}

describe("PlanningGenerationPreflight", () => {
  it("removes the real paid entry for ready technical-demo and hidden descriptor modes", () => {
    const { rerender } = render(<MemoryRouter><PlanningGenerationPreflight {...props({ executionMode: "technical", technicalDemoUserId: id("user") })} /></MemoryRouter>);
    expect(screen.getByRole("button", { name: "查看边界并确认技术模拟" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看模型信息并确认生成" })).not.toBeInTheDocument();
    expect(screen.queryByText("可能调用模型并产生费用")).not.toBeInTheDocument();
    expect(screen.getByText(/固定内容技术模拟/)).toHaveTextContent("不调用 AI");
    expect(screen.getByRole("heading", { name: "技术模拟前：冻结《第一章》上下文" })).toBeInTheDocument();
    expect(screen.queryByText(run.id)).not.toBeInTheDocument();
    expect(screen.queryByText(id("沈星-source"))).not.toBeInTheDocument();
    rerender(<MemoryRouter><PlanningGenerationPreflight {...props({ executionMode: "hidden" })} /></MemoryRouter>);
    expect(screen.queryByRole("button", { name: "查看边界并确认技术模拟" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看模型信息并确认生成" })).not.toBeInTheDocument();
    expect(screen.getByText(/这里仅检查并保存本章/)).toBeInTheDocument();
    expect(screen.queryByText(/固定内容技术模拟/)).not.toBeInTheDocument();
  });
  it("limits the zero-cost claim to preflight while separating the paid generation step", () => {
    render(<PlanningGenerationPreflight {...props()} />);
    expect(screen.getByText("检查记录已保存")).toBeInTheDocument();
    expect(screen.getByText("上下文检查：零 AI · 零费用")).toBeInTheDocument();
    expect(screen.getByText("本次检查：AI 未调用")).toBeInTheDocument();
    expect(screen.getByText("本次检查费用：无")).toBeInTheDocument();
    expect(screen.getByText("可能调用模型并产生费用")).toBeInTheDocument();
    expect(screen.queryByText(run.context_checksum)).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("查看 2 项设定"));
    fireEvent.click(screen.getAllByText("查看全部分配来源")[0]);
    expect(screen.getByText("继承自整部小说")).toBeInTheDocument();
    expect(screen.getByText("本章节直接分配")).toBeInTheDocument();
    fireEvent.click(screen.getByText("查看 1 条关系"));
    expect(screen.getByText("沈星 信任 林夜")).toBeInTheDocument();
    expect(screen.getByText(/设定内容在分配后有更新/)).toBeInTheDocument();
  });

  it("keeps a missing by-key result on the original-request retry path", () => {
    const onRetryOriginal = vi.fn();
    render(<PlanningGenerationPreflight {...props({ run: null, recoveryState: "not_found", onRetryOriginal })} />);
    expect(screen.getByText(/只能使用原操作编号和原版本载荷/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "使用原请求安全重试" }));
    expect(onRetryOriginal).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "检查生成上下文" })).not.toBeInTheDocument();
  });

  it("labels an older snapshot and disables recheck when the chapter has unsaved edits", () => {
    render(<PlanningGenerationPreflight {...props({ stale: true, disabled: true, disabledReason: "当前章节有未保存修改。" })} />);
    expect(screen.getByText("基于旧版本")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新检查当前上下文" })).toBeDisabled();
    expect(screen.getByText("当前章节有未保存修改。")).toBeInTheDocument();
  });

  it("keeps recheck and close disabled while an execution receipt is pending", () => {
    render(<PlanningGenerationPreflight {...props({
      runActionsDisabledReason: "生成执行收据仍在处理中；只能核对当前生成。",
      attempt: attempt("reserved", false),
    })} />);
    expect(screen.getByRole("button", { name: "再次检查当前上下文" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "关闭这条记录" })).toBeDisabled();
    expect(screen.getByText(/生成执行收据仍在处理中/)).toBeInTheDocument();
  });

  it("presents an alert dialog with unavailable limits, honest billing, and an anonymous provider label", () => {
    const onCancelGenerationConfirmation = vi.fn();
    render(<PlanningGenerationPreflight {...props({ capability, confirmationOpen: true, onCancelGenerationConfirmation })} />);
    const dialog = screen.getByRole("alertdialog", { name: "确认调用模型生成候选" });
    expect(dialog).toHaveTextContent("已配置的模型服务 / demo-model");
    expect(dialog).not.toHaveTextContent("provider-internal-hash");
    expect(dialog).toHaveTextContent("输入上限");
    expect(dialog).toHaveTextContent("最终以服务商账单为准");
    expect(dialog).toHaveTextContent("不覆盖任何现有正文");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onCancelGenerationConfirmation).toHaveBeenCalledOnce();
  });

  it("never offers a second POST path for an unknown outcome and reports possible billing", () => {
    const onCheckGenerationAttempt = vi.fn();
    render(<PlanningGenerationPreflight {...props({ attempt: attempt("outcome_unknown"), onCheckGenerationAttempt })} />);
    expect(screen.getByText(/可能已被服务商受理并产生费用/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /重新获取/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "按原编号核对状态" }));
    expect(onCheckGenerationAttempt).toHaveBeenCalledOnce();
  });

  it("distinguishes a pre-call failure from a possibly billed post-call failure", () => {
    const { rerender } = render(<PlanningGenerationPreflight {...props({ attempt: attempt("failed", false) })} />);
    expect(screen.getByText(/在模型调用前失败，没有模型费用/)).toBeInTheDocument();
    rerender(<PlanningGenerationPreflight {...props({ attempt: attempt("failed", true) })} />);
    expect(screen.getByText(/已进入模型调用，可能产生费用/)).toBeInTheDocument();
  });

  it("moves focus to a newly validated candidate heading", async () => {
    const candidate: GenerationCandidateResponse = {
      id: id("candidate"), project_id: projectId, run_id: run.id,
      planning_chapter_id: chapterId, source_attempt_id: id("attempt"), parent_candidate_id: null,
      version_no: 1, origin_kind: "generated", title: "第一章", content: "候选正文",
      content_format: "plain_text", content_checksum: "d".repeat(64), content_size_bytes: 12,
      word_count: 4, created_by: id("user"), created_at: now,
    };
    render(<PlanningGenerationPreflight {...props({ candidate })} />);
    const heading = screen.getByRole("heading", { name: "候选正文已就绪：第一章" });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(screen.getByLabelText("候选正文内容")).toHaveAttribute("tabindex", "0");
  });

  it("shows deterministic review evidence without claiming semantic approval", async () => {
    const candidate: GenerationCandidateResponse = {
      id: id("candidate"), project_id: projectId, run_id: run.id,
      planning_chapter_id: chapterId, source_attempt_id: id("attempt"), parent_candidate_id: null,
      version_no: 1, origin_kind: "generated", title: "第一章", content: "提到了《无名星门》。",
      content_format: "plain_text", content_checksum: "d".repeat(64), content_size_bytes: 30,
      word_count: 9, created_by: id("user"), created_at: now,
    };
    const audit: GenerationCandidateAuditResponse = {
      schema_version: 1, ruleset_version: 1, project_id: projectId, run_id: run.id,
      planning_chapter_id: chapterId, candidate_id: candidate.id, candidate_version: 1,
      candidate_checksum: candidate.content_checksum, context_checksum: run.context_checksum,
      status: "review",
      integrity: { status: "pass", content_size_bytes: 30, word_count: 9, storage_limit_bytes: 262144, storage_limit_reached: false },
      target_length: { status: "review", actual_word_count: 9, target_word_count: 2000, minimum_word_count: 1400, maximum_word_count: 2600 },
      preparation: { status: "review", warnings: run.context_manifest.warnings },
      unrecognized_explicit_terms: { status: "review", items: [{ term: "无名星门", excerpt: "提到了《无名星门》。", start_offset: 3, end_offset: 9 }], truncated: false },
      context_summary: { element_count: 2, relation_count: 1, warning_count: 1, elements: run.context_manifest.elements.map((item) => ({ element_id: item.element_id, type_key: item.type.key, type_display_name: item.type.display_name, name: item.version.name, version_no: item.version.version_no })), foreshadow_actions_supported: false, foreshadow_action_count: 0 },
    };
    render(<PlanningGenerationPreflight {...props({ candidate, candidateAudit: audit })} />);
    expect(screen.getByRole("heading", { name: "确定性检查" })).toBeInTheDocument();
    expect(screen.getByText("需要人工核对")).toBeInTheDocument();
    expect(screen.getByText(/不判断情节、人物或世界规则的语义一致性/)).toBeInTheDocument();
    expect(screen.getByText(/发现需要核对的《》标记名称/)).toBeInTheDocument();
    expect(screen.getByText(/仅因《》标记且未出现在本次冻结清单中/)).toBeInTheDocument();
    expect(screen.getByText("需要人工核对")).toHaveAttribute("role", "status");
    expect(screen.getByText("查看本次冻结设定").closest("summary")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/一致性通过|AI.*验证|违规设定|事实冲突/);
    expect(screen.getByText("《无名星门》")).toBeInTheDocument();
    const evidence = screen.getAllByText("提到了《无名星门》。").find((item) => item.tagName === "BLOCKQUOTE");
    expect(evidence).toBeDefined();
    expect(evidence).not.toHaveAttribute("tabindex");
  });

  it("keeps candidate readable when audit fails and retries only the audit", () => {
    const onReadGenerationCandidateAudit = vi.fn();
    const candidate: GenerationCandidateResponse = {
      id: id("candidate"), project_id: projectId, run_id: run.id,
      planning_chapter_id: chapterId, source_attempt_id: id("attempt"), parent_candidate_id: null,
      version_no: 1, origin_kind: "generated", title: "第一章", content: "候选正文",
      content_format: "plain_text", content_checksum: "d".repeat(64), content_size_bytes: 12,
      word_count: 4, created_by: id("user"), created_at: now,
    };
    render(<PlanningGenerationPreflight {...props({ candidate, auditError: "检查暂不可用。候选正文仍保留只读。", onReadGenerationCandidateAudit })} />);
    expect(screen.getByLabelText("候选正文内容")).toHaveTextContent("候选正文");
    fireEvent.click(screen.getByRole("button", { name: "重新读取检查" }));
    expect(onReadGenerationCandidateAudit).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: /确认并生成/ })).not.toBeInTheDocument();
  });

  it("focuses a newly entered failed terminal alert but keeps calling as polite status", async () => {
    const failed = attempt("failed", false);
    const { rerender } = render(<PlanningGenerationPreflight {...props({ attempt: failed })} />);
    const failedAlert = screen.getByText("failed").closest("[role='alert']");
    await waitFor(() => expect(failedAlert).toHaveFocus());
    rerender(<PlanningGenerationPreflight {...props({ attempt: attempt("calling") })} />);
    expect(screen.getByText("模型调用中")).toBeInTheDocument();
    expect(screen.getByText("模型调用中").closest("[role='alert']")).toBeNull();
  });
});
