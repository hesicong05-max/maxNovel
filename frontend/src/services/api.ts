import type {
  AuthResponse,
  AuthUser,
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

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

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
    // Return a never-resolving promise to prevent error alerts from flashing
    // before the redirect to /login takes effect
    return new Promise<T>(() => {});
  }
  if (!resp.ok) {
    const error = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(error.detail || `HTTP ${resp.status}`);
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
    // Return a promise that never resolves — the redirect will unmount the component
    return new Promise<Response>(() => {});
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
      const error = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(error.detail || `HTTP ${resp.status}`);
    }
    return resp.json() as Promise<{ text: string; filename: string; char_count: number }>;
  },

  // Outline
  getOutline: (projectId: string) =>
    fetchJSON<OutlineData>(`/outline/${projectId}`),
  generateOutline: (projectId: string) =>
    fetchJSON<OutlineData>(`/outline/${projectId}/generate`, { method: "POST" }),
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
      const error = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(error.detail || `HTTP ${resp.status}`);
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
      throw new Error(`HTTP ${resp.status}`);
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
      throw new Error(`HTTP ${resp.status}`);
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
    fetchJSON<{ like_count: number }>(`/community/novels/${novelId}/like`, {
      method: "POST",
    }),

  getCommunityTags: () =>
    fetchJSON<CommunityTag[]>(`/community/tags`),

  getProjectStats: (projectId: string) =>
    fetchJSON<ProjectStats>(`/community/projects/${projectId}/stats`),
};
