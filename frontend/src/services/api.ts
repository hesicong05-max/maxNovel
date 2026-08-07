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
  LoreCandidate,
  LoreCandidateActionInput,
  LoreCandidateActionResponse,
  LoreCandidateEditInput,
  LoreCandidateFilters,
  LoreCandidateInboxResponse,
  LoreElementCreateInput,
  LoreElementCreateResponse,
  LoreElementDetail,
  LoreElementFilters,
  LoreElementStateInput,
  LoreElementUpdateInput,
  LoreElementWriteResponse,
  LoreExtractionBatch,
  LoreListResponse,
  LoreMergePreviewInput,
  LoreMergePreviewResponse,
  LoreMergeCommitInput,
  LoreMergeOperation,
  LoreMergeOperationsResponse,
  LoreMigrationCommitInput,
  LoreMigrationOperation,
  LoreMigrationPreviewResponse,
  LoreOverview,
  LoreRelation,
  LoreRelationCreateInput,
  LoreRelationCreateResponse,
  LoreRelationListResponse,
  LoreRelationStateInput,
  LoreRelationType,
  LoreRelationUpdateInput,
  LoreReviewDecisionInput,
  LoreReviewDecisionResponse,
  LoreReviewDetail,
  LoreManualReviewCreateInput,
  LoreManualReviewCreateResponse,
  LoreReviewListResponse,
  LoreReviewScanResponse,
  LoreTypesResponse,
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
  readonly suggestionId?: string;
  readonly reloadRequired: boolean;
  readonly outcomeUnknown: boolean;

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
    this.suggestionId = data.suggestion_id;
    this.reloadRequired = data.reload_required === true;
    this.outcomeUnknown = data.outcome_unknown === true;
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
    retryable: payload.retryable === true || nestedDetail.retryable === true,
    retry_after_seconds:
      optionalPositiveNumber(payload.retry_after_seconds) ||
      (Number.isFinite(retryHeader) && retryHeader > 0
        ? retryHeader
        : undefined),
    event_id: optionalString(payload.event_id),
    suggestion_id:
      optionalString(payload.suggestion_id) || optionalString(nestedDetail.suggestion_id),
    reload_required:
      payload.reload_required === true || nestedDetail.reload_required === true,
    outcome_unknown:
      payload.outcome_unknown === true || nestedDetail.outcome_unknown === true,
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

  createLoreExtraction: (
    projectId: string,
    data: {
      idempotency_key: string;
      document_text: string;
      source_kind: string;
      source_ref?: string | null;
    }
  ) => fetchJSON<LoreExtractionBatch>(
    `/projects/${projectId}/lore/extractions`,
    { method: "POST", body: JSON.stringify(data) }
  ),

  // Unified lore repository (read-only first slice)
  getLoreOverview: (projectId: string, signal?: AbortSignal) =>
    fetchJSON<LoreOverview>(`/projects/${projectId}/lore/overview`, { signal }),
  getLoreMigrationPreview: (projectId: string, signal?: AbortSignal) =>
    fetchJSON<LoreMigrationPreviewResponse>(
      `/projects/${projectId}/lore/migration-preview`,
      { signal }
    ),
  commitLoreMigration: (projectId: string, data: LoreMigrationCommitInput) =>
    fetchJSON<LoreMigrationOperation>(
      `/projects/${projectId}/lore/migration-operations`,
      { method: "POST", body: JSON.stringify(data) }
    ),
  getLoreMigrationOperationByKey: (
    projectId: string,
    operationKey: string,
    signal?: AbortSignal
  ) => fetchJSON<LoreMigrationOperation>(
    `/projects/${projectId}/lore/migration-operations/by-key/${encodeURIComponent(operationKey)}`,
    { signal }
  ),
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
  createLoreElement: (projectId: string, data: LoreElementCreateInput) =>
    fetchJSON<LoreElementCreateResponse>(
      `/projects/${projectId}/lore/elements`,
      { method: "POST", body: JSON.stringify(data) }
    ),
  updateLoreElement: (
    projectId: string,
    elementId: string,
    data: LoreElementUpdateInput
  ) =>
    fetchJSON<LoreElementWriteResponse>(
      `/projects/${projectId}/lore/elements/${elementId}`,
      { method: "PATCH", body: JSON.stringify(data) }
    ),
  changeLoreElementState: (
    projectId: string,
    elementId: string,
    action: "enable" | "disable" | "archive" | "restore-archive",
    data: LoreElementStateInput
  ) =>
    fetchJSON<LoreElementWriteResponse>(
      `/projects/${projectId}/lore/elements/${elementId}/${action}`,
      { method: "POST", body: JSON.stringify(data) }
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
  listLoreTypes: (projectId: string, signal?: AbortSignal) =>
    fetchJSON<LoreTypesResponse>(`/projects/${projectId}/lore/types`, { signal }),
  listLoreRelationTypes: (projectId: string, signal?: AbortSignal) =>
    fetchJSON<{ items: LoreRelationType[] }>(
      `/projects/${projectId}/lore/relation-types`,
      { signal }
    ),
  listLoreRelations: (
    projectId: string,
    elementId: string,
    filters: { status?: "active" | "archived"; cursor?: string; limit?: number } = {},
    signal?: AbortSignal
  ) => fetchJSON<LoreRelationListResponse>(
    withQuery(`/projects/${projectId}/lore/elements/${elementId}/relations`, filters),
    { signal }
  ),
  getLoreRelation: (projectId: string, relationId: string, signal?: AbortSignal) =>
    fetchJSON<LoreRelation>(
      `/projects/${projectId}/lore/relations/${relationId}`,
      { signal }
    ),
  createLoreRelation: (
    projectId: string,
    sourceElementId: string,
    data: LoreRelationCreateInput
  ) => fetchJSON<LoreRelationCreateResponse>(
    `/projects/${projectId}/lore/elements/${sourceElementId}/relations`,
    { method: "POST", body: JSON.stringify(data) }
  ),
  updateLoreRelation: (
    projectId: string,
    relationId: string,
    data: LoreRelationUpdateInput
  ) => fetchJSON<LoreRelation>(
    `/projects/${projectId}/lore/relations/${relationId}`,
    { method: "PATCH", body: JSON.stringify(data) }
  ),
  changeLoreRelationState: (
    projectId: string,
    relationId: string,
    action: "archive" | "restore",
    data: LoreRelationStateInput
  ) => fetchJSON<LoreRelation>(
    `/projects/${projectId}/lore/relations/${relationId}/${action}`,
    { method: "POST", body: JSON.stringify(data) }
  ),
  scanLoreReviews: (projectId: string) =>
    fetchJSON<LoreReviewScanResponse>(
      `/projects/${projectId}/lore/reviews/scan`,
      { method: "POST" }
    ),
  createManualLoreReview: (projectId: string, data: LoreManualReviewCreateInput) =>
    fetchJSON<LoreManualReviewCreateResponse>(
      `/projects/${projectId}/lore/reviews/manual`,
      { method: "POST", body: JSON.stringify(data) }
    ),
  listLoreReviews: (
    projectId: string,
    filters: {
      q?: string;
      kind?: "possible_duplicate" | "possible_conflict";
      review_status?: string;
      cursor?: string;
      limit?: number;
    } = {},
    signal?: AbortSignal
  ) => fetchJSON<LoreReviewListResponse>(
    withQuery(`/projects/${projectId}/lore/reviews`, filters),
    { signal }
  ),
  getLoreReview: (projectId: string, suggestionId: string, signal?: AbortSignal) =>
    fetchJSON<LoreReviewDetail>(
      `/projects/${projectId}/lore/reviews/${suggestionId}`,
      { signal }
    ),
  decideLoreReview: (
    projectId: string,
    suggestionId: string,
    data: LoreReviewDecisionInput
  ) => fetchJSON<LoreReviewDecisionResponse>(
    `/projects/${projectId}/lore/reviews/${suggestionId}/decide`,
    { method: "POST", body: JSON.stringify(data) }
  ),
  previewLoreMerge: (
    projectId: string,
    suggestionId: string,
    data: LoreMergePreviewInput
  ) => fetchJSON<LoreMergePreviewResponse>(
    `/projects/${projectId}/lore/reviews/${suggestionId}/merge-preview`,
    { method: "POST", body: JSON.stringify(data) }
  ),
  commitLoreMerge: (
    projectId: string,
    suggestionId: string,
    data: LoreMergeCommitInput
  ) => fetchJSON<LoreMergeOperation>(
    `/projects/${projectId}/lore/reviews/${suggestionId}/merge-commit`,
    { method: "POST", body: JSON.stringify(data) }
  ),
  getLoreMergeOperationByKey: (
    projectId: string,
    operationKey: string,
    signal?: AbortSignal
  ) => fetchJSON<LoreMergeOperation>(
    `/projects/${projectId}/lore/merge-operations/by-key/${encodeURIComponent(operationKey)}`,
    { signal }
  ),
  listLoreElementMergeHistory: (
    projectId: string,
    elementId: string,
    signal?: AbortSignal
  ) => fetchJSON<LoreMergeOperationsResponse>(
    `/projects/${projectId}/lore/elements/${elementId}/merge-history`,
    { signal }
  ),
  getLoreCandidate: (
    projectId: string,
    batchId: string,
    candidateId: string,
    signal?: AbortSignal
  ) =>
    fetchJSON<LoreCandidate>(
      `/projects/${projectId}/lore/extractions/${batchId}/candidates/${candidateId}`,
      { signal }
    ),
  editLoreCandidate: (
    projectId: string,
    batchId: string,
    candidateId: string,
    data: LoreCandidateEditInput
  ) =>
    fetchJSON<LoreCandidate>(
      `/projects/${projectId}/lore/extractions/${batchId}/candidates/${candidateId}`,
      { method: "PATCH", body: JSON.stringify(data) }
    ),
  acceptLoreCandidate: (
    projectId: string,
    batchId: string,
    candidateId: string,
    data: LoreCandidateActionInput
  ) =>
    fetchJSON<LoreCandidateActionResponse>(
      `/projects/${projectId}/lore/extractions/${batchId}/candidates/${candidateId}/accept`,
      { method: "POST", body: JSON.stringify(data) }
    ),
  rejectLoreCandidate: (
    projectId: string,
    batchId: string,
    candidateId: string,
    data: LoreCandidateActionInput
  ) =>
    fetchJSON<LoreCandidateActionResponse>(
      `/projects/${projectId}/lore/extractions/${batchId}/candidates/${candidateId}/reject`,
      { method: "POST", body: JSON.stringify(data) }
    ),

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
