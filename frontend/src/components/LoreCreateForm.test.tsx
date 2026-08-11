import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import { draftStorageKey, type DraftScope } from "@/services/maintenanceDrafts";
import type { LoreElementCreateResponse, LoreTypeDefinition } from "@/types/lore";
import LoreCreateForm, { type LoreCreateStoredPayload } from "./LoreCreateForm";

const scope: DraftScope = {
  userId: "user-1",
  projectId: "project-1",
  kind: "lore-create",
  objectId: "new",
};

const loreTypes: LoreTypeDefinition[] = [{
  id: "type-character",
  key: "character",
  display_name: "角色",
  description: "角色设定",
  field_schema: [{
    key: "appearance",
    label: "外貌",
    control: "text",
    value_type: "text",
    help: "只填写用户确认的外貌",
    order: 10,
    required: false,
  }],
  is_builtin: true,
  schema_revision: 1,
  status: "active",
  created_at: "2026-08-06T00:00:00Z",
  updated_at: "2026-08-06T00:00:00Z",
}];

const response: LoreElementCreateResponse = {
  id: "element-1",
  type: { key: "character", display_name: "角色" },
  name: "林渊",
  summary: "",
  confirmation_status: "confirmed",
  lifecycle_status: "active",
  enabled: true,
  generation_eligible: true,
  lock_version: 1,
  content_version: 1,
  payload_schema_revision: 1,
  payload: { appearance: "黑发" },
  field_states: { appearance: "provided" },
  field_definitions: loreTypes[0].field_schema,
  sources: [],
  relation_count: 0,
  binding_count: 0,
  created_at: "2026-08-06T00:00:00Z",
  updated_at: "2026-08-06T00:00:00Z",
  replayed: false,
};

function renderForm(initialStored: LoreCreateStoredPayload | null = null) {
  const onComplete = vi.fn();
  render(
    <LoreCreateForm
      projectId="project-1"
      scope={scope}
      loreTypes={loreTypes}
      typesLoading={false}
      typesError=""
      initialStored={initialStored}
      onDirtyChange={vi.fn()}
      onBusyChange={vi.fn()}
      onComplete={onComplete}
      onCancel={vi.fn()}
    />
  );
  return { onComplete };
}

async function fillValidDraft() {
  await userEvent.type(screen.getByRole("textbox", { name: "名称" }), "林渊");
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "外貌的信息状态" }),
    "provided"
  );
  await userEvent.type(screen.getByRole("textbox", { name: "外貌" }), "黑发");
  await userEvent.type(screen.getByRole("textbox", { name: "原文摘录（可选）" }), "林渊有一头黑发。");
}

describe("LoreCreateForm", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("persists one operation key, submits explicit fields, and clears the draft on success", async () => {
    const createLoreElement = vi.fn().mockResolvedValue(response);
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      createLoreElement,
    });
    const { onComplete } = renderForm();
    await fillValidDraft();
    await userEvent.click(screen.getByRole("button", { name: "创建正式设定" }));

    await waitFor(() => expect(onComplete).toHaveBeenCalledWith(response));
    expect(createLoreElement).toHaveBeenCalledTimes(1);
    const [, input] = createLoreElement.mock.calls[0];
    expect(input.operation_key).toMatch(/^lore-create:/);
    expect(input).toMatchObject({
      type_key: "character",
      name: "林渊",
      payload: { appearance: "黑发" },
      field_states: { appearance: "provided" },
      sources: [{
        kind: "manual",
        reference: null,
        excerpt: "林渊有一头黑发。",
        is_primary: true,
      }],
    });
    expect(screen.getByRole("textbox", { name: "外貌" }).tagName).toBe("INPUT");
    expect(localStorage.getItem(draftStorageKey(scope))).toBeNull();
  });

  it("freezes an unknown outcome and only replays the exact request after user action", async () => {
    const createLoreElement = vi.fn()
      .mockRejectedValueOnce(new Error("network lost"))
      .mockResolvedValueOnce({ ...response, replayed: true });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      createLoreElement,
    });
    renderForm();
    await fillValidDraft();
    await userEvent.click(screen.getByRole("button", { name: "创建正式设定" }));

    const replay = await screen.findByRole("button", { name: "核对上次创建结果" });
    const stored = JSON.parse(localStorage.getItem(draftStorageKey(scope))!);
    expect(stored.payload.phase).toBe("outcome_unknown");
    expect(screen.getByRole("textbox", { name: "名称" })).toBeDisabled();
    await userEvent.click(replay);

    await waitFor(() => expect(createLoreElement).toHaveBeenCalledTimes(2));
    expect(createLoreElement.mock.calls[1][1]).toEqual(createLoreElement.mock.calls[0][1]);
  });

  it("restores an unknown result without automatically posting it", async () => {
    const frozenInput = {
      operation_key: "lore-create:1234567890abcdef",
      type_key: "character",
      name: "已提交角色",
      summary: "",
      payload: { appearance: null },
      field_states: { appearance: "unknown" as const },
      sources: [{ kind: "manual", reference: null, is_primary: true }],
    };
    const initial: LoreCreateStoredPayload = {
      operationKey: frozenInput.operation_key,
      draft: {
        typeKey: "character",
        name: "已提交角色",
        summary: "",
        payload: { appearance: "" },
        fieldStates: { appearance: "unknown" },
        sourceReference: "",
        sourceExcerpt: "",
      },
      frozenInput,
      phase: "outcome_unknown",
    };
    const createLoreElement = vi.fn();
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      createLoreElement,
    });
    renderForm(initial);

    expect(await screen.findByText("已恢复这台设备上的未完成草稿。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "核对上次创建结果" })).toBeInTheDocument();
    expect(createLoreElement).not.toHaveBeenCalled();
  });

  it("treats a server error as an unknown outcome and preserves the exact replay", async () => {
    const createLoreElement = vi.fn()
      .mockRejectedValueOnce(new apiModule.ApiError(500, {
        detail: "response failed",
      }))
      .mockResolvedValueOnce({ ...response, replayed: true });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({
      ...apiModule.api,
      createLoreElement,
    });
    renderForm();
    await fillValidDraft();
    await userEvent.click(screen.getByRole("button", { name: "创建正式设定" }));

    const replay = await screen.findByRole("button", { name: "核对上次创建结果" });
    const firstInput = createLoreElement.mock.calls[0][1];
    expect(screen.getByRole("textbox", { name: "名称" })).toBeDisabled();
    expect(JSON.parse(localStorage.getItem(draftStorageKey(scope))!).payload.phase).toBe("outcome_unknown");

    await userEvent.click(replay);
    await waitFor(() => expect(createLoreElement).toHaveBeenCalledTimes(2));
    expect(createLoreElement.mock.calls[1][1]).toEqual(firstInput);
  });

  it("freezes a maintenance response and retries only the preserved request", async () => {
    const createLoreElement = vi.fn()
      .mockRejectedValueOnce(new apiModule.ApiError(503, {
        detail: "writes frozen",
        code: "PROJECT_WRITE_FROZEN",
        retryable: true,
      }))
      .mockResolvedValueOnce({ ...response, replayed: false });
    vi.spyOn(apiModule, "api", "get").mockReturnValue({ ...apiModule.api, createLoreElement });
    renderForm();
    await fillValidDraft();
    await userEvent.click(screen.getByRole("button", { name: "创建正式设定" }));

    const retry = await screen.findByRole("button", { name: "重试上次创建" });
    const firstInput = createLoreElement.mock.calls[0][1];
    expect(screen.getByRole("textbox", { name: "名称" })).toBeDisabled();
    expect(screen.getByRole("alert")).not.toHaveTextContent("操作编号");
    await userEvent.click(retry);

    await waitFor(() => expect(createLoreElement).toHaveBeenCalledTimes(2));
    expect(createLoreElement.mock.calls[1][1]).toEqual(firstInput);
  });

  it("separates idempotency conflicts from retryable element conflicts", async () => {
    const createLoreElement = vi.fn()
      .mockRejectedValueOnce(new apiModule.ApiError(409, {
        detail: "rolled back",
        code: "LORE_ELEMENT_CONFLICT",
        retryable: true,
      }))
      .mockRejectedValueOnce(new apiModule.ApiError(409, {
        detail: "different request",
        code: "LORE_CREATE_IDEMPOTENCY_CONFLICT",
      }));
    vi.spyOn(apiModule, "api", "get").mockReturnValue({ ...apiModule.api, createLoreElement });
    renderForm();
    await fillValidDraft();
    await userEvent.click(screen.getByRole("button", { name: "创建正式设定" }));

    const retry = await screen.findByRole("button", { name: "重试上次创建" });
    const firstInput = createLoreElement.mock.calls[0][1];
    await userEvent.click(retry);
    await waitFor(() => expect(createLoreElement).toHaveBeenCalledTimes(2));
    expect(createLoreElement.mock.calls[1][1]).toEqual(firstInput);
    expect(await screen.findByRole("alert")).toHaveTextContent("服务器记录不一致");
    expect(screen.queryByRole("button", { name: "重试上次创建" })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).not.toHaveTextContent("操作编号");
  });

  it("keeps editable input and focuses the error after a definitive 422", async () => {
    const createLoreElement = vi.fn().mockRejectedValue(new apiModule.ApiError(422, {
      detail: "字段格式不符合要求",
      code: "LORE_FIELD_VALIDATION_FAILED",
    }));
    vi.spyOn(apiModule, "api", "get").mockReturnValue({ ...apiModule.api, createLoreElement });
    renderForm();
    await fillValidDraft();
    await userEvent.click(screen.getByRole("button", { name: "创建正式设定" }));

    const alert = await screen.findByRole("alert");
    await waitFor(() => expect(alert).toHaveFocus());
    expect(screen.getByRole("textbox", { name: "名称" })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "名称" })).toHaveValue("林渊");
  });
});
