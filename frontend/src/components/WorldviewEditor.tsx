import { useEffect, useRef, useState } from "react";
import { ApiError, api, isProjectWriteFrozenError } from "@/services/api";
import {
  clearDraft,
  fingerprintDraftBase,
  loadDraft,
  saveDraft,
  type DraftEnvelope,
  type DraftScope,
} from "@/services/maintenanceDrafts";
import { useAuth } from "./AuthContext";
import DraftRecoveryNotice, {
  type DraftRecoveryState,
} from "./DraftRecoveryNotice";
import MaintenanceNotice from "./MaintenanceNotice";
import type { Character, Conflict, Faction, Geography, HistoryEvent, PowerSystem, SpecialSetting, WorldviewData, WorldviewSource } from "@/types";

interface Props {
  projectId: string;
  hasWorldview: boolean;
  genre: string;
  onComplete: () => void;
  onExtractionComplete?: () => void;
  onBack: () => void;
  migrationTarget?: {
    category: string;
    index: number;
    itemFingerprint: string;
    sourceChecksum: string;
  } | null;
  migrationRequestInvalid?: boolean;
  onReturnToMigration?: () => void;
}

type EditorMode = "manual" | "import" | "hybrid";

type SaveResult =
  | "saved_clean"            // 完整保存：提交版本已保存，草稿已清除
  | "saved_new_edit_local"   // 提交时版本已保存，新编辑已保留本地
  | "saved_new_edit_failed"  // 提交时版本已保存，新编辑未能写入本地
  | "saved_draft_failed";    // 服务端已保存，但旧本地草稿未清除

interface WorldviewDraftPayload {
  data: WorldviewData;
  source: WorldviewSource;
  mode: EditorMode;
  structuredReady: boolean;
  pendingImportText?: string;
}

interface RecoveryCandidate {
  state: DraftRecoveryState;
  draft: DraftEnvelope<WorldviewDraftPayload>;
}

type ExtractionPhase =
  | "submitting"
  | "running"
  | "completed"
  | "failed"
  | "maintenance"
  | "outcome_unknown";

interface ExtractionDraftPayload {
  documentText: string;
  documentHash: string;
  idempotencyKey: string;
  phase: ExtractionPhase;
  batchId: string | null;
  candidateCount: number | null;
  errorCode: string | null;
  errorStatus: number | null;
  retryable: boolean;
}

function isExtractionDraftPayload(value: unknown): value is ExtractionDraftPayload {
  if (!value || typeof value !== "object") return false;
  const payload = value as Partial<ExtractionDraftPayload>;
  return (
    typeof payload.documentText === "string" &&
    typeof payload.documentHash === "string" && /^[a-f0-9]{64}$/.test(payload.documentHash) &&
    typeof payload.idempotencyKey === "string" &&
    ["submitting", "running", "completed", "failed", "maintenance", "outcome_unknown"].includes(String(payload.phase)) &&
    (payload.batchId === null || typeof payload.batchId === "string") &&
    (payload.candidateCount === null || typeof payload.candidateCount === "number") &&
    (payload.errorCode === null || typeof payload.errorCode === "string") &&
    (payload.errorStatus === null || typeof payload.errorStatus === "number") &&
    typeof payload.retryable === "boolean"
  );
}

function extractionOperationKey(): string {
  if (globalThis.crypto?.randomUUID) return `extract-${globalThis.crypto.randomUUID()}`;
  return `extract-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

const EMPTY_WORLDVIEW: WorldviewData = {
  characters: [],
  geography: [],
  factions: [],
  power_system: [],
  history: [],
  conflicts: [],
  special_settings: [],
};

function draftPayload(
  data: WorldviewData,
  source: WorldviewSource,
  mode: EditorMode,
  importText = "",
  structuredReady = mode === "manual"
): WorldviewDraftPayload {
  return {
    data: {
      characters: data.characters,
      geography: data.geography,
      factions: data.factions,
      power_system: data.power_system,
      history: data.history,
      conflicts: data.conflicts,
      special_settings: data.special_settings,
      raw_text: data.raw_text,
      source,
    },
    source,
    mode,
    structuredReady,
    ...(mode !== "manual" && importText
      ? { pendingImportText: importText }
      : {}),
  };
}

const FIELD_LABELS: Record<string, string> = {
  name: "名称",
  personality: "性格",
  background: "背景",
  motivation: "动机",
  ability: "能力/特长",
  relations: "关系",
  description: "描述",
  significance: "重要性",
  stance: "立场",
  power_level: "实力等级",
  levels: "等级划分",
  rules: "规则",
  limitations: "限制",
  event: "事件",
  time: "时间",
  impact: "影响",
  type: "类型",
  parties: "涉及方",
  stakes: "利害关系",
  resolution_hint: "解决线索",
};

function worldviewPlainText(payload: WorldviewDraftPayload): string {
  const data = payload.data;
  const sections: Array<[string, unknown[]]> = [
    ["角色", data.characters],
    ["地理设定", data.geography],
    ["势力组织", data.factions],
    ["力量体系", data.power_system],
    ["历史事件", data.history],
    ["核心矛盾", data.conflicts],
    ["特殊设定", data.special_settings],
  ];
  const formattedSections = sections
    .filter(([, entries]) => entries.length > 0)
    .map(
      ([title, entries]) =>
        `${title}\n${entries
          .map((entry) =>
            Object.entries(entry as Record<string, unknown>)
              .filter(
                ([, value]) =>
                  value !== "" &&
                  value != null &&
                  (!Array.isArray(value) || value.length > 0)
              )
              .map(([key, value]) => {
                const display = Array.isArray(value) ? value.join("、") : String(value);
                return `${FIELD_LABELS[key] || key}：${display}`;
              })
              .join("｜")
          )
          .join("\n")}`
    )
    .join("\n\n");
  return payload.pendingImportText
    ? `待解析的导入原文\n${payload.pendingImportText}\n\n${formattedSections}`.trim()
    : formattedSections;
}

function isWorldviewDraftPayload(value: unknown): value is WorldviewDraftPayload {
  if (!value || typeof value !== "object") return false;
  const payload = value as Partial<WorldviewDraftPayload>;
  const data = payload.data as Partial<WorldviewData> | undefined;
  return (
    !!data &&
    Array.isArray(data.characters) &&
    Array.isArray(data.geography) &&
    Array.isArray(data.factions) &&
    Array.isArray(data.power_system) &&
    Array.isArray(data.history) &&
    Array.isArray(data.conflicts) &&
    Array.isArray(data.special_settings) &&
    (payload.source === "manual" ||
      payload.source === "imported" ||
      payload.source === "hybrid") &&
    (payload.mode === "manual" ||
      payload.mode === "import" ||
      payload.mode === "hybrid") &&
    typeof payload.structuredReady === "boolean" &&
    (payload.pendingImportText === undefined ||
      typeof payload.pendingImportText === "string")
  );
}

export default function WorldviewEditor({
  projectId,
  hasWorldview,
  genre,
  onComplete,
  onExtractionComplete,
  onBack,
  migrationTarget = null,
  migrationRequestInvalid = false,
  onReturnToMigration,
}: Props) {
  const { user } = useAuth();
  const [mode, setMode] = useState<EditorMode>("manual");
  const [data, setData] = useState<WorldviewData>(EMPTY_WORLDVIEW);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [parsedInfo, setParsedInfo] = useState<{ total: number; by_category: Record<string, number>; by_priority: Record<string, number> } | null>(null);
  const [importText, setImportText] = useState("");
  const [importResult, setImportResult] = useState<{ count: number; done: boolean } | null>(null);
  const [source, setSource] = useState<WorldviewSource>("manual");
  const [recovery, setRecovery] = useState<RecoveryCandidate | null>(null);
  const [maintenanceFailure, setMaintenanceFailure] = useState<{
    error: ApiError;
    draftStored: boolean;
  } | null>(null);
  const [draftMessage, setDraftMessage] = useState("");
  const [copyFallback, setCopyFallback] = useState<string | null>(null);
  const [draftStorageFailed, setDraftStorageFailed] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [loadedScopeKey, setLoadedScopeKey] = useState("");
  const [scopeLoading, setScopeLoading] = useState(true);
  const [scopeLoadError, setScopeLoadError] = useState("");
  const [saved, setSaved] = useState(false);  // 本地追踪保存状态，解决 hasWorldview prop 闭锁问题
  const [nextStepBlocked, setNextStepBlocked] = useState(false);  // 未保存编辑时阻止离开编辑器
  const [corruptDraft, setCorruptDraft] = useState(false);  // 损坏或不兼容的草稿，只能丢弃
  const [reparseNeeded, setReparseNeeded] = useState(false);  // 导入原文修改后需重新解析
  const [saveResult, setSaveResult] = useState<SaveResult | null>(null);
  const loadedRef = useRef(false);  // 防止已加载的数据被 hasWorldview 变化覆盖
  const [saveError, setSaveError] = useState("");
  const [worldviewStale, setWorldviewStale] = useState(false);
  const [migrationTargetState, setMigrationTargetState] = useState<
    "idle" | "loading" | "valid" | "invalid" | "error"
  >("idle");
  const [migrationPreviewReloadToken, setMigrationPreviewReloadToken] = useState(0);
  const [serverSourceChecksum, setServerSourceChecksum] = useState<string | null>(null);
  const [importError, setImportError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [relationalMode, setRelationalMode] = useState<boolean | null>(null);
  const [loreModeError, setLoreModeError] = useState("");
  const [loreModeReloadToken, setLoreModeReloadToken] = useState(0);
  const [extractionDraft, setExtractionDraft] = useState<ExtractionDraftPayload | null>(null);
  const [extractionNotice, setExtractionNotice] = useState("");
  const [extractionError, setExtractionError] = useState("");
  const [extractionCorrupt, setExtractionCorrupt] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const baseFingerprintRef = useRef<string | null>(null);
  const serverSnapshotRef = useRef("");
  const serverSourceChecksumRef = useRef<string | null>(null);
  const worldviewExistsRef = useRef(false);
  const draftReadyRef = useRef(false);
  const dirtyRef = useRef(false);
  const currentPayloadRef = useRef<WorldviewDraftPayload>(
    draftPayload(EMPTY_WORLDVIEW, "manual", "manual")
  );
  const scopeRef = useRef<DraftScope | null>(null);
  const loadGenerationRef = useRef(0);
  const scopeGenerationRef = useRef(0);
  const saveGenerationRef = useRef(0);
  const editorRootRef = useRef<HTMLDivElement>(null);
  const migrationNoticeRef = useRef<HTMLDivElement>(null);
  const saveResultRef = useRef<HTMLDivElement>(null);
  const draftRecoveryContainerRef = useRef<HTMLDivElement>(null);
  const focusRecoveryAfterReloadRef = useRef(false);
  const migrationFocusHandledRef = useRef("");
  const extractionResultRef = useRef<HTMLDivElement>(null);
  const pendingFocusRef = useRef<(() => HTMLElement | null | undefined) | null>(
    null
  );

  const draftScope: DraftScope | null = user
    ? { userId: user.id, projectId, kind: "worldview", objectId: "worldview" }
    : null;
  const extractionScope: DraftScope | null = user
    ? { userId: user.id, projectId, kind: "lore-extraction", objectId: "worldview-import" }
    : null;
  const currentScopeKey = user ? `${user.id}:${projectId}` : `anonymous:${projectId}`;
  currentPayloadRef.current = draftPayload(
    data,
    source,
    mode,
    importText,
    mode === "manual" || importResult?.done === true
  );

  useEffect(() => {
    const scopedDraft = draftScope;
    scopeGenerationRef.current += 1;
    loadGenerationRef.current += 1;
    saveGenerationRef.current += 1;
    scopeRef.current = draftScope;
    draftReadyRef.current = false;
    dirtyRef.current = false;
    baseFingerprintRef.current = null;
    serverSnapshotRef.current = "";
    serverSourceChecksumRef.current = null;
    worldviewExistsRef.current = false;
    setRecovery(null);
    setCorruptDraft(false);
    setMaintenanceFailure(null);
    setDraftStorageFailed(false);
    setData(EMPTY_WORLDVIEW);
    setMode("manual");
    setImportText("");
    setImportResult(null);
    setSource("manual");
    setParsedInfo(null);
    setSaved(false);
    setNextStepBlocked(false);
    setReparseNeeded(false);
    setSaveResult(null);
    setCopyFallback(null);
    setLoading(false);
    setImporting(false);
    setReloading(false);
    setScopeLoading(true);
    setScopeLoadError("");
    setImportError("");
    setUploadError("");
    setSaveError("");
    setWorldviewStale(false);
    setMigrationTargetState("idle");
    setMigrationPreviewReloadToken(0);
    setServerSourceChecksum(null);
    return () => {
      if (dirtyRef.current && scopedDraft) {
        saveDraft(
          scopedDraft,
          currentPayloadRef.current,
          baseFingerprintRef.current
        );
      }
    };
  }, [projectId, user?.id]);

  useEffect(() => {
    let active = true;
    setRelationalMode(null);
    setLoreModeError("");
    void api.getLoreOverview(projectId).then((overview) => {
      if (active) {
        const relational = overview.migration_status.storage_mode === "relational";
        setRelationalMode(relational);
        if (relational) setMode("import");
      }
    }).catch(() => {
      if (active) {
        setRelationalMode(null);
        setLoreModeError("无法确认当前项目的设定仓库模式，已停止 AI 提取以避免写入错误位置。请检查网络后重试页面。");
      }
    });
    return () => { active = false; };
  }, [projectId, loreModeReloadToken]);

  useEffect(() => {
    if (relationalMode && mode !== "import") setMode("import");
  }, [relationalMode, mode]);

  useEffect(() => {
    setExtractionDraft(null);
    setExtractionNotice("");
    setExtractionError("");
    setExtractionCorrupt(false);
    if (!extractionScope || scopeLoading) return;
    const stored = loadDraft<ExtractionDraftPayload>(extractionScope);
    if ((stored.status === "available" || stored.status === "expired") && isExtractionDraftPayload(stored.draft.payload)) {
      setExtractionDraft(stored.draft.payload);
      setImportText(stored.draft.payload.documentText);
      setExtractionNotice(
        stored.draft.payload.phase === "completed"
          ? `上次提取已完成，共 ${stored.draft.payload.candidateCount ?? 0} 项待审核候选。`
          : "已恢复上次提取任务；请先核对结果，系统不会自动重复调用 AI。"
      );
    } else if (stored.status === "corrupt") {
      setExtractionCorrupt(true);
      setExtractionError("上次提取状态已损坏。请放弃该状态后重新提取；原文草稿仍由世界观编辑器保留。");
    }
  }, [user?.id, projectId, scopeLoading]);

  // pagehide / 可靠卸载前同步草稿刷新 — 覆盖输入后立即刷新场景
  useEffect(() => {
    const scopedDraft = draftScope;
    function flushBeforeUnload() {
      if (dirtyRef.current && scopedDraft) {
        saveDraft(
          scopedDraft,
          currentPayloadRef.current,
          baseFingerprintRef.current
        );
      }
    }
    window.addEventListener("pagehide", flushBeforeUnload);
    return () => window.removeEventListener("pagehide", flushBeforeUnload);
  }, [draftScope?.userId, draftScope?.projectId]);

  // 始终尝试加载世界观 — 不仅依赖 hasWorldview prop（该 prop 可能因父组件未刷新而过时）
  useEffect(() => {
    loadedRef.current = false;  // projectId 变化时重置
    void loadWorldview();
  }, [projectId, user?.id]);

  // 如果父组件刷新后 hasWorldview 变为 true，也重新加载 — 但仅在尚未加载时
  useEffect(() => {
    if (hasWorldview && draftReadyRef.current && !loadedRef.current) {
      void loadWorldview();
    }
  }, [hasWorldview]);

  useEffect(() => {
    if (!draftReadyRef.current || !draftScope || recovery) return;
    const serialized = JSON.stringify(currentPayloadRef.current);
    dirtyRef.current = serialized !== serverSnapshotRef.current;
    if (!dirtyRef.current) return;
    const timer = window.setTimeout(() => {
      const result = saveDraft(
        draftScope,
        currentPayloadRef.current,
        baseFingerprintRef.current
      );
      const failed = result.status !== "saved";
      setDraftStorageFailed(failed);
      if (failed) {
        setDraftMessage("无法自动保留本地草稿，请立即复制内容。");
      }
    }, 700);
    return () => window.clearTimeout(timer);
  }, [
    data,
    source,
    mode,
    importText,
    importResult?.done,
    recovery,
    user?.id,
    projectId,
  ]);

  // Focus management: apply pending focus when recovery/corrupt blocking UI is dismissed.
  // Using a useEffect (runs after React DOM flush) instead of setTimeout is more
  // reliable in test environments (jsdom + vitest) where setTimeout callbacks may
  // not fire predictably.
  useEffect(() => {
    if (recovery !== null || corruptDraft) return;
    const factory = pendingFocusRef.current;
    if (!factory) return;
    pendingFocusRef.current = null;
    const target = factory();
    target?.focus();
  }, [recovery, corruptDraft]);

  async function applyServerState(
    serverData: WorldviewData,
    serverSource: WorldviewSource,
    serverMode: EditorMode,
    exists: boolean,
    checkRecovery: boolean,
    generation: number,
    scopeGeneration: number,
    expectedScopeKey: string,
    parsedCount = 0,
    sourceChecksum: string | null = null
  ) {
    const payload = draftPayload(
      serverData,
      serverSource,
      serverMode,
      "",
      true
    );
    const fingerprint = await fingerprintDraftBase(payload);
    if (
      generation !== loadGenerationRef.current ||
      scopeGeneration !== scopeGenerationRef.current
    ) {
      return;
    }
    baseFingerprintRef.current =
      fingerprint.status === "available" ? fingerprint.value : null;
    serverSnapshotRef.current = JSON.stringify(payload);
    serverSourceChecksumRef.current = sourceChecksum;
    setServerSourceChecksum(sourceChecksum);
    worldviewExistsRef.current = exists;
    dirtyRef.current = false;
    setData(serverData);
    setSource(serverSource);
    setMode(serverMode);
    setImportText("");
    setImportResult(
      serverMode === "manual" ? null : { count: parsedCount, done: true }
    );
    setSaved(exists);
    setLoadedScopeKey(expectedScopeKey);
    setScopeLoading(false);
    setReloading(false);
    setScopeLoadError("");
    draftReadyRef.current = true;

    if (!checkRecovery || !draftScope) return;
    const result = loadDraft<WorldviewDraftPayload>(draftScope);
    if (result.status === "corrupt") {
      setCorruptDraft(true);
      setDraftMessage("本地草稿已损坏，项目中已保存的版本未受影响。请确认丢弃损坏的本地草稿。");
      return;
    }
    if (result.status === "unavailable") {
      setDraftMessage("本地存储不可用，草稿恢复已跳过。");
      return;
    }
    if (result.status !== "available" && result.status !== "expired") return;
    if (!isWorldviewDraftPayload(result.draft.payload)) {
      setCorruptDraft(true);
      setDraftMessage("本地草稿格式不匹配，项目中已保存的版本未受影响。请确认丢弃损坏的本地草稿。");
      return;
    }
    const state: DraftRecoveryState =
      result.status === "expired"
        ? "expired"
        : baseFingerprintRef.current !== null &&
            result.draft.baseFingerprint === baseFingerprintRef.current
          ? "available"
          : "conflict";  // 未知指纹按冲突处理
    setRecovery({ state, draft: result.draft });
    setDraftMessage(
      "发现本地草稿，编辑区已锁定。请选择载入本地副本或丢弃草稿。"
    );
  }

  async function loadWorldview(checkRecovery = true) {
    const generation = ++loadGenerationRef.current;
    const scopeGeneration = scopeGenerationRef.current;
    const expectedScopeKey = currentScopeKey;
    const isFirstLoad = !draftReadyRef.current;
    if (isFirstLoad) {
      setScopeLoading(true);
      setScopeLoadError("");
    } else {
      setReloading(true);
    }
    // 同 scope 重载时清除过期 parsedInfo
    if (!isFirstLoad) {
      setParsedInfo(null);
    }
    try {
      const wv = await api.getWorldview(projectId);
      if (
        generation !== loadGenerationRef.current ||
        scopeGeneration !== scopeGenerationRef.current
      ) {
        return;
      }
      if (!wv) {
        // 服务器返回 null/undefined — 进入可编辑空世界观，不永久加载
        if (
          generation !== loadGenerationRef.current ||
          scopeGeneration !== scopeGenerationRef.current
        ) {
          return;
        }
        await applyServerState(
          EMPTY_WORLDVIEW,
          "manual",
          "manual",
          false,
          checkRecovery,
          generation,
          scopeGeneration,
          expectedScopeKey
        );
        return;
      }
      const serverData: WorldviewData = {
        characters: wv.characters || [],
        geography: wv.geography || [],
        factions: wv.factions || [],
        power_system: wv.power_system || [],
        history: wv.history || [],
        conflicts: wv.conflicts || [],
        special_settings: wv.special_settings || [],
        raw_text: wv.raw_text,
        source: (wv.source as WorldviewSource) || "manual",
      };
      const serverSource = (wv.source as WorldviewSource) || "manual";
      const serverMode: EditorMode =
        serverSource === "imported"
          ? "import"
          : serverSource === "hybrid"
            ? "hybrid"
            : "manual";
      await applyServerState(
        serverData,
        serverSource,
        serverMode,
        true,
        checkRecovery,
        generation,
        scopeGeneration,
        expectedScopeKey,
        wv.parsed_elements?.length || 0,
        wv.source_checksum
      );
      if (
        generation !== loadGenerationRef.current ||
        scopeGeneration !== scopeGenerationRef.current
      ) {
        return;
      }
      loadedRef.current = true;  // 标记已加载，防止后续 hasWorldview 变化覆盖
      if (wv.parsed_elements) {
        const byCat: Record<string, number> = {};
        const byPri: Record<string, number> = {};
        for (const e of wv.parsed_elements) {
          byCat[e.category] = (byCat[e.category] || 0) + 1;
          byPri[e.priority] = (byPri[e.priority] || 0) + 1;
        }
        setParsedInfo({ total: wv.parsed_elements.length, by_category: byCat, by_priority: byPri });
      }
    } catch (e) {
      // 404 表示无世界观，属正常情况
      if (e instanceof ApiError && e.status === 404) {
        if (
          generation !== loadGenerationRef.current ||
          scopeGeneration !== scopeGenerationRef.current
        ) {
          return;
        }
        await applyServerState(
          EMPTY_WORLDVIEW,
          "manual",
          "manual",
          false,
          checkRecovery,
          generation,
          scopeGeneration,
          expectedScopeKey
        );
      } else {
        if (
          generation !== loadGenerationRef.current ||
          scopeGeneration !== scopeGenerationRef.current
        ) {
          return;
        }
        console.error("Failed to load worldview:", e);
        draftReadyRef.current = true;
        setLoadedScopeKey(expectedScopeKey);
        setScopeLoading(false);
        setReloading(false);
        setScopeLoadError("世界观加载失败，请检查网络后重试。");
      }
    }
  }

  function storeCurrentDraft() {
    if (!draftScope) return { stored: false };
    const result = saveDraft(
      draftScope,
      currentPayloadRef.current,
      baseFingerprintRef.current
    );
    const stored = result.status === "saved";
    setDraftStorageFailed(!stored);
    if (!stored) setDraftMessage("无法自动保留本地草稿，请立即复制内容。");
    return { stored };
  }

  async function copyWorldview(payload = currentPayloadRef.current) {
    const text = worldviewPlainText(payload);
    try {
      await navigator.clipboard.writeText(text);
      setCopyFallback(null);
      setDraftMessage("世界观内容已复制。");
    } catch {
      setCopyFallback(text);
      setDraftMessage("自动复制失败，已在页面显示可全选的纯文本副本。");
    }
  }

  function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const operationScope = scopeGenerationRef.current;
    setImporting(true);
    setUploadError("");
    // All file types go through the backend for robust encoding detection
    api.uploadWorldviewFile(projectId, file)
      .then((result) => {
        if (operationScope === scopeGenerationRef.current) {
          // 文件替换原文时，使旧解析结果失效，要求重新解析
          if (importResult?.done) {
            setImportResult(null);
            setParsedInfo(null);
            setReparseNeeded(true);
            setDraftMessage(
              "原文已修改，上次提取结果已失效，请重新解析。"
            );
          }
          setImportText(result.text);
          setUploadError("");
        }
      })
      .catch((_err) => {
        if (operationScope === scopeGenerationRef.current) {
          setUploadError(
            "文件解析失败，请重试或直接粘贴文本。"
          );
        }
      })
      .finally(() => {
        if (operationScope === scopeGenerationRef.current) {
          setImporting(false);
          if (fileInputRef.current) fileInputRef.current.value = "";
        }
      });
  }

  async function handleImport() {
    if (!importText.trim() || importText.trim().length < 10) {
      setImportError("请输入或上传至少 10 个字符的文档内容");
      return;
    }
    setImporting(true);
    setImportResult(null);
    setImportError("");
    const operationScope = scopeGenerationRef.current;
    // 先确保原文已落本地草稿
    const firstDraft = storeCurrentDraft();
    try {
      const result = await api.importWorldview(projectId, importText, genre);
      if (operationScope !== scopeGenerationRef.current) return;
      setData({
        characters: result.characters || [],
        geography: result.geography || [],
        factions: result.factions || [],
        power_system: result.power_system || [],
        history: result.history || [],
        conflicts: result.conflicts || [],
        special_settings: result.special_settings || [],
        raw_text: importText,
        source: mode === "hybrid" ? "hybrid" : "imported",
      });
      setSource(mode === "hybrid" ? "hybrid" : "imported");
      setImportResult({ count: result.element_count, done: true });
      setReparseNeeded(false);
      setSaved(false);
    } catch (e) {
      if (operationScope === scopeGenerationRef.current) {
        if (isProjectWriteFrozenError(e)) {
          // 维护冻结 — 复用安全维护提示，不展示后端内部信息
          const retry = storeCurrentDraft();
          setMaintenanceFailure({
            error: e as ApiError,
            draftStored: firstDraft.stored || retry.stored,
          });
        } else {
          console.error("Import failed:", e);
          setImportError(
            "AI 解析失败，原文已保留，可修改后重试。"
          );
        }
      }
    } finally {
      if (operationScope === scopeGenerationRef.current) setImporting(false);
    }
  }

  async function handleStrictExtraction(newAttempt = false) {
    const documentText = importText.trim();
    if (documentText.length < 10) {
      setExtractionError("请输入或上传至少 10 个字符的文档内容。");
      return;
    }
    if (!extractionScope) {
      setExtractionError("登录状态尚未就绪，暂时无法安全保存提取任务。");
      return;
    }
    if (
      extractionDraft &&
      extractionDraft.documentText !== documentText &&
      ["submitting", "running", "maintenance", "outcome_unknown"].includes(extractionDraft.phase)
    ) {
      setExtractionError("上次任务的结果尚未确定。请先恢复该任务绑定的原文，再使用同一任务核对结果；系统不会静默换用新任务标识。");
      return;
    }
    const fingerprint = await fingerprintDraftBase({ documentText });
    if (fingerprint.status !== "available") {
      setExtractionError("浏览器无法生成原文指纹，已停止提取以避免任务与错误原文绑定。");
      return;
    }
    const canReuse = !newAttempt && extractionDraft?.documentText === documentText && extractionDraft.documentHash === fingerprint.value;
    const operation: ExtractionDraftPayload = {
      documentText,
      documentHash: fingerprint.value,
      idempotencyKey: canReuse ? extractionDraft.idempotencyKey : extractionOperationKey(),
      phase: "submitting",
      batchId: canReuse ? extractionDraft.batchId : null,
      candidateCount: canReuse ? extractionDraft.candidateCount : null,
      errorCode: null,
      errorStatus: null,
      retryable: false,
    };
    const persisted = saveDraft(extractionScope, operation, null);
    if (persisted.status !== "saved") {
      setExtractionError("无法在本机保存幂等任务，已停止提取以避免重复调用。请复制原文后检查浏览器存储设置。");
      return;
    }
    setExtractionDraft(operation);
    setExtractionError("");
    setExtractionNotice("正在提取独立设定；结果只会进入待审核列表。");
    setImporting(true);
    try {
      const batch = await api.createLoreExtraction(projectId, {
        idempotency_key: operation.idempotencyKey,
        document_text: operation.documentText,
        source_kind: "worldview_import",
        source_ref: "世界观编辑器导入原文",
      });
      const phase: ExtractionPhase = batch.status;
      const next: ExtractionDraftPayload = {
        ...operation,
        phase,
        batchId: batch.id,
        candidateCount: batch.candidate_count,
        errorCode: batch.error_code,
        errorStatus: null,
        retryable: batch.retryable,
      };
      saveDraft(extractionScope, next, null);
      setExtractionDraft(next);
      if (batch.status === "completed" && batch.candidate_count > 0) {
        setExtractionNotice(`已生成 ${batch.candidate_count} 项待审核候选，尚未成为正式设定。`);
        onExtractionComplete ? onExtractionComplete() : onComplete();
      } else if (batch.status === "completed") {
        setExtractionNotice("原文中未识别到可确认的独立设定。可修改原文重新提取，或前往设定仓库手动创建。");
        requestAnimationFrame(() => extractionResultRef.current?.focus());
      } else if (batch.status === "running") {
        setExtractionNotice("提取仍在处理中。请稍后使用同一任务核对结果，不会重复调用 AI。");
      } else {
        setExtractionError(batch.error_message || "本次提取未完成；原文和任务状态已保留。");
      }
    } catch (error) {
      const apiError = error instanceof ApiError ? error : null;
      const phase: ExtractionPhase = apiError?.status === 503
        ? "maintenance"
        : apiError?.status === 409
          ? "failed"
        : apiError && apiError.status >= 500
          ? "outcome_unknown"
          : apiError
            ? "failed"
            : "outcome_unknown";
      const next: ExtractionDraftPayload = {
        ...operation,
        phase,
        errorCode: apiError?.code ?? null,
        errorStatus: apiError?.status ?? null,
        retryable: apiError?.retryable === true,
      };
      saveDraft(extractionScope, next, null);
      setExtractionDraft(next);
      if (apiError?.status === 413) {
        setExtractionError("原文超过当前 20,000 字提取上限，内容已完整保留；请缩短后明确发起新提取，系统不会静默截断。");
      } else if (apiError?.status === 503) {
        setExtractionError("系统正在维护，提取任务和原文已保留。维护结束后请使用同一任务重试提取。");
      } else if (apiError?.status === 409) {
        setExtractionError("任务标识与原文不一致，已停止操作。请恢复原文核对，或明确放弃后重新提取。");
      } else if (phase === "outcome_unknown") {
        setExtractionError("请求结果尚不确定，任务和原文已保留。请使用同一任务核对结果，不要重新提交。");
      } else if (apiError) {
        setExtractionError(apiError.detail || "提取失败，原文已保留。");
      } else {
        setExtractionError("网络中断，结果尚不确定。请使用同一任务核对结果，不要重新提交。");
      }
    } finally {
      setImporting(false);
    }
  }

  function abandonExtraction() {
    if (!extractionScope) return;
    if (!window.confirm("确定放弃上次提取任务状态吗？原文会保留，但之后重新提取将使用新的任务标识。")) return;
    const cleared = clearDraft(extractionScope);
    if (cleared.status === "unavailable") {
      setExtractionError("无法清除本机任务状态，已保留当前任务以避免重复调用。");
      return;
    }
    setExtractionDraft(null);
    setExtractionCorrupt(false);
    setExtractionNotice("已放弃上次任务状态，原文仍保留在编辑器中。");
    setExtractionError("");
  }

  function restoreExtractionSource() {
    if (!extractionDraft) return;
    setImportText(extractionDraft.documentText);
    setExtractionError("");
    setExtractionNotice("已恢复上次任务绑定的原文，可使用同一任务核对结果。");
  }

  async function handleSave() {
    if (recovery) return;
    if (worldviewExistsRef.current && !serverSourceChecksumRef.current) {
      setSaveError("无法确认当前世界观版本，已停止保存。请重新加载后再试。");
      return;
    }
    const saveGeneration = ++saveGenerationRef.current;
    const submittedScope = draftScope;
    const submittedPayload = currentPayloadRef.current;
    const submittedSerialized = JSON.stringify(submittedPayload);
    setLoading(true);
    setSaveError("");
    setSaveResult(null);
    setWorldviewStale(false);
    const localDraft = storeCurrentDraft();
    try {
      // 导入/混合模式下确保 raw_text 始终为最新输入的原文
      const saveData =
        mode !== "manual" && importText
          ? { ...data, raw_text: importText, source }
          : { ...data, source };
      const savedWorldview = await api.setWorldview(projectId, {
        ...saveData,
        expected_source_checksum: serverSourceChecksumRef.current,
      });
      if (
        saveGeneration !== saveGenerationRef.current ||
        !submittedScope ||
        scopeRef.current?.userId !== submittedScope.userId ||
        scopeRef.current?.projectId !== submittedScope.projectId
      ) {
        return;
      }
      const { pendingImportText: _savedImportText, ...savedPayload } =
        submittedPayload;
      const fingerprint = await fingerprintDraftBase(savedPayload);
      if (saveGeneration !== saveGenerationRef.current) return;
      baseFingerprintRef.current =
        fingerprint.status === "available" ? fingerprint.value : null;
      serverSourceChecksumRef.current = savedWorldview.source_checksum;
      setServerSourceChecksum(savedWorldview.source_checksum);
      worldviewExistsRef.current = true;
      serverSnapshotRef.current = JSON.stringify(savedPayload);
      // 重试成功 — 清除旧维护提示和普通错误
      setMaintenanceFailure(null);
      setSaveError("");
      setNextStepBlocked(false);  // 清除上一步仅本地提示
      setReparseNeeded(false);  // 保存成功后清除重新解析提示
      const hasNewEdits =
        JSON.stringify(currentPayloadRef.current) !== submittedSerialized;
      if (hasNewEdits) {
        setSaved(false);
        dirtyRef.current = true;
        const result = saveDraft(
          submittedScope,
          currentPayloadRef.current,
          baseFingerprintRef.current
        );
        const draftOk = result.status === "saved";
        setDraftStorageFailed(!draftOk);
        setDraftMessage(
          draftOk
            ? "提交时的版本已保存，当前新编辑尚未保存到项目，已保留本地草稿。"
            : "提交时的版本已保存，但新编辑未能写入本地草稿，请立即复制内容。"
        );
        setSaveResult(draftOk ? "saved_new_edit_local" : "saved_new_edit_failed");
      } else {
        setSaved(true);
        setImportText("");
        dirtyRef.current = false;
        const cleared = clearDraft(submittedScope);
        if (cleared.status === "cleared" || cleared.status === "missing") {
          setDraftStorageFailed(false);
          setDraftMessage("世界观已保存，本地草稿已清除。");
          setSaveResult("saved_clean");
        } else {
          setDraftStorageFailed(true);
          setDraftMessage(
            "世界观已保存到项目，但旧本地草稿未能清除；再次进入时请核对后手动放弃。"
          );
          setSaveResult("saved_draft_failed");
        }
      }
    } catch (e) {
      if (saveGeneration !== saveGenerationRef.current) return;
      if (isProjectWriteFrozenError(e)) {
        setMaintenanceFailure({ error: e, draftStored: localDraft.stored });
      } else if (e instanceof ApiError && e.code === "WORLDVIEW_SOURCE_STALE") {
        setMaintenanceFailure(null);
        setWorldviewStale(true);
        setSaveError(
          localDraft.stored
            ? "服务器上的世界观已更新，本地修改已保留。请重新加载并比较后再保存。"
            : "服务器上的世界观已更新，当前内容仅保留在页面，请立即复制后再重新加载。"
        );
      } else {
        // 重试变成普通错误时清除旧维护提示
        setMaintenanceFailure(null);
        console.error("Save failed:", e);
        setSaveError(
          localDraft.stored
            ? "保存失败，内容已保留在页面和本地草稿中，可重试。"
            : "保存失败，内容仅保留在页面，请立即复制。"
        );
      }
    } finally {
      if (saveGeneration === saveGenerationRef.current) setLoading(false);
    }
  }

  function restoreDraft() {
    if (!recovery) return;
    setData(recovery.draft.payload.data);
    setSource(recovery.draft.payload.source);
    setMode(recovery.draft.payload.mode);
    setImportText(recovery.draft.payload.pendingImportText || "");
    setImportResult(
      recovery.draft.payload.structuredReady &&
        recovery.draft.payload.mode !== "manual"
        ? {
            count:
              recovery.draft.payload.data.characters.length +
              recovery.draft.payload.data.geography.length +
              recovery.draft.payload.data.factions.length +
              recovery.draft.payload.data.power_system.length +
              recovery.draft.payload.data.history.length +
              recovery.draft.payload.data.conflicts.length +
              recovery.draft.payload.data.special_settings.length,
            done: true,
          }
        : null
    );
    setSaved(false);
    pendingFocusRef.current = () =>
      editorRootRef.current?.querySelector<HTMLElement>(
        ".wv-section-title[tabindex]"
      ) ||
      editorRootRef.current?.querySelector<HTMLElement>(
        ".wv-entry input:not([readonly])"
      ) ||
      editorRootRef.current?.querySelector<HTMLElement>("button.btn-primary");
    setRecovery(null);
    dirtyRef.current = true;
    setDraftMessage("本地草稿已载入，请核对后手动保存。");
  }

  function discardDraft() {
    if (!recovery || !draftScope) return;
    const result = clearDraft(draftScope);
    if (result.status === "cleared" || result.status === "missing") {
      pendingFocusRef.current = () =>
        editorRootRef.current?.querySelector<HTMLElement>(
          ".wv-section-title[tabindex]"
        ) ||
        editorRootRef.current?.querySelector<HTMLElement>(
          "button.btn-primary"
        );
      setRecovery(null);
      setDraftStorageFailed(false);
      setDraftMessage("本地草稿已删除，项目中已保存的内容未受影响。");
    } else {
      setDraftMessage("本地草稿删除失败，请稍后重试。");
    }
  }

  function discardCorruptDraft() {
    const confirmed = window.confirm(
      "项目中已保存的内容不会受影响。确认丢弃损坏的本地草稿吗？"
    );
    if (!confirmed) return;
    if (!draftScope) return;
    const result = clearDraft(draftScope);
    if (result.status === "cleared" || result.status === "missing") {
      pendingFocusRef.current = () =>
        editorRootRef.current?.querySelector<HTMLElement>(
          ".wv-section-title[tabindex]"
        ) ||
        editorRootRef.current?.querySelector<HTMLElement>(
          "button.btn-primary"
        );
      setCorruptDraft(false);
      setDraftStorageFailed(false);
      setDraftMessage("损坏的本地草稿已丢弃，项目版本未受影响。");
    } else {
      setDraftMessage("损坏草稿删除失败，请稍后重试。");
    }
  }

  function handleBack() {
    const storedNow = dirtyRef.current ? storeCurrentDraft().stored : true;
    const contentUnprotected = dirtyRef.current
      ? !storedNow
      : draftStorageFailed ||
        (maintenanceFailure !== null && !maintenanceFailure.draftStored);
    if (
      contentUnprotected &&
      !window.confirm("内容尚未保留，返回可能丢失。确定继续吗？")
    ) {
      return;
    }
    onBack();
  }

  function switchMode(newMode: EditorMode) {
    setMode(newMode);
    if (newMode === "manual") {
      // 切回手动模式时，始终将当前导入原文同步到 raw_text，确保保存请求体包含最新原文
      if (importText.trim()) {
        setData({ ...data, raw_text: importText });
      }
      setSource("manual");
    }
    else if (newMode === "import" && !importResult?.done) setSource("imported");
    else if (newMode === "hybrid") setSource("hybrid");
  }

  const isReadOnly = mode === "import" && importResult?.done;
  const showEditor = relationalMode !== true && (mode === "manual" || importResult?.done === true);

  const migrationCategoryLabels: Record<string, string> = {
    characters: "角色",
    geography: "地点",
    factions: "阵营",
    power_system: "能力体系",
    history: "历史事件",
    conflicts: "冲突",
    special_settings: "其他重要设定",
  };

  function isMigrationTarget(category: string, index: number): boolean {
    return migrationTarget?.category === category && migrationTarget.index === index;
  }

  const migrationTargetEntries = migrationTarget
    ? (data as unknown as Record<string, unknown[]>)[migrationTarget.category]
    : null;
  const migrationTargetExists = Array.isArray(migrationTargetEntries) &&
    migrationTarget != null && migrationTarget.index < migrationTargetEntries.length &&
    migrationTargetState === "valid";
  const migrationSourceUnchanged =
    JSON.stringify(currentPayloadRef.current) === serverSnapshotRef.current;
  const migrationCanLocate = migrationTargetExists && migrationSourceUnchanged &&
    recovery === null && !corruptDraft;

  useEffect(() => {
    if (!migrationTarget) {
      setMigrationTargetState("idle");
      return;
    }
    if (scopeLoading || loadedScopeKey !== currentScopeKey) {
      setMigrationTargetState("loading");
      return;
    }
    if (!serverSourceChecksum) {
      setMigrationTargetState("invalid");
      return;
    }
    const controller = new AbortController();
    let active = true;
    setMigrationTargetState("loading");
    void api.getLoreMigrationPreview(projectId, controller.signal).then((preview) => {
      if (!active) return;
      const current = preview.items.find((item) =>
        item.legacy_category === migrationTarget.category &&
        item.legacy_index === migrationTarget.index
      );
      const reasonIsEditable = current?.reason_codes.some((reason) =>
          reason === "missing_name" || reason === "parsed_name_mismatch"
        ) === true;
      const exactVersionMatches =
        preview.storage_mode === "legacy" &&
        preview.source_checksum === migrationTarget.sourceChecksum &&
        serverSourceChecksum === migrationTarget.sourceChecksum;
      setMigrationTargetState(
        exactVersionMatches && current?.item_fingerprint === migrationTarget.itemFingerprint && reasonIsEditable
          ? "valid"
          : "invalid"
      );
    }).catch((error) => {
      if (active && (error as Error).name !== "AbortError") setMigrationTargetState("error");
    });
    return () => {
      active = false;
      controller.abort();
    };
  }, [
    projectId,
    migrationTarget?.category,
    migrationTarget?.index,
    migrationTarget?.itemFingerprint,
    migrationTarget?.sourceChecksum,
    serverSourceChecksum,
    scopeLoading,
    loadedScopeKey,
    currentScopeKey,
    migrationPreviewReloadToken,
  ]);

  useEffect(() => {
    const key = migrationTarget
      ? `${projectId}:${migrationTarget.itemFingerprint}`
      : migrationRequestInvalid
        ? `${projectId}:invalid-migration-request`
        : "";
    if (!key) {
      migrationFocusHandledRef.current = "";
      return;
    }
    if (
      relationalMode !== false || migrationTargetState === "loading" ||
      scopeLoading || loadedScopeKey !== currentScopeKey || recovery || corruptDraft ||
      migrationFocusHandledRef.current === key
    ) return;
    migrationFocusHandledRef.current = key;
    migrationNoticeRef.current?.focus();
  }, [projectId, migrationTarget?.itemFingerprint, migrationRequestInvalid, migrationTargetState, relationalMode, scopeLoading, loadedScopeKey, recovery, corruptDraft]);

  useEffect(() => {
    if (!saveResult || maintenanceFailure || saveError) return;
    saveResultRef.current?.focus();
  }, [saveResult, maintenanceFailure, saveError]);

  useEffect(() => {
    if (!recovery || !focusRecoveryAfterReloadRef.current) return;
    focusRecoveryAfterReloadRef.current = false;
    draftRecoveryContainerRef.current?.focus();
  }, [recovery]);

  function locateMigrationTarget() {
    if (!migrationCanLocate || !migrationTarget) return;
    if (isReadOnly) switchMode("hybrid");
    requestAnimationFrame(() => {
      const selector = `[data-migration-target="${migrationTarget.category}:${migrationTarget.index}"]`;
      const entry = editorRootRef.current?.querySelector<HTMLElement>(selector);
      const field = entry?.querySelector<HTMLElement>("input, textarea, button");
      const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
      entry?.scrollIntoView?.({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
      field?.focus();
    });
  }

  function reloadProjectVersionAfterStale() {
    setSaveError("");
    setWorldviewStale(false);
    focusRecoveryAfterReloadRef.current = true;
    void loadWorldview(true);
  }

  // CRUD helpers
  function addCharacter() { setData({ ...data, characters: [...data.characters, { name: "", personality: "", background: "", motivation: "", ability: "", relations: [] }] }); }
  function updateCharacter(i: number, field: keyof Character, value: string) { const c = [...data.characters]; c[i] = { ...c[i], [field]: value }; setData({ ...data, characters: c }); }
  function removeCharacter(i: number) { setData({ ...data, characters: data.characters.filter((_, idx) => idx !== i) }); }
  function addGeography() { setData({ ...data, geography: [...data.geography, { name: "", description: "", significance: "" }] }); }
  function updateGeography(i: number, field: keyof Geography, value: string) { const g = [...data.geography]; g[i] = { ...g[i], [field]: value }; setData({ ...data, geography: g }); }
  function removeGeography(i: number) { setData({ ...data, geography: data.geography.filter((_, idx) => idx !== i) }); }
  function addFaction() { setData({ ...data, factions: [...data.factions, { name: "", stance: "", power_level: "", relations: [] }] }); }
  function updateFaction(i: number, field: keyof Faction, value: string) { const f = [...data.factions]; f[i] = { ...f[i], [field]: value }; setData({ ...data, factions: f }); }
  function removeFaction(i: number) { setData({ ...data, factions: data.factions.filter((_, idx) => idx !== i) }); }
  function addPowerSystem() { setData({ ...data, power_system: [...data.power_system, { name: "", levels: "", rules: "", limitations: "" }] }); }
  function updatePowerSystem(i: number, field: keyof PowerSystem, value: string) { const p = [...data.power_system]; p[i] = { ...p[i], [field]: value }; setData({ ...data, power_system: p }); }
  function removePowerSystem(i: number) { setData({ ...data, power_system: data.power_system.filter((_, idx) => idx !== i) }); }
  function addHistory() { setData({ ...data, history: [...data.history, { event: "", time: "", description: "", impact: "" }] }); }
  function updateHistory(i: number, field: keyof HistoryEvent, value: string) { const h = [...data.history]; h[i] = { ...h[i], [field]: value }; setData({ ...data, history: h }); }
  function removeHistory(i: number) { setData({ ...data, history: data.history.filter((_, idx) => idx !== i) }); }
  function addConflict() { setData({ ...data, conflicts: [...data.conflicts, { name: "", type: "", parties: "", stakes: "", resolution_hint: "" }] }); }
  function updateConflict(i: number, field: keyof Conflict, value: string) { const c = [...data.conflicts]; c[i] = { ...c[i], [field]: value }; setData({ ...data, conflicts: c }); }
  function removeConflict(i: number) { setData({ ...data, conflicts: data.conflicts.filter((_, idx) => idx !== i) }); }
  function addSpecial() { setData({ ...data, special_settings: [...data.special_settings, { name: "", description: "", rules: "" }] }); }
  function updateSpecial(i: number, field: keyof SpecialSetting, value: string) { const s = [...data.special_settings]; s[i] = { ...s[i], [field]: value }; setData({ ...data, special_settings: s }); }
  function removeSpecial(i: number) { setData({ ...data, special_settings: data.special_settings.filter((_, idx) => idx !== i) }); }

  const MODES = [
    { key: "manual" as const, label: "手动创建", desc: "逐项填写世界观模板" },
    { key: "import" as const, label: "导入文档", desc: "上传/粘贴文本，AI 自动提取" },
    { key: "hybrid" as const, label: "混合模式", desc: "导入后可手动追加修改" },
  ];

  if (
    relationalMode === null &&
    !scopeLoading &&
    loadedScopeKey === currentScopeKey
  ) {
    return (
      <div ref={editorRootRef}>
        <button className="btn-back" onClick={handleBack}>← 返回项目详情</button>
        <div className="card empty-state" role={loreModeError ? "alert" : "status"} aria-busy={!loreModeError}>
          <h2>{loreModeError ? "无法确认设定仓库模式" : "正在确认设定仓库模式"}</h2>
          <p>{loreModeError || "确认完成前不会显示旧编辑流程，也不会调用 AI。"}</p>
          {loreModeError && <button className="btn btn-primary" type="button" onClick={() => setLoreModeReloadToken((value) => value + 1)}>重新确认仓库模式</button>}
        </div>
      </div>
    );
  }

  const extractionSourceMatches = extractionDraft?.documentText === importText.trim();
  const extractionUncertain = extractionDraft != null && ["submitting", "running", "maintenance", "outcome_unknown"].includes(extractionDraft.phase);
  const extractionCanAbandon = extractionCorrupt || extractionDraft?.phase === "completed" || extractionDraft?.phase === "failed";
  const extractionCanStartAgain = extractionDraft?.phase === "failed" && (
    extractionDraft.retryable || extractionDraft.errorStatus === 413
  );
  const extractionPrimaryLabel = extractionDraft?.phase === "completed"
    ? extractionDraft.candidateCount && extractionDraft.candidateCount > 0
      ? "前往待审核设定"
      : "前往仓库手动创建"
    : extractionDraft?.phase === "failed"
      ? "本次提取失败"
      : extractionDraft?.phase === "maintenance"
        ? "维护结束后重试提取"
        : extractionDraft && extractionSourceMatches
          ? "核对上次提取结果"
          : "提取为待审核设定";

  function handleExtractionPrimary() {
    if (extractionDraft?.phase === "completed") {
      extractionDraft.candidateCount && extractionDraft.candidateCount > 0
        ? (onExtractionComplete ? onExtractionComplete() : onComplete())
        : onComplete();
      return;
    }
    void handleStrictExtraction(false);
  }

  return (
    <div ref={editorRootRef}>
      <button className="btn-back" onClick={handleBack}>← 返回项目详情</button>
      <span className="sr-only" aria-live="polite">
        {draftMessage}
      </span>

      {maintenanceFailure && (
        <MaintenanceNotice
          error={maintenanceFailure.error}
          draftStored={maintenanceFailure.draftStored}
          onCopy={() => void copyWorldview()}
          onRetry={() => void handleSave()}
          onBack={handleBack}
          focusOnMount
        />
      )}

      {saveResult && !maintenanceFailure && !saveError && (
        <div
          ref={saveResultRef}
          className="draft-notice"
          style={{ borderColor: "var(--gold)", background: "var(--gold-light)" }}
          role="status"
          tabIndex={-1}
        >
          <h3>世界观已保存</h3>
          <p>
            {saveResult === "saved_clean"
              ? "世界观已保存到项目，本地草稿已清除。"
              : saveResult === "saved_new_edit_local"
              ? "提交时版本已保存，当前新编辑尚未保存到项目，已保留本地草稿。"
              : saveResult === "saved_new_edit_failed"
              ? "提交时版本已保存，但新编辑未能写入本地草稿，请立即复制内容。"
              : "世界观已保存到项目，但旧本地草稿未能清除；再次进入时请核对后手动放弃。"}
          </p>
          {saveResult === "saved_new_edit_failed" && (
            <div className="draft-notice__actions">
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void copyWorldview()}
              >
                复制未保存内容
              </button>
            </div>
          )}
          {migrationTarget && onReturnToMigration && saveResult === "saved_clean" && (
            <div className="draft-notice__actions">
              <button type="button" className="btn btn-primary" onClick={onReturnToMigration}>
                返回并重新检查
              </button>
            </div>
          )}
        </div>
      )}

      {saveError && !maintenanceFailure && (
        <div className="draft-notice draft-notice--maintenance" role="alert">
          <h3>保存失败</h3>
          <p>{saveError}</p>
          <div className="draft-notice__actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => worldviewStale ? reloadProjectVersionAfterStale() : void handleSave()}
            >
              {worldviewStale ? "重新加载项目版本" : "重试保存"}
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => void copyWorldview()}
            >
              复制内容
            </button>
          </div>
        </div>
      )}

      {draftStorageFailed && !maintenanceFailure && !saveError &&
       saveResult !== "saved_new_edit_failed" && saveResult !== "saved_draft_failed" && (
        <div className="draft-notice draft-notice--maintenance" role="alert">
          <h3>本地草稿未能保存</h3>
          <p>请立即复制当前内容；返回或刷新页面可能造成内容丢失。</p>
          <div className="draft-notice__actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void copyWorldview()}
            >
              复制未保存内容
            </button>
          </div>
        </div>
      )}

      {corruptDraft && (
        <div className="draft-notice draft-notice--maintenance" role="alert">
          <h3>本地草稿无法读取</h3>
          <p>本地草稿已损坏或格式不兼容，项目中已保存的版本未受影响。</p>
          <div className="draft-notice__actions">
            <button
              type="button"
              className="btn btn-danger"
              onClick={discardCorruptDraft}
            >
              确认丢弃损坏草稿
            </button>
          </div>
        </div>
      )}

      {recovery && (
        <div ref={draftRecoveryContainerRef} tabIndex={-1}>
          <DraftRecoveryNotice
            state={recovery.state}
            savedAt={new Date(recovery.draft.savedAt).toISOString()}
            onRestore={restoreDraft}
            onCopy={() => void copyWorldview(recovery.draft.payload)}
            onDiscard={discardDraft}
          />
        </div>
      )}

      {copyFallback !== null && (
        <div className="draft-notice">
          <label htmlFor="worldview-copy-fallback">
            自动复制失败，请全选下方纯文本副本并复制
          </label>
          <textarea
            id="worldview-copy-fallback"
            className="form-textarea worldview-copy-fallback"
            value={copyFallback}
            readOnly
            onFocus={(event) => event.currentTarget.select()}
          />
        </div>
      )}

      {(migrationTarget || migrationRequestInvalid) && loadedScopeKey === currentScopeKey && !scopeLoading && (
        <div
          id="worldview-migration-fix-notice"
          ref={migrationNoticeRef}
          className="draft-notice worldview-migration-fix"
          tabIndex={-1}
          role={migrationTargetState === "invalid" || migrationTargetState === "error" || migrationRequestInvalid ? "alert" : "status"}
        >
          <h3>修正旧资料检查项</h3>
          <p>
            {migrationRequestInvalid
              ? "这个修正链接无效或不完整。系统没有定位或修改任何资料，请返回预检重新检查。"
              : migrationTargetState === "loading" || migrationTargetState === "idle"
              ? "正在根据最新的已保存资料核对这项检查结果。"
              : migrationTargetState === "error"
              ? "暂时无法核对这项资料，系统没有定位或修改任何内容。你可以重试核对或返回预检。"
              : migrationTargetExists && migrationTarget
              ? `正在处理：旧世界观 › ${migrationCategoryLabels[migrationTarget.category] ?? "资料"} › 第 ${migrationTarget.index + 1} 项。系统不会替你补写或自动保存。`
              : "这项资料的位置已经变化。请返回预检重新检查，系统不会按旧位置修改内容。"}
          </p>
          {migrationTargetExists && migrationTargetState === "valid" && (
            <div className="draft-notice__actions">
              <button type="button" className="btn btn-primary" onClick={locateMigrationTarget} disabled={!migrationCanLocate}>
                {isReadOnly ? "切换为混合模式并定位" : "定位到这项资料"}
              </button>
              {onReturnToMigration && (
                <button type="button" className="btn btn-secondary" onClick={onReturnToMigration}>
                  返回迁移预检
                </button>
              )}
            </div>
          )}
          {migrationTargetState === "error" && (
            <div className="draft-notice__actions">
              <button type="button" className="btn btn-primary" onClick={() => setMigrationPreviewReloadToken((value) => value + 1)}>
                重试核对
              </button>
              {onReturnToMigration && <button type="button" className="btn btn-secondary" onClick={onReturnToMigration}>返回迁移预检</button>}
            </div>
          )}
          {(migrationRequestInvalid || migrationTargetState === "invalid") && onReturnToMigration && (
            <div className="draft-notice__actions">
              <button type="button" className="btn btn-primary" onClick={onReturnToMigration}>
                返回并重新检查
              </button>
            </div>
          )}
          {(recovery || corruptDraft) && <small>请先处理上方本地草稿，再定位和编辑。</small>}
          {!recovery && !corruptDraft && migrationTargetState === "valid" && !migrationSourceUnchanged && (
            <small>当前编辑区已有未保存变更。请先保存或返回预检，系统不会按旧位置继续定位。</small>
          )}
        </div>
      )}

      {loadedScopeKey !== currentScopeKey || scopeLoading ? (
        <div className="card">正在加载世界观…</div>
      ) : scopeLoadError ? (
        <div className="draft-notice draft-notice--maintenance" role="alert">
          <h3>世界观加载失败</h3>
          <p>{scopeLoadError}</p>
          <div className="draft-notice__actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void loadWorldview()}
              disabled={reloading}
              aria-busy={reloading}
            >
              {reloading ? "正在重新加载…" : "重新加载"}
            </button>
          </div>
        </div>
      ) : (
      <fieldset
        className="worldview-workspace"
        disabled={recovery !== null || corruptDraft}
        aria-describedby={recovery ? "worldview-recovery-lock" : undefined}
      >
      <legend className="sr-only">世界观编辑区</legend>
      {recovery && (
        <p id="worldview-recovery-lock" className="sr-only">
          请先载入或丢弃本地草稿，再继续编辑。
        </p>
      )}
      {corruptDraft && !recovery && (
        <p id="worldview-recovery-lock" className="sr-only">
          请先丢弃损坏的本地草稿，再继续编辑。
        </p>
      )}

      {/* Mode selector */}
      <div className="card">
        <div className="wv-section-title">世界观创建方式</div>
        <div style={{ display: "flex", gap: "0.625rem", flexWrap: "wrap" }}>
          {(relationalMode ? MODES.filter((item) => item.key === "import") : MODES).map((m) => (
            <button
              key={m.key}
              className="btn"
              onClick={() => switchMode(m.key)}
              aria-pressed={mode === m.key}
              style={{
                flex: "1 1 180px",
                flexDirection: "column",
                alignItems: "flex-start",
                padding: "0.625rem 0.875rem",
                borderColor: mode === m.key ? "var(--gold)" : "var(--border)",
                background: mode === m.key ? "var(--gold-light)" : "var(--white)",
                borderWidth: "2px",
              }}
            >
              <span style={{ fontWeight: 600, fontSize: "14px", color: mode === m.key ? "var(--gold-dark)" : "var(--text-1)" }}>{m.label}</span>
              <span style={{ fontSize: "11px", color: "var(--text-3)", marginTop: "2px" }}>{m.desc}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Import panel */}
      {mode !== "manual" && (
        <div className="card">
          <div className="wv-section-title">导入世界观文档</div>
          <p className="form-hint" style={{ marginBottom: "0.625rem" }}>
            {relationalMode === null
              ? "正在确认当前项目的设定仓库模式；确认完成前不会调用 AI。"
              : relationalMode
              ? "粘贴或上传世界观原文。AI 只生成逐项待审核候选，不会自动写入正式设定；你需要逐项接纳或拒绝。"
              : "粘贴或上传包含世界观设定的文档（支持 .txt / .md / .doc / .docx）。当前兼容项目会先填充旧世界观表单，不会自动同步正式设定仓库。"}
          </p>
          <textarea
            className="form-textarea worldview-import-text"
            style={{ minHeight: "140px", fontFamily: "var(--font-mono)" }}
            placeholder={"在此粘贴世界观文档内容...\n\n例如：\n林远，男，18岁，性格坚韧...\n苍澜大陆分为东南西北四大区域...\n修炼境界：聚气境、筑基境、金丹境..."}
            value={importText}
            onChange={(e) => {
              const newText = e.target.value;
              setImportText(newText);
              // 修改原文后使旧解析结果失效，要求重新解析
              if (importResult?.done && newText !== importText) {
                setImportResult(null);
                setParsedInfo(null);
                setReparseNeeded(true);
                setDraftMessage(
                  "原文已修改，上次提取结果已失效，请重新解析。"
                );
              }
            }}
            disabled={importing}
            aria-label="导入世界观文档原文"
          />
          <div className="lore-candidate-actions">
            <input ref={fileInputRef} type="file" accept=".txt,.md,.markdown,.doc,.docx" onChange={handleFileUpload} style={{ display: "none" }} />
            <button className="btn" onClick={() => fileInputRef.current?.click()} disabled={importing}>
              {importing ? "解析文件中..." : "上传文件"}
            </button>
            <button
              className="btn btn-primary"
              onClick={() => relationalMode ? handleExtractionPrimary() : void handleImport()}
              disabled={
                importing ||
                extractionCorrupt ||
                (extractionDraft?.phase !== "completed" && importText.trim().length < 10) ||
                extractionDraft?.phase === "failed"
              }
            >
              {importing ? "AI 提取中..." : relationalMode ? extractionPrimaryLabel : "兼容解析并填充表单"}
            </button>
            {relationalMode && extractionCanStartAgain && (
              <button className="btn btn-secondary" type="button" disabled={importing || (extractionDraft?.errorStatus === 413 && extractionSourceMatches)} onClick={() => void handleStrictExtraction(true)}>{extractionDraft?.errorStatus === 413 ? "修改后重新提取" : "明确重新提取"}</button>
            )}
            {relationalMode && extractionUncertain && !extractionSourceMatches && (
              <button className="btn btn-secondary" type="button" disabled={importing} onClick={restoreExtractionSource}>恢复上次任务原文</button>
            )}
            {relationalMode && extractionCanAbandon && (
              <button className="btn btn-secondary" type="button" disabled={importing} onClick={abandonExtraction}>放弃任务状态</button>
            )}
            {!relationalMode && importResult?.done && (
              <span className="tag tag-gold" style={{ fontWeight: 600 }}>已提取 {importResult.count} 个要素</span>
            )}
          </div>
          {relationalMode && extractionNotice && (
            <div ref={extractionResultRef} tabIndex={-1} role="status" className="draft-notice" style={{ marginTop: "0.625rem" }}><p>{extractionNotice}</p></div>
          )}
          {relationalMode && extractionError && (
            <div role="alert" className="draft-notice draft-notice--maintenance" style={{ marginTop: "0.625rem" }}><p>{extractionError}</p></div>
          )}
          {loreModeError && (
            <div role="alert" className="draft-notice draft-notice--maintenance" style={{ marginTop: "0.625rem" }}><p>{loreModeError}</p></div>
          )}
          {mode === "hybrid" && importResult?.done && (
            <div style={{ marginTop: "0.625rem", padding: "0.5rem 0.75rem", background: "var(--gold-light)", borderRadius: "var(--r-md)", fontSize: "12px", color: "var(--gold-dark)", borderLeft: "3px solid var(--gold)" }}>
              混合模式已激活 — 下方表单已填充提取的要素，你可以自由编辑、添加或删除任意内容
            </div>
          )}
          {importResult?.done && !saved && !reparseNeeded && (
            <div style={{ marginTop: "0.625rem", padding: "0.5rem 0.75rem", background: "#fef9e7", borderRadius: "var(--r-md)", fontSize: "13px", color: "#7d6608", borderLeft: "3px solid #f39c12" }}>
              ⚠️ 世界观已提取但尚未保存。请点击下方「保存世界观」，保存后可进入设定仓库继续整理。
            </div>
          )}
          {reparseNeeded && (
            <div style={{ marginTop: "0.625rem", padding: "0.5rem 0.75rem", background: "var(--red-light)", borderRadius: "var(--r-md)", fontSize: "13px", color: "var(--red)", borderLeft: "3px solid var(--red)" }}>
              原文已修改，上次提取结果已失效，请重新解析。
            </div>
          )}
          {importError && (
            <div className="draft-notice draft-notice--maintenance" role="alert" style={{ marginTop: "0.625rem" }}>
              <p>{importError}</p>
            </div>
          )}
          {uploadError && (
            <div className="draft-notice draft-notice--maintenance" role="alert" style={{ marginTop: "0.625rem" }}>
              <p>{uploadError}</p>
            </div>
          )}
        </div>
      )}

      {/* Worldview editor */}
      {showEditor && (
        <>
          {/* Characters */}
          <div className="card">
            <div className="wv-section-title" tabIndex={-1}>角色 ({data.characters.length})</div>
            {data.characters.map((c, i) => (
              <div key={i} className={`wv-entry ${isMigrationTarget("characters", i) ? "is-migration-target" : ""}`} data-migration-target={`characters:${i}`}>
                <input className="form-input" placeholder="姓名" value={c.name} onChange={(e) => updateCharacter(i, "name", e.target.value)} readOnly={isReadOnly} aria-label={`角色${i + 1} 姓名`} aria-describedby={isMigrationTarget("characters", i) ? "worldview-migration-fix-notice" : undefined} />
                <input className="form-input" placeholder="性格" value={c.personality} onChange={(e) => updateCharacter(i, "personality", e.target.value)} readOnly={isReadOnly} aria-label={`角色${i + 1} 性格`} />
                <input className="form-input" placeholder="背景" value={c.background} onChange={(e) => updateCharacter(i, "background", e.target.value)} readOnly={isReadOnly} aria-label={`角色${i + 1} 背景`} />
                <div style={{ display: "flex", gap: "0.375rem" }}>
                  <input className="form-input" placeholder="动机" value={c.motivation} onChange={(e) => updateCharacter(i, "motivation", e.target.value)} readOnly={isReadOnly} aria-label={`角色${i + 1} 动机`} />
                  {!isReadOnly && <button className="btn btn-ghost btn-sm" onClick={() => removeCharacter(i)} style={{ color: "var(--red)" }} aria-label={`移除角色 ${i + 1}`}><span aria-hidden="true">✕</span></button>}
                </div>
                <input className="form-input" placeholder="能力/特长" value={c.ability} onChange={(e) => updateCharacter(i, "ability", e.target.value)} readOnly={isReadOnly} aria-label={`角色${i + 1} 能力/特长`} style={{ gridColumn: "1 / -1" }} />
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addCharacter}>+ 添加角色</button>}
          </div>

          {/* Geography */}
          <div className="card">
            <div className="wv-section-title" tabIndex={-1}>地理设定 ({data.geography.length})</div>
            {data.geography.map((g, i) => (
              <div key={i} className={`wv-entry ${isMigrationTarget("geography", i) ? "is-migration-target" : ""}`} data-migration-target={`geography:${i}`}>
                <input className="form-input" placeholder="地名" value={g.name} onChange={(e) => updateGeography(i, "name", e.target.value)} readOnly={isReadOnly} aria-label={`地点${i + 1} 名称`} aria-describedby={isMigrationTarget("geography", i) ? "worldview-migration-fix-notice" : undefined} />
                <input className="form-input" placeholder="描述" value={g.description} onChange={(e) => updateGeography(i, "description", e.target.value)} readOnly={isReadOnly} aria-label={`地点${i + 1} 描述`} />
                <input className="form-input" placeholder="重要性" value={g.significance} onChange={(e) => updateGeography(i, "significance", e.target.value)} readOnly={isReadOnly} aria-label={`地点${i + 1} 重要性`} />
                {!isReadOnly && <button className="btn btn-ghost btn-sm" onClick={() => removeGeography(i)} style={{ color: "var(--red)" }} aria-label={`移除地点 ${i + 1}`}><span aria-hidden="true">✕</span></button>}
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addGeography}>+ 添加地点</button>}
          </div>

          {/* Factions */}
          <div className="card">
            <div className="wv-section-title" tabIndex={-1}>势力组织 ({data.factions.length})</div>
            {data.factions.map((f, i) => (
              <div key={i} className={`wv-entry ${isMigrationTarget("factions", i) ? "is-migration-target" : ""}`} data-migration-target={`factions:${i}`}>
                <input className="form-input" placeholder="势力名称" value={f.name} onChange={(e) => updateFaction(i, "name", e.target.value)} readOnly={isReadOnly} aria-label={`势力${i + 1} 名称`} aria-describedby={isMigrationTarget("factions", i) ? "worldview-migration-fix-notice" : undefined} />
                <input className="form-input" placeholder="立场" value={f.stance} onChange={(e) => updateFaction(i, "stance", e.target.value)} readOnly={isReadOnly} aria-label={`势力${i + 1} 立场`} />
                <input className="form-input" placeholder="实力等级" value={f.power_level} onChange={(e) => updateFaction(i, "power_level", e.target.value)} readOnly={isReadOnly} aria-label={`势力${i + 1} 实力等级`} />
                {!isReadOnly && <button className="btn btn-ghost btn-sm" onClick={() => removeFaction(i)} style={{ color: "var(--red)" }} aria-label={`移除势力 ${i + 1}`}><span aria-hidden="true">✕</span></button>}
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addFaction}>+ 添加势力</button>}
          </div>

          {/* Power System */}
          <div className="card">
            <div className="wv-section-title" tabIndex={-1}>力量体系 ({data.power_system.length})</div>
            {data.power_system.map((ps, i) => (
              <div key={i} className={`wv-entry ${isMigrationTarget("power_system", i) ? "is-migration-target" : ""}`} data-migration-target={`power_system:${i}`}>
                <input className="form-input" placeholder="体系名称" value={ps.name} onChange={(e) => updatePowerSystem(i, "name", e.target.value)} readOnly={isReadOnly} aria-label={`体系${i + 1} 名称`} aria-describedby={isMigrationTarget("power_system", i) ? "worldview-migration-fix-notice" : undefined} />
                <input className="form-input" placeholder="等级划分" value={ps.levels} onChange={(e) => updatePowerSystem(i, "levels", e.target.value)} readOnly={isReadOnly} aria-label={`体系${i + 1} 等级划分`} />
                <input className="form-input" placeholder="规则" value={ps.rules} onChange={(e) => updatePowerSystem(i, "rules", e.target.value)} readOnly={isReadOnly} aria-label={`体系${i + 1} 规则`} />
                <div style={{ display: "flex", gap: "0.375rem" }}>
                  <input className="form-input" placeholder="限制" value={ps.limitations} onChange={(e) => updatePowerSystem(i, "limitations", e.target.value)} readOnly={isReadOnly} aria-label={`体系${i + 1} 限制`} />
                  {!isReadOnly && <button className="btn btn-ghost btn-sm" onClick={() => removePowerSystem(i)} style={{ color: "var(--red)" }} aria-label={`移除体系 ${i + 1}`}><span aria-hidden="true">✕</span></button>}
                </div>
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addPowerSystem}>+ 添加体系</button>}
          </div>

          {/* History */}
          <div className="card">
            <div className="wv-section-title" tabIndex={-1}>历史事件 ({data.history.length})</div>
            {data.history.map((h, i) => (
              <div key={i} className={`wv-entry ${isMigrationTarget("history", i) ? "is-migration-target" : ""}`} data-migration-target={`history:${i}`}>
                <input className="form-input" placeholder="事件名称" value={h.event} onChange={(e) => updateHistory(i, "event", e.target.value)} readOnly={isReadOnly} aria-label={`事件${i + 1} 名称`} aria-describedby={isMigrationTarget("history", i) ? "worldview-migration-fix-notice" : undefined} />
                <input className="form-input" placeholder="时间" value={h.time} onChange={(e) => updateHistory(i, "time", e.target.value)} readOnly={isReadOnly} aria-label={`事件${i + 1} 时间`} />
                <input className="form-input" placeholder="描述" value={h.description} onChange={(e) => updateHistory(i, "description", e.target.value)} readOnly={isReadOnly} aria-label={`事件${i + 1} 描述`} />
                <div style={{ display: "flex", gap: "0.375rem" }}>
                  <input className="form-input" placeholder="影响" value={h.impact} onChange={(e) => updateHistory(i, "impact", e.target.value)} readOnly={isReadOnly} aria-label={`事件${i + 1} 影响`} />
                  {!isReadOnly && <button className="btn btn-ghost btn-sm" onClick={() => removeHistory(i)} style={{ color: "var(--red)" }} aria-label={`移除事件 ${i + 1}`}><span aria-hidden="true">✕</span></button>}
                </div>
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addHistory}>+ 添加事件</button>}
          </div>

          {/* Conflicts */}
          <div className="card">
            <div className="wv-section-title" tabIndex={-1}>核心矛盾 ({data.conflicts.length})</div>
            {data.conflicts.map((c, i) => (
              <div key={i} className={`wv-entry ${isMigrationTarget("conflicts", i) ? "is-migration-target" : ""}`} data-migration-target={`conflicts:${i}`}>
                <input className="form-input" placeholder="矛盾名称" value={c.name} onChange={(e) => updateConflict(i, "name", e.target.value)} readOnly={isReadOnly} aria-label={`矛盾${i + 1} 名称`} aria-describedby={isMigrationTarget("conflicts", i) ? "worldview-migration-fix-notice" : undefined} />
                <input className="form-input" placeholder="类型" value={c.type} onChange={(e) => updateConflict(i, "type", e.target.value)} readOnly={isReadOnly} aria-label={`矛盾${i + 1} 类型`} />
                <input className="form-input" placeholder="涉及方" value={c.parties} onChange={(e) => updateConflict(i, "parties", e.target.value)} readOnly={isReadOnly} aria-label={`矛盾${i + 1} 涉及方`} />
                <div style={{ display: "flex", gap: "0.375rem" }}>
                  <input className="form-input" placeholder="利害关系" value={c.stakes} onChange={(e) => updateConflict(i, "stakes", e.target.value)} readOnly={isReadOnly} aria-label={`矛盾${i + 1} 利害关系`} />
                  {!isReadOnly && <button className="btn btn-ghost btn-sm" onClick={() => removeConflict(i)} style={{ color: "var(--red)" }} aria-label={`移除矛盾 ${i + 1}`}><span aria-hidden="true">✕</span></button>}
                </div>
                <input className="form-input" placeholder="解决线索" value={c.resolution_hint} onChange={(e) => updateConflict(i, "resolution_hint", e.target.value)} readOnly={isReadOnly} aria-label={`矛盾${i + 1} 解决线索`} style={{ gridColumn: "1 / -1" }} />
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addConflict}>+ 添加矛盾</button>}
          </div>

          {/* Special Settings */}
          <div className="card">
            <div className="wv-section-title" tabIndex={-1}>特殊设定 ({data.special_settings.length})</div>
            {data.special_settings.map((ss, i) => (
              <div key={i} className={`wv-entry ${isMigrationTarget("special_settings", i) ? "is-migration-target" : ""}`} data-migration-target={`special_settings:${i}`}>
                <input className="form-input" placeholder="设定名称" value={ss.name} onChange={(e) => updateSpecial(i, "name", e.target.value)} readOnly={isReadOnly} aria-label={`设定${i + 1} 名称`} aria-describedby={isMigrationTarget("special_settings", i) ? "worldview-migration-fix-notice" : undefined} />
                <input className="form-input" placeholder="描述" value={ss.description} onChange={(e) => updateSpecial(i, "description", e.target.value)} readOnly={isReadOnly} aria-label={`设定${i + 1} 描述`} />
                <input className="form-input" placeholder="规则" value={ss.rules} onChange={(e) => updateSpecial(i, "rules", e.target.value)} readOnly={isReadOnly} aria-label={`设定${i + 1} 规则`} />
                {!isReadOnly && <button className="btn btn-ghost btn-sm" onClick={() => removeSpecial(i)} style={{ color: "var(--red)" }} aria-label={`移除设定 ${i + 1}`}><span aria-hidden="true">✕</span></button>}
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addSpecial}>+ 添加设定</button>}
          </div>

          {/* Parsed elements info */}
          {parsedInfo && parsedInfo.total > 0 && (
            <div className="card" style={{ background: "var(--gold-light)", borderColor: "var(--gold-border)", borderLeftWidth: "3px", borderLeftColor: "var(--gold)" }}>
              <div style={{ fontWeight: 600, marginBottom: "0.5rem", color: "var(--gold-dark)", fontSize: "14px" }}>
                世界观已解析 — 共 {parsedInfo.total} 个要素
              </div>
              <div style={{ display: "flex", gap: "0.375rem", flexWrap: "wrap" }}>
                {Object.entries(parsedInfo.by_priority).map(([pri, count]) => (
                  <span key={pri} className={`tag tag-${pri}`}>
                    {pri === "core" ? "核心" : pri === "important" ? "重要" : pri === "secondary" ? "次要" : "背景"}: {count}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Actions — 底部导航按钮 */}
          <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.875rem", flexWrap: "wrap" }}>
            <button className="btn btn-primary btn-lg" onClick={handleSave} disabled={loading}>
              {loading ? "保存中..." : "保存世界观"}
            </button>
            {/* 保存过世界观后可以进入独立设定仓库。 */}
            {(hasWorldview || saved) && (
              <button
                className="btn btn-danger btn-lg"
                onClick={() => {
                  if (dirtyRef.current) {
                    // 有未保存编辑 — 先落本地草稿并提示，不直接跳转
                    const storedNow = storeCurrentDraft().stored;
                    if (storedNow) {
                      setNextStepBlocked(true);
                      setDraftMessage("内容仅保存在本设备，尚未保存到项目。建议先保存世界观后再打开设定仓库。");
                    } else {
                      setDraftMessage("存在未保存编辑且本地草稿也未能保留，请先复制内容。");
                      return;
                    }
                  } else {
                    onComplete();
                  }
                }}
              >
                打开设定仓库 →
              </button>
            )}
          </div>

          {nextStepBlocked && (
            <div className="draft-notice" style={{ marginTop: "0.875rem" }}>
              <h3>内容仅保存在本设备</h3>
              <p>编辑已保留到本地草稿（本设备），尚未保存到项目。建议保存世界观后再打开设定仓库；也可只查看现有设定。</p>
              <div className="draft-notice__actions">
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => {
                    setNextStepBlocked(false);
                    onComplete();
                  }}
                >
                  仍要打开设定仓库
                </button>
                <button
                  type="button"
                  className="btn"
                  onClick={() => setNextStepBlocked(false)}
                >
                  留在编辑器
                </button>
              </div>
            </div>
          )}
        </>
      )}

      {mode === "manual" && !hasWorldview && (
        <div className="card" style={{ background: "var(--gold-light)", borderColor: "var(--gold-border)", borderLeftWidth: "3px", borderLeftColor: "var(--gold)" }}>
          <p style={{ fontSize: "13px", color: "var(--gold-dark)", lineHeight: 1.7 }}>
            填写世界观架构。系统会自动解析为结构化要素并分配优先级（核心/重要/次要/背景），
            保存后可在统一的设定仓库中继续分类查看和管理。
          </p>
        </div>
      )}
      </fieldset>
      )}
    </div>
  );
}
