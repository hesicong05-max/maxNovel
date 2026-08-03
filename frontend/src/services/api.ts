import type {
  AuthResponse,
  AuthUser,
  ApiErrorData,
  BatchStreamMessage,
  ChapterData,
  ChapterListItem,
  CommunityNovelBrief,
  CommunityNovelCreate,
  CommunityNovelDetail,
  CommunityNovelUpdate,
  CommunityTag,
  LoginRequest,
  OutlineData,
  OutlineStreamMessage,
  ProgressData,
  Project,
  ProjectStats,
  RegisterRequest,
  StreamMessage,
  WordCountConfig,
  WorldviewData,
  WorldviewElement,
  WorldviewImportResult,
} from "@/types";
import type {
  LoreCandidateFilters,
  LoreCandidateInboxResponse,
  LoreElementDetail,
  LoreElementFilters,
  LoreListResponse,
  LoreOverview,
} from "@/types/lore";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly code?: string;
  readonly maintenanceState?: string;
  readonly retryable: boolean;
  readonly retryAfterSeconds?: number;
  readonly eventId?: string;
  readonly reloadRequired: boolean;

  constructor(status: number, data: ApiErrorData) {
    super(data.detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = data.detail;
    this.code = data.code;
    this.maintenanceState = data.maintenance_state;
    this.retryable = data.retryable === true;
    this.retryAfterSeconds = data.retry_after_seconds;
    this.eventId = data.event_id;
    this.reloadRequired = data.reload_required === true;
  }
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function optionalPositiveNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : undefined;
}

async function responseApiError(response: Response): Promise<ApiError> {
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    value = null;
  }
  const payload =
    value && typeof value === "object"
      ? (value as Record<string, unknown>)
      : {};
  const nestedDetail =
    payload.detail && typeof payload.detail === "object"
      ? (payload.detail as Record<string, unknown>)
      : {};
  const retryHeader = Number(response.headers.get("Retry-After"));
  return new ApiError(response.status, {
    detail:
      optionalString(payload.detail) ||
      optionalString(nestedDetail.message) ||
      (response.statusText
        ? response.statusText
        : `HTTP ${response.status}`),
    code: optionalString(payload.code) || optionalString(nestedDetail.code),
    maintenance_state: optionalString(payload.maintenance_state),
    retryable: payload.retryable === true,
    retry_after_seconds:
      optionalPositiveNumber(payload.retry_after_seconds) ||
      (Number.isFinite(retryHeader) && retryHeader > 0
        ? retryHeader
        : undefined),
    event_id: optionalString(payload.event_id),
    reload_required:
      payload.reload_required === true || nestedDetail.reload_required === true,
  });
}

function withQuery(
  path: string,
  values: object
): string {
  const params = new URLSearchParams();
  Object.entries(values as Record<string, unknown>).forEach(([key, value]) => {
    if (
      typeof value === "string" ||
      typeof value === "number" ||
      typeof value === "boolean"
    ) {
      if (value !== "") params.set(key, String(value));
    }
  });
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

async function throwResponseError(response: Response): Promise<never> {
  throw await responseApiError(response);
}

export function isProjectWriteFrozenError(
  error: unknown
): error is ApiError {
  return (
    error instanceof ApiError &&
    error.status === 503 &&
    error.code === "PROJECT_WRITE_FROZEN"
  );
}

export function isProjectWriteFrozenData(
  error: unknown
): error is ApiErrorData {
  return (
    !!error &&
    typeof error === "object" &&
    (error as Partial<ApiErrorData>).code === "PROJECT_WRITE_FROZEN"
  );
}

// === Auth Token Management ===

const TOKEN_KEY = "novel_auth_token";

export function getAuthToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setAuthToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearAuthToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// === Global 401 handler ===

let onUnauthorized: (() => void) | null = null;

export function setOnUnauthorized(cb: (() => void) | null): void {
  onUnauthorized = cb;
}

// === Core fetch helpers ===

async function fetchJSON<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${url}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...options?.headers,
    },
  });
  if (resp.status === 401) {
    clearAuthToken();
    onUnauthorized?.();
    throw new ApiError(401, { detail: "登录已过期，请重新登录" });
  }
  if (!resp.ok) {
    await throwResponseError(resp);
  }
  return resp.json();
}

/** Fetch with auth headers but no JSON content-type (for FormData uploads) */
async function fetchWithAuth(url: string, options?: RequestInit): Promise<Response> {
  const resp = await fetch(`${API_BASE}${url}`, {
    ...options,
    headers: {
      ...authHeaders(),
      ...options?.headers,
    },
  });
  if (resp.status === 401) {
    clearAuthToken();
    onUnauthorized?.();
    throw new ApiError(401, { detail: "登录已过期，请重新登录" });
  }
  return resp;
}

// === Projects ===

export const api = {
  // ── Authentication ──
  register: (data: RegisterRequest) =>
    fetchJSON<AuthResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  login: (data: LoginRequest) =>
    fetchJSON<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  getMe: () => fetchJSON<AuthUser>("/auth/me"),

  // Projects
  listProjects: () => fetchJSON<Project[]>("/projects"),
  createProject: (data: {
    title: string;
    genre: string;
    total_chapters: number;
    chapter_word_count: number;
    style_intensity: string;
  }) => fetchJSON<Project>("/projects", { method: "POST", body: JSON.stringify(data) }),
  getProject: (id: string) => fetchJSON<Project>(`/projects/${id}`),
  deleteProject: (id: string) =>
    fetchJSON<{ message: string }>(`/projects/${id}`, { method: "DELETE" }),

  // Worldview
  getWorldview: (projectId: string) =>
    fetchJSON<WorldviewData & { id: string; parsed_elements: WorldviewElement[]; source: string }>(
      `/worldview/${projectId}`
    ),
  setWorldview: (projectId: string, data: WorldviewData) =>
    fetchJSON<WorldviewData & { id: string; parsed_elements: WorldviewElement[]; source: string }>(
      `/worldview/${projectId}`,
      { method: "POST", body: JSON.stringify(data) }
    ),
  importWorldview: (projectId: string, documentText: string, genre: string) =>
    fetchJSON<WorldviewImportResult>(`/worldview/${projectId}/import`, {
      method: "POST",
      body: JSON.stringify({ document_text: documentText, genre }),
    }),
  uploadWorldviewFile: async (projectId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    const resp = await fetchWithAuth(`/worldview/${projectId}/upload-file`, {
      method: "POST",
      body: formData,
    });
    if (!resp.ok) {
      await throwResponseError(resp);
    }
    return resp.json() as Promise<{ text: string; filename: string; char_count: number }>;
  },

  // Unified lore repository (read-only first slice)
  getLoreOverview: (projectId: string, signal?: AbortSignal) =>
    fetchJSON<LoreOverview>(`/projects/${projectId}/lore/overview`, { signal }),
  listLoreElements: (
    projectId: string,
    filters: LoreElementFilters = {},
    signal?: AbortSignal
  ) =>
    fetchJSON<LoreListResponse>(
      withQuery(`/projects/${projectId}/lore/elements`, filters),
      { signal }
    ),
  getLoreElement: (projectId: string, elementId: string, signal?: AbortSignal) =>
    fetchJSON<LoreElementDetail>(
      `/projects/${projectId}/lore/elements/${elementId}`,
      { signal }
    ),
  listLoreCandidates: (
    projectId: string,
    filters: LoreCandidateFilters = {},
    signal?: AbortSignal
  ) =>
    fetchJSON<LoreCandidateInboxResponse>(
      withQuery(`/projects/${projectId}/lore/extractions/candidates`, filters),
      { signal }
    ),

  // Outline
  getOutline: (projectId: string) =>
    fetchJSON<OutlineData>(`/outline/${projectId}`),
  generateOutline: (projectId: string) =>
    fetchJSON<OutlineData>(`/outline/${projectId}/generate`, { method: "POST" }),
  generateOutlineStream: async function* (
    projectId: string,
    signal?: AbortSignal
  ): AsyncGenerator<OutlineStreamMessage> {
    const resp = await fetchWithAuth(
      `/outline/${projectId}/generate-stream`,
      { method: "POST", signal }
    );

    if (!resp.ok) {
      await throwResponseError(resp);
    }

    const reader = resp.body?.getReader();
    if (!reader) throw new Error("No response body");

    const decoder = new TextDecoder();
    let buffer = "";

    function parseLine(line: string): OutlineStreamMessage | null {
      if (!line.startsWith("data: ")) return null;
      const dataStr = line.slice(6).trim();
      if (!dataStr) return null;
      try {
        return JSON.parse(dataStr) as OutlineStreamMessage;
      } catch {
        return null;
      }
    }

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const events = buffer.split("\n\n");
      buffer = events.pop() || "";

      for (const evt of events) {
        for (const line of evt.split("\n")) {
          const msg = parseLine(line);
          if (msg) yield msg;
        }
      }
    }

    if (buffer.trim()) {
      for (const line of buffer.split("\n")) {
        const msg = parseLine(line);
        if (msg) yield msg;
      }
    }
  },
  updateOutline: (projectId: string, data: { story_arc: string; chapters: OutlineData["chapters"] }) =>
    fetchJSON<OutlineData>(`/outline/${projectId}`, { method: "PUT", body: JSON.stringify(data) }),
  confirmOutline: (projectId: string) =>
    fetchJSON<{ message: string }>(`/outline/${projectId}/confirm`, { method: "POST" }),

  // Chapters
  listChapters: (projectId: string) =>
    fetchJSON<ChapterListItem[]>(`/chapters/${projectId}`),
  getChapter: (projectId: string, chapterNum: number) =>
    fetchJSON<ChapterData>(`/chapters/${projectId}/${chapterNum}`),
  updateChapter: (projectId: string, chapterNum: number, data: { title?: string; content?: string }) =>
    fetchJSON<ChapterData>(`/chapters/${projectId}/${chapterNum}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  // Progress
  getProgress: (projectId: string) =>
    fetchJSON<ProgressData>(`/chapters/${projectId}/progress`),

  // Export — uses fetchWithAuth to include Authorization header
  exportNovel: async (projectId: string, format: "txt" | "markdown"): Promise<Blob> => {
    const resp = await fetchWithAuth(`/export/${projectId}/${format}`);
    if (!resp.ok) {
      await throwResponseError(resp);
    }
    return resp.blob();
  },

  // Settings
  getLLMSettings: () =>
    fetchJSON<{
      api_key: string;
      base_url: string;
      model: string;
      temperature: number;
      max_tokens: number;
      is_configured: boolean;
    }>("/settings/llm"),
  saveLLMSettings: (data: {
    api_key: string;
    base_url: string;
    model: string;
    temperature: number;
    max_tokens: number;
  }) =>
    fetchJSON<{
      api_key: string;
      base_url: string;
      model: string;
      temperature: number;
      max_tokens: number;
      is_configured: boolean;
    }>("/settings/llm", { method: "POST", body: JSON.stringify(data) }),
  testLLMConnection: () =>
    fetchJSON<{ success: boolean; reply: string | null; model: string | null; error: string | null }>(
      "/settings/llm/test",
      { method: "POST" }
    ),
  getProviders: () =>
    fetchJSON<{
      providers: Array<{
        name: string;
        base_url: string;
        models: string[];
      }>;
    }>("/settings/llm/providers"),

  // Chapter streaming
  streamChapter: async function* (
    projectId: string,
    chapterNum: number,
    signal?: AbortSignal
  ): AsyncGenerator<StreamMessage> {
    const resp = await fetchWithAuth(
      `/chapters/${projectId}/${chapterNum}/generate`,
      { method: "POST", signal }
    );

    if (!resp.ok) {
      await throwResponseError(resp);
    }

    const reader = resp.body?.getReader();
    if (!reader) throw new Error("No response body");

    const decoder = new TextDecoder();
    let buffer = "";

    function parseLine(line: string): StreamMessage | null {
      if (!line.startsWith("data: ")) return null;
      const dataStr = line.slice(6).trim();
      if (!dataStr) return null;
      try {
        return JSON.parse(dataStr) as StreamMessage;
      } catch {
        return null;
      }
    }

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Process SSE events — split on double newline (SSE event boundary)
      const events = buffer.split("\n\n");
      buffer = events.pop() || "";

      for (const evt of events) {
        for (const line of evt.split("\n")) {
          const msg = parseLine(line);
          if (msg) yield msg;
        }
      }
    }

    // Process any remaining data in buffer after stream closes
    if (buffer.trim()) {
      for (const line of buffer.split("\n")) {
        const msg = parseLine(line);
        if (msg) yield msg;
      }
    }
  },

  // === Word Count Configuration ===

  getWordCounts: (projectId: string) =>
    fetchJSON<WordCountConfig>(`/chapters/${projectId}/word-counts`),

  saveWordCounts: (projectId: string, data: {
    total_word_count: number | null;
    chapters: { chapter_num: number; target_word_count: number | null }[];
  }) =>
    fetchJSON<WordCountConfig>(`/chapters/${projectId}/word-counts`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  // === Batch Chapter Generation ===

  streamBatchGenerate: async function* (
    projectId: string,
    skipExisting: boolean = true,
    signal?: AbortSignal
  ): AsyncGenerator<BatchStreamMessage> {
    const resp = await fetchWithAuth(
      `/chapters/${projectId}/generate-all?skip_existing=${skipExisting}`,
      { method: "POST", signal }
    );

    if (!resp.ok) {
      await throwResponseError(resp);
    }

    const reader = resp.body?.getReader();
    if (!reader) throw new Error("No response body");

    const decoder = new TextDecoder();
    let buffer = "";

    function parseLine(line: string): BatchStreamMessage | null {
      if (!line.startsWith("data: ")) return null;
      const dataStr = line.slice(6).trim();
      if (!dataStr) return null;
      try {
        return JSON.parse(dataStr) as BatchStreamMessage;
      } catch {
        return null;
      }
    }

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const events = buffer.split("\n\n");
      buffer = events.pop() || "";

      for (const evt of events) {
        for (const line of evt.split("\n")) {
          const msg = parseLine(line);
          if (msg) yield msg;
        }
      }
    }

    if (buffer.trim()) {
      for (const line of buffer.split("\n")) {
        const msg = parseLine(line);
        if (msg) yield msg;
      }
    }
  },

  // ═══ Community ═══

  listCommunityNovels: (params?: {
    offset?: number;
    limit?: number;
    tag?: string;
    sort?: "latest" | "popular" | "random";
  }) => {
    const qs = new URLSearchParams();
    if (params?.offset != null) qs.set("offset", String(params.offset));
    if (params?.limit != null) qs.set("limit", String(params.limit));
    if (params?.tag) qs.set("tag", params.tag);
    if (params?.sort) qs.set("sort", params.sort);
    const queryStr = qs.toString();
    return fetchJSON<CommunityNovelBrief[]>(
      `/community/novels${queryStr ? `?${queryStr}` : ""}`
    );
  },

  getRandomNovels: (limit: number = 6, excludeIds: string[] = []) =>
    fetchJSON<CommunityNovelBrief[]>(
      `/community/novels/random?limit=${limit}&exclude=${excludeIds.join(",")}`
    ),

  getCommunityNovel: (novelId: string) =>
    fetchJSON<CommunityNovelDetail>(`/community/novels/${novelId}`),

  uploadCommunityNovel: (data: CommunityNovelCreate) =>
    fetchJSON<CommunityNovelDetail>(`/community/novels`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateCommunityNovel: (novelId: string, data: CommunityNovelUpdate) =>
    fetchJSON<CommunityNovelDetail>(`/community/novels/${novelId}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  deleteCommunityNovel: (novelId: string) =>
    fetchJSON<{ message: string }>(`/community/novels/${novelId}`, {
      method: "DELETE",
    }),

  likeCommunityNovel: (novelId: string) =>
    fetchJSON<{ like_count: number; already_liked?: boolean }>(`/community/novels/${novelId}/like`, {
      method: "POST",
    }),

  getCommunityTags: () =>
    fetchJSON<CommunityTag[]>(`/community/tags`),

  getProjectStats: (projectId: string) =>
    fetchJSON<ProjectStats>(`/community/projects/${projectId}/stats`),
};
