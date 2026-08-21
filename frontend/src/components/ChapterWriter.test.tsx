import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as apiModule from "@/services/api";
import type {
  BatchStreamMessage,
  ChapterData,
  ChapterListItem,
  StreamMessage,
  WordCountConfig,
} from "@/types";
import ChapterWriter from "./ChapterWriter";

const chapterOne: ChapterListItem = {
  id: "chapter-1",
  chapter_num: 1,
  title: "潮汐门限",
  status: "generated",
  word_count: 1200,
  revealed_elements: [],
};

const chapterTwo: ChapterListItem = {
  id: "chapter-2",
  chapter_num: 2,
  title: "禁航令",
  status: "generated",
  word_count: 900,
  revealed_elements: [],
};

const wordCounts: WordCountConfig = {
  total_word_count: 3000,
  project_default: 1000,
  chapters: [1, 2, 3].map((chapter_num) => ({
    chapter_num,
    target_word_count: 1000,
    effective_word_count: 1000,
    title: `第${chapter_num}章`,
  })),
};

const chapterDetail = (chapter_num: number, title: string, content: string): ChapterData => ({
  id: `chapter-${chapter_num}`,
  project_id: "project-1",
  chapter_num,
  title,
  content,
  word_count: content.length,
  summary: "",
  status: "generated",
  revealed_elements: [],
});

async function* stream<T>(...messages: T[]): AsyncGenerator<T> {
  for (const message of messages) yield message;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function renderWriter(overrides: Partial<React.ComponentProps<typeof ChapterWriter>> = {}) {
  const onProgress = vi.fn();
  const onBack = vi.fn();
  const view = render(
    <ChapterWriter
      projectId="project-1"
      totalChapters={3}
      onProgress={onProgress}
      onBack={onBack}
      {...overrides}
    />
  );
  return { ...view, onProgress, onBack };
}

function mockApi(overrides: Record<string, unknown> = {}) {
  const mocked = {
    listChapters: vi.fn().mockResolvedValue([]),
    getChapter: vi.fn(),
    updateChapter: vi.fn(),
    getWordCounts: vi.fn().mockResolvedValue(wordCounts),
    saveWordCounts: vi.fn(),
    exportNovel: vi.fn(),
    streamChapter: vi.fn(() => stream<StreamMessage>()),
    streamBatchGenerate: vi.fn(() => stream<BatchStreamMessage>()),
    ...overrides,
  };
  vi.spyOn(apiModule, "api", "get").mockReturnValue({ ...apiModule.api, ...mocked });
  return mocked;
}

describe("ChapterWriter legacy compatibility characterization", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal("alert", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("mounts with the two authoritative reads and no write or stream", async () => {
    const api = mockApi();
    renderWriter();

    expect(await screen.findByRole("button", { name: "生成第1章" })).toBeInTheDocument();
    expect(api.listChapters).toHaveBeenCalledTimes(1);
    expect(api.listChapters).toHaveBeenCalledWith("project-1");
    expect(api.getWordCounts).toHaveBeenCalledTimes(1);
    expect(api.updateChapter).not.toHaveBeenCalled();
    expect(api.saveWordCounts).not.toHaveBeenCalled();
    expect(api.exportNovel).not.toHaveBeenCalled();
    expect(api.streamChapter).not.toHaveBeenCalled();
    expect(api.streamBatchGenerate).not.toHaveBeenCalled();
  });

  it("chooses the next ungenerated chapter, or the final chapter when all are present", async () => {
    const api = mockApi({ listChapters: vi.fn().mockResolvedValue([chapterOne, chapterTwo]) });
    const { rerender } = renderWriter();
    expect(await screen.findByRole("button", { name: "生成第3章" })).toBeInTheDocument();

    api.listChapters.mockResolvedValue([
      chapterOne,
      chapterTwo,
      { ...chapterTwo, id: "chapter-3", chapter_num: 3, title: "第三章" },
    ]);
    rerender(<ChapterWriter projectId="project-2" totalChapters={3} onProgress={vi.fn()} onBack={vi.fn()} />);
    expect(await screen.findByRole("button", { name: "重新生成第3章" })).toBeInTheDocument();
    expect(api.listChapters).toHaveBeenCalledTimes(2);
  });

  it("drops a late chapter detail response after a newer chapter selection", async () => {
    const one = deferred<ChapterData>();
    const two = deferred<ChapterData>();
    const getChapter = vi.fn((_project: string, chapter: number) => chapter === 1 ? one.promise : two.promise);
    mockApi({ listChapters: vi.fn().mockResolvedValue([chapterOne, chapterTwo]), getChapter });
    renderWriter({ totalChapters: 2 });

    await screen.findByText("禁航令");
    await userEvent.click(screen.getByText("潮汐门限"));
    await userEvent.click(screen.getByText("禁航令"));
    await act(async () => two.resolve(chapterDetail(2, "禁航令", "第二章权威正文")));
    expect(await screen.findByText("第二章权威正文")).toBeInTheDocument();
    await act(async () => one.resolve(chapterDetail(1, "潮汐门限", "迟到的第一章")));
    expect(screen.queryByText("迟到的第一章")).not.toBeInTheDocument();
    expect(getChapter).toHaveBeenCalledTimes(2);
  });

  it("renders a single chapter stream in order and refreshes once after complete", async () => {
    const listChapters = vi.fn().mockResolvedValue([]);
    const streamChapter = vi.fn(() => stream<StreamMessage>(
      { type: "metadata", chapter_num: 1, title: "归潮", phase: "opening", phase_label: "开篇" },
      { type: "content", text: "潮声" },
      { type: "content", text: "渐近" },
      { type: "complete", chapter_num: 1, word_count: 4 }
    ));
    mockApi({ listChapters, streamChapter });
    const view = renderWriter();
    await userEvent.click(await screen.findAllByRole("button", { name: "生成第1章" }).then((items) => items.at(-1)!));

    expect(await screen.findByText("潮声渐近")).toBeInTheDocument();
    expect(screen.getByText("归潮")).toBeInTheDocument();
    await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(2));
    expect(streamChapter).toHaveBeenCalledTimes(1);
    expect(streamChapter).toHaveBeenCalledWith("project-1", 1, expect.any(AbortSignal));
    expect(view.onProgress).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["error event", () => stream<StreamMessage>({ type: "error", error: "模型失败" }), "生成失败: 模型失败", 2],
    ["missing terminal", () => stream<StreamMessage>({ type: "content", text: "未确认" }), "生成连接意外中断", 2],
    ["generator throw", async function* () { throw new Error("网络断开"); }, "生成失败: 网络断开", null],
  ])("keeps %s distinct and never retries automatically", async (_name, makeStream, message, listCalls) => {
    const listChapters = vi.fn().mockResolvedValue([]);
    const streamChapter = vi.fn(makeStream);
    mockApi({ listChapters, streamChapter });
    renderWriter();
    await userEvent.click(await screen.findByRole("button", { name: "生成第1章" }));

    await waitFor(() => expect(alert).toHaveBeenCalledWith(expect.stringContaining(message)));
    expect(streamChapter).toHaveBeenCalledTimes(1);
    if (listCalls !== null) expect(listChapters).toHaveBeenCalledTimes(listCalls);
  });

  it("aborts an active chapter stream on unmount without reporting failure", async () => {
    let receivedSignal: AbortSignal | undefined;
    const streamChapter = vi.fn((_project: string, _chapter: number, signal: AbortSignal) => {
      receivedSignal = signal;
      return (async function* () {
        await new Promise<void>((resolve) => signal.addEventListener("abort", () => resolve(), { once: true }));
        throw new DOMException("Aborted", "AbortError");
      })();
    });
    mockApi({ streamChapter });
    const { unmount } = renderWriter();
    await userEvent.click(await screen.findByRole("button", { name: "生成第1章" }));
    await waitFor(() => expect(streamChapter).toHaveBeenCalledTimes(1));
    unmount();

    expect(receivedSignal?.aborted).toBe(true);
    await act(async () => Promise.resolve());
    expect(alert).not.toHaveBeenCalled();
  });

  it("records batch success and failure while preserving the default skip-existing flag", async () => {
    const listChapters = vi.fn().mockResolvedValue([chapterOne]);
    const streamBatchGenerate = vi.fn(() => stream<BatchStreamMessage>(
      { type: "batch_start", total_to_generate: 2 },
      { type: "batch_progress", current: 1, total: 2, chapter_num: 2 },
      { type: "complete", chapter_num: 2, word_count: 1100 },
      { type: "batch_progress", current: 2, total: 2, chapter_num: 3 },
      { type: "error", chapter_num: 3, error: "生成失败" },
      { type: "batch_complete", total_generated: 1, failed_chapters: [3] }
    ));
    mockApi({ listChapters, streamBatchGenerate });
    const view = renderWriter();
    await userEvent.click(screen.getAllByRole("button", { name: "一键生成所有章节" }).at(-1)!);

    expect(await screen.findByText("批量生成完成 — 成功 1 章，失败 1 章")).toBeInTheDocument();
    expect(screen.getAllByText("第2章").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("第3章").length).toBeGreaterThanOrEqual(1);
    expect(streamBatchGenerate).toHaveBeenCalledWith("project-1", true, expect.any(AbortSignal));
    expect(streamBatchGenerate).toHaveBeenCalledTimes(1);
    expect(listChapters).toHaveBeenCalledTimes(2);
    expect(view.onProgress).toHaveBeenCalledTimes(1);
  });

  it("passes an explicit skip-existing change to one batch stream", async () => {
    const streamBatchGenerate = vi.fn(() => stream<BatchStreamMessage>({ type: "batch_complete" }));
    mockApi({ streamBatchGenerate });
    renderWriter();
    const checkbox = await screen.findByRole("checkbox", { name: "跳过已生成章节" });
    await userEvent.click(checkbox);
    await userEvent.click(screen.getByRole("button", { name: "一键生成所有章节" }));
    await waitFor(() => expect(streamBatchGenerate).toHaveBeenCalledTimes(1));
    expect(streamBatchGenerate).toHaveBeenCalledWith("project-1", false, expect.any(AbortSignal));
  });

  it("treats a batch stream without batch_complete as interrupted and never retries", async () => {
    const streamBatchGenerate = vi.fn(() => stream<BatchStreamMessage>({ type: "batch_start", total_to_generate: 1 }));
    mockApi({ streamBatchGenerate });
    const view = renderWriter();
    await userEvent.click(screen.getAllByRole("button", { name: "一键生成所有章节" }).at(-1)!);

    await waitFor(() => expect(alert).toHaveBeenCalledWith("批量生成连接意外中断，请检查已保存章节后重试"));
    expect(streamBatchGenerate).toHaveBeenCalledTimes(1);
    expect(view.onProgress).toHaveBeenCalledTimes(1);
  });

  it("keeps a thrown batch stream distinct from a terminal event and never retries", async () => {
    const listChapters = vi.fn().mockResolvedValue([]);
    const streamBatchGenerate = vi.fn(async function* () {
      throw new Error("批量网络断开");
    });
    mockApi({ listChapters, streamBatchGenerate });
    const { onProgress } = renderWriter();
    await userEvent.click(await screen.findByRole("button", { name: "一键生成所有章节" }));

    await waitFor(() => expect(alert).toHaveBeenCalledWith("批量生成失败: 批量网络断开"));
    expect(streamBatchGenerate).toHaveBeenCalledTimes(1);
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(onProgress).not.toHaveBeenCalled();
  });

  it("aborts an active batch stream on unmount without retrying or reporting failure", async () => {
    let receivedSignal: AbortSignal | undefined;
    const streamBatchGenerate = vi.fn((_project: string, _skip: boolean, signal: AbortSignal) => {
      receivedSignal = signal;
      return (async function* () {
        await new Promise<void>((resolve) => signal.addEventListener("abort", () => resolve(), { once: true }));
        throw new DOMException("Aborted", "AbortError");
      })();
    });
    mockApi({ streamBatchGenerate });
    const { unmount, onProgress } = renderWriter();
    await userEvent.click(await screen.findByRole("button", { name: "一键生成所有章节" }));
    await waitFor(() => expect(streamBatchGenerate).toHaveBeenCalledTimes(1));
    unmount();

    expect(receivedSignal?.aborted).toBe(true);
    await act(async () => Promise.resolve());
    expect(alert).not.toHaveBeenCalled();
    expect(onProgress).not.toHaveBeenCalled();
  });

  it("saves an explicitly edited title and body once, then refreshes progress", async () => {
    const listChapters = vi.fn().mockResolvedValue([chapterOne]);
    const getChapter = vi.fn().mockResolvedValue(chapterDetail(1, chapterOne.title, "旧正文"));
    const updateChapter = vi.fn().mockResolvedValue(chapterDetail(1, "新标题", "新正文"));
    mockApi({ listChapters, getChapter, updateChapter });
    const { onProgress } = renderWriter({ totalChapters: 1 });
    await userEvent.click(await screen.findByText(chapterOne.title));
    expect(await screen.findByText("旧正文")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "编辑" }));
    const title = screen.getAllByRole("textbox").find((node) => node.tagName === "INPUT")!;
    const body = screen.getByDisplayValue("旧正文");
    await userEvent.clear(title);
    await userEvent.type(title, "新标题");
    await userEvent.clear(body);
    await userEvent.type(body, "新正文");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(updateChapter).toHaveBeenCalledTimes(1));
    expect(updateChapter).toHaveBeenCalledWith("project-1", 1, { title: "新标题", content: "新正文" });
    expect(listChapters).toHaveBeenCalledTimes(2);
    expect(onProgress).toHaveBeenCalledTimes(1);
  });

  it("keeps the editor content after a single failed save", async () => {
    const updateChapter = vi.fn().mockRejectedValue(new Error("写入失败"));
    mockApi({
      listChapters: vi.fn().mockResolvedValue([chapterOne]),
      getChapter: vi.fn().mockResolvedValue(chapterDetail(1, chapterOne.title, "保留正文")),
      updateChapter,
    });
    renderWriter({ totalChapters: 1 });
    await userEvent.click(await screen.findByText(chapterOne.title));
    await screen.findByText("保留正文");
    await userEvent.click(screen.getByRole("button", { name: "编辑" }));
    const body = screen.getByDisplayValue("保留正文");
    await userEvent.type(body, "，继续保留");
    await userEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(alert).toHaveBeenCalledWith("保存失败: 写入失败"));
    expect(updateChapter).toHaveBeenCalledTimes(1);
    expect(screen.getByDisplayValue("保留正文，继续保留")).toBeInTheDocument();
  });

  it("distributes a total with a remainder and saves the exact chapter targets once", async () => {
    const saveWordCounts = vi.fn().mockResolvedValue(wordCounts);
    const getWordCounts = vi.fn().mockResolvedValue(wordCounts);
    mockApi({ saveWordCounts, getWordCounts });
    renderWriter();
    await userEvent.click(await screen.findByText(/^字数设置/));
    const total = screen.getByPlaceholderText("如 60000");
    await userEvent.clear(total);
    await userEvent.type(total, "1501");
    await userEvent.click(screen.getByRole("button", { name: "自动分配" }));
    expect(screen.getByDisplayValue("501")).toBeInTheDocument();
    expect(screen.getAllByDisplayValue("500")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "保存字数设置" }));

    await waitFor(() => expect(saveWordCounts).toHaveBeenCalledTimes(1));
    expect(saveWordCounts).toHaveBeenCalledWith("project-1", {
      total_word_count: 1501,
      chapters: [
        { chapter_num: 1, target_word_count: 501 },
        { chapter_num: 2, target_word_count: 500 },
        { chapter_num: 3, target_word_count: 500 },
      ],
    });
    expect(getWordCounts).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["1499", "总字数不能少于 1500"],
    ["30001", "总字数不能超过 30000"],
  ])("rejects total word count %s before any save", async (value, message) => {
    const saveWordCounts = vi.fn();
    mockApi({ saveWordCounts });
    renderWriter();
    await userEvent.click(await screen.findByText(/^字数设置/));
    const total = screen.getByPlaceholderText("如 60000");
    await userEvent.clear(total);
    await userEvent.type(total, value);
    await userEvent.click(screen.getByRole("button", { name: "自动分配" }));

    expect(await screen.findByText((content) => content.includes(message))).toBeInTheDocument();
    expect(saveWordCounts).not.toHaveBeenCalled();
  });

  it("exports loaded content once and revokes the generated object URL", async () => {
    const blob = new Blob(["正文"], { type: "text/plain" });
    const exportNovel = vi.fn().mockResolvedValue(blob);
    const createObjectURL = vi.fn().mockReturnValue("blob:chapter-export");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });
    let clickedAnchor: HTMLAnchorElement | null = null;
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clickedAnchor = this;
    });
    mockApi({
      listChapters: vi.fn().mockResolvedValue([chapterOne]),
      getChapter: vi.fn().mockResolvedValue(chapterDetail(1, chapterOne.title, "已保存正文")),
      exportNovel,
    });
    renderWriter({ totalChapters: 1 });
    await userEvent.click(await screen.findByText(chapterOne.title));
    await screen.findByText("已保存正文");
    await userEvent.click(screen.getByRole("button", { name: "导出全文" }));

    await waitFor(() => expect(exportNovel).toHaveBeenCalledTimes(1));
    expect(exportNovel).toHaveBeenCalledWith("project-1", "txt");
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(click).toHaveBeenCalledTimes(1);
    expect(clickedAnchor).toMatchObject({ href: "blob:chapter-export", download: "project-1.txt" });
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:chapter-export");
  });

  it("calls the provided back action without a network write", async () => {
    const api = mockApi();
    const { onBack } = renderWriter();
    await userEvent.click(await screen.findByRole("button", { name: "← 返回世界观与设定" }));
    expect(onBack).toHaveBeenCalledTimes(1);
    expect(api.updateChapter).not.toHaveBeenCalled();
    expect(api.saveWordCounts).not.toHaveBeenCalled();
    expect(api.streamChapter).not.toHaveBeenCalled();
    expect(api.streamBatchGenerate).not.toHaveBeenCalled();
  });
});
