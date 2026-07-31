import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/services/api";
import { useAuth } from "@/components/AuthContext";
import {
  clearDraft,
  getDraftRaw,
  loadDraft,
  saveDraft,
  type WorldviewDraft,
} from "@/components/WorldviewDraftStorage";
import type {
  Character,
  Conflict,
  Faction,
  Geography,
  HistoryEvent,
  PowerSystem,
  SpecialSetting,
  WorldviewData,
  WorldviewSource,
} from "@/types";

interface Props {
  projectId: string;
  hasWorldview: boolean;
  genre: string;
  onComplete: () => void;
  onBack: () => void;
}

type EditorMode = "manual" | "import" | "hybrid";

const EMPTY_WORLDVIEW: WorldviewData = {
  characters: [],
  geography: [],
  factions: [],
  power_system: [],
  history: [],
  conflicts: [],
  special_settings: [],
};

const AUTOSAVE_DELAY = 700;

type RecoveryState =
  | { kind: "none" }
  | { kind: "ok"; draft: WorldviewDraft }
  | { kind: "corrupt"; raw: string }
  | { kind: "error"; message: string };

/** Snapshot captured at save-request start for race-condition detection (P0-2). */
interface SaveSnapshot {
  userId: string;
  projectId: string;
  data: WorldviewData;
  importText: string;
  mode: EditorMode;
  source: WorldviewSource;
}

function snapshotKey(s: {
  data: WorldviewData;
  importText: string;
  mode: EditorMode;
  source: WorldviewSource;
}): string {
  return JSON.stringify([s.data, s.importText, s.mode, s.source]);
}

export default function WorldviewEditor({ projectId, hasWorldview, genre, onComplete, onBack }: Props) {
  const { user } = useAuth();
  const userId = user?.id ?? "anonymous";

  const [mode, setMode] = useState<EditorMode>("manual");
  const [data, setData] = useState<WorldviewData>(EMPTY_WORLDVIEW);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [parsedInfo, setParsedInfo] = useState<{ total: number; by_category: Record<string, number>; by_priority: Record<string, number> } | null>(null);
  const [importText, setImportText] = useState("");
  const [importResult, setImportResult] = useState<{ count: number; done: boolean } | null>(null);
  const [source, setSource] = useState<WorldviewSource>("manual");
  const [saved, setSaved] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [recovery, setRecovery] = useState<RecoveryState>({ kind: "none" });
  const [showUnsavedConfirm, setShowUnsavedConfirm] = useState<null | "back" | "next">(null);
  const [saveError, setSaveError] = useState(false);
  // P0-1: track local storage failures separately from server save failures
  const [localSaveError, setLocalSaveError] = useState(false);
  // P0-5: secondary confirmation for corrupt draft discard
  const [confirmDiscardCorrupt, setConfirmDiscardCorrupt] = useState(false);
  // P0-1: track draft save failure when trying to leave
  const [leaveSaveFailed, setLeaveSaveFailed] = useState(false);
  // P0-2: copy failure state — show manual copy textarea
  const [copyFailed, setCopyFailed] = useState(false);
  const [copyContent, setCopyContent] = useState("");
  // P1-5: visible error inside recovery dialog when clearDraft fails
  const [clearDraftError, setClearDraftError] = useState("");

  // ── Refs ──
  const generationRef = useRef(0);
  const editRevisionRef = useRef(0);
  const loadedRef = useRef(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const autosaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastFocusedRef = useRef<HTMLElement | null>(null);
  const recoveryDialogRef = useRef<HTMLDivElement>(null);
  const unsavedDialogRef = useRef<HTMLDivElement>(null);
  const skipAutosaveRef = useRef(true);
  // P1-4: ref for auto-focusing the manual copy textarea
  const copyTextareaRef = useRef<HTMLTextAreaElement>(null);

  // P0-3: Refs for pagehide — avoid stale closures
  const dirtyRef = useRef(false);
  const recoveryRef = useRef<RecoveryState>({ kind: "none" });
  const initializedScopeRef = useRef(false);

  // P0-1: Track whether loadWorldview has been called for current scope
  const scopeLoadInitiatedRef = useRef(false);
  // P0-1: Request token for loadWorldview — prevents duplicate concurrent loads
  const loadTokenRef = useRef(0);

  // P1: Focus capture tracking — only capture on first open
  const recoveryFocusCapturedRef = useRef(false);
  const unsavedFocusCapturedRef = useRef(false);

  // Current-value refs for async access (P0-2, P0-3)
  const dataRef = useRef(data);
  const importTextRef = useRef(importText);
  const modeRef = useRef(mode);
  const sourceRef = useRef(source);
  dataRef.current = data;
  importTextRef.current = importText;
  modeRef.current = mode;
  sourceRef.current = source;
  dirtyRef.current = dirty;
  recoveryRef.current = recovery;

  // ── P0-1: Bump edit revision on user edits ──
  const bumpEditRevision = useCallback(() => {
    editRevisionRef.current++;
  }, []);

  // ── P0-3: Scope change — generational isolation ──
  useEffect(() => {
    generationRef.current++;
    editRevisionRef.current = 0;
    loadedRef.current = false;
    skipAutosaveRef.current = true;
    initializedScopeRef.current = false;
    scopeLoadInitiatedRef.current = false;
    recoveryFocusCapturedRef.current = false;
    unsavedFocusCapturedRef.current = false;

    // Reset every piece of editor state
    setData(EMPTY_WORLDVIEW);
    setMode("manual");
    setImportText("");
    setSource("manual");
    setSaved(false);
    setDirty(false);
    setParsedInfo(null);
    setImportResult(null);
    setStatusMessage("");
    setSaveError(false);
    setLocalSaveError(false);
    setRecovery({ kind: "none" });
    setShowUnsavedConfirm(null);
    setConfirmDiscardCorrupt(false);
    setLeaveSaveFailed(false);
    setCopyFailed(false);
    setClearDraftError("");

    // P1-1: reset async button locks so new scope starts clean
    setLoading(false);
    setImporting(false);
    // P1-1: invalidate stale file input state
    if (fileInputRef.current) fileInputRef.current.value = "";

    // Load draft for new scope
    const result = loadDraft(userId, projectId);
    if (result.status === "ok") {
      setRecovery({ kind: "ok", draft: result.draft });
    } else if (result.status === "corrupt") {
      setRecovery({ kind: "corrupt", raw: result.raw });
    } else if (result.status === "error") {
      setRecovery({ kind: "error", message: result.message });
    }

    // Mark scope as initialized — pagehide may save after this point
    initializedScopeRef.current = true;

    // Load worldview from server (generation+editRevision guarded inside)
    scopeLoadInitiatedRef.current = true;
    loadWorldview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, projectId]);

  // ── Load existing worldview when hasWorldview toggles ──
  // P0-1: Only trigger if no load has been initiated for this scope yet,
  // preventing duplicate GETs when both effects fire on mount.
  useEffect(() => {
    if (hasWorldview && !loadedRef.current && !scopeLoadInitiatedRef.current) {
      scopeLoadInitiatedRef.current = true;
      loadWorldview();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasWorldview]);

  // ── P0-1: Autosave with real result checking ──
  useEffect(() => {
    if (skipAutosaveRef.current) {
      skipAutosaveRef.current = false;
      return;
    }
    // Don't autosave while recovery dialog is pending
    if (recovery.kind !== "none") return;
    // Don't autosave if there are no unsaved changes
    if (!dirty) return;

    if (autosaveTimerRef.current) clearTimeout(autosaveTimerRef.current);
    autosaveTimerRef.current = setTimeout(() => {
      const result = saveDraft(userId, projectId, {
        data: dataRef.current,
        importText: importTextRef.current,
        mode: modeRef.current,
        source: sourceRef.current,
        savedAt: Date.now(),
        schemaVersion: 1,
      });
      if (result.success) {
        setStatusMessage("草稿已自动保存");
        setLocalSaveError(false);
      } else {
        setStatusMessage("未保存到本机：" + result.message);
        setLocalSaveError(true);
      }
    }, AUTOSAVE_DELAY);
    return () => {
      if (autosaveTimerRef.current) clearTimeout(autosaveTimerRef.current);
    };
  }, [data, importText, mode, source, userId, projectId, recovery.kind, dirty]);

  // ── P0-3: pagehide sync save — use refs, guard against recovery/dirty ──
  useEffect(() => {
    function handlePageHide() {
      // P0-3: Only save if scope is initialized, dirty=true, and no recovery pending
      if (!initializedScopeRef.current) return;
      if (!dirtyRef.current) return; // Don't recreate cleared draft
      if (recoveryRef.current.kind !== "none") return; // Don't overwrite recovery
      saveDraft(userId, projectId, {
        data: dataRef.current,
        importText: importTextRef.current,
        mode: modeRef.current,
        source: sourceRef.current,
        savedAt: Date.now(),
        schemaVersion: 1,
      });
    }
    window.addEventListener("pagehide", handlePageHide);
    return () => window.removeEventListener("pagehide", handlePageHide);
  }, [userId, projectId]);

  // ── Recovery dialog focus management (P1) ──
  useEffect(() => {
    if (recovery.kind !== "none") {
      // P1: Only capture external focus on first open, not on confirmDiscardCorrupt toggle
      if (!recoveryFocusCapturedRef.current) {
        lastFocusedRef.current = document.activeElement as HTMLElement;
        recoveryFocusCapturedRef.current = true;
      }
      const timer = setTimeout(() => {
        if (confirmDiscardCorrupt) {
          // P1: In secondary confirmation, focus "取消" button
          const btns = recoveryDialogRef.current?.querySelectorAll<HTMLElement>("button");
          if (btns) {
            for (const btn of btns) {
              if (btn.textContent === "取消") {
                btn.focus();
                return;
              }
            }
          }
        }
        // In first dialog, focus first safe button (restore or copy)
        const firstBtn = recoveryDialogRef.current?.querySelector<HTMLElement>("button");
        firstBtn?.focus();
      }, 0);
      return () => clearTimeout(timer);
    } else {
      recoveryFocusCapturedRef.current = false;
    }
  }, [recovery, confirmDiscardCorrupt]);

  // ── Unsaved-confirm dialog focus management (P1) ──
  useEffect(() => {
    if (showUnsavedConfirm) {
      // P1: Only capture external focus on first open
      if (!unsavedFocusCapturedRef.current) {
        lastFocusedRef.current = document.activeElement as HTMLElement;
        unsavedFocusCapturedRef.current = true;
      }
      const timer = setTimeout(() => {
        // P1: Always focus "继续编辑" — never "承担风险离开"
        const btns = unsavedDialogRef.current?.querySelectorAll<HTMLElement>("button");
        if (!btns) return;
        for (const btn of btns) {
          if (btn.textContent === "继续编辑") {
            btn.focus();
            return;
          }
        }
      }, 0);
      return () => clearTimeout(timer);
    } else {
      unsavedFocusCapturedRef.current = false;
    }
  }, [showUnsavedConfirm]); // P1: leaveSaveFailed NOT in deps — don't overwrite focus ref

  // ── P1-4: Auto-focus + select the manual copy textarea when it appears ──
  useEffect(() => {
    if (copyFailed && copyTextareaRef.current) {
      const timer = setTimeout(() => {
        copyTextareaRef.current?.focus();
        copyTextareaRef.current?.select();
      }, 0);
      return () => clearTimeout(timer);
    }
  }, [copyFailed]);

  // ── P0-1: Load worldview with generation + editRevision + loadToken guard ──
  async function loadWorldview() {
    const token = ++loadTokenRef.current;
    const gen = generationRef.current;
    const capturedEditRev = editRevisionRef.current;
    const capturedProjectId = projectId;
    try {
      const wv = await api.getWorldview(capturedProjectId);
      // P0-1: Discard if a newer load started, scope changed, or user edited
      if (token !== loadTokenRef.current) return;
      if (gen !== generationRef.current) return;
      if (capturedEditRev !== editRevisionRef.current) return; // user edited
      if (!wv) return;
      loadedRef.current = true;
      skipAutosaveRef.current = true;
      setData({
        characters: wv.characters || [],
        geography: wv.geography || [],
        factions: wv.factions || [],
        power_system: wv.power_system || [],
        history: wv.history || [],
        conflicts: wv.conflicts || [],
        special_settings: wv.special_settings || [],
        raw_text: wv.raw_text,
        source: (wv.source as WorldviewSource) || "manual",
      });
      // P0-1: sync importText from server raw_text so save always submits the right value
      setImportText(wv.raw_text ?? "");
      setSaved(true);
      setDirty(false);
      if (wv.source === "imported") { setMode("import"); setSource("imported"); }
      else if (wv.source === "hybrid") { setMode("hybrid"); setSource("hybrid"); }
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
      if (token !== loadTokenRef.current) return;
      if (gen !== generationRef.current) return;
      if (capturedEditRev !== editRevisionRef.current) return;
      if (e instanceof Error && !e.message.includes("404")) {
        console.error("Failed to load worldview:", e);
      }
    }
  }

  // ── updateData wrapper for dirty tracking + edit revision bump ──
  const updateData = useCallback((newData: WorldviewData) => {
    setData(newData);
    setDirty(true);
    bumpEditRevision();
  }, [bumpEditRevision]);

  // ── P0-1: File upload with generation + editRevision guard ──
  function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const gen = generationRef.current;
    const capturedEditRev = editRevisionRef.current;
    setImporting(true);
    api.uploadWorldviewFile(projectId, file)
      .then((result) => {
        if (gen !== generationRef.current) return;
        if (capturedEditRev !== editRevisionRef.current) return; // P0-1: user edited
        setImportText(result.text);
        // P1-2: uploaded text is a user edit — mark dirty + bump revision
        setDirty(true);
        bumpEditRevision();
      })
      .catch(() => {
        if (gen !== generationRef.current) return;
        setStatusMessage("文件解析失败，请检查文件格式后重试");
      })
      .finally(() => {
        if (gen !== generationRef.current) return;
        setImporting(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      });
  }

  // ── P0-1, P0-4: Import with generation + editRevision guard ──
  async function handleImport() {
    if (!importText.trim() || importText.trim().length < 10) {
      setStatusMessage("请输入或上传至少 10 个字符的文档内容");
      return;
    }
    const gen = generationRef.current;
    const capturedEditRev = editRevisionRef.current;
    setImporting(true);
    setImportResult(null);
    try {
      const result = await api.importWorldview(projectId, importText, genre);
      if (gen !== generationRef.current) return;
      if (capturedEditRev !== editRevisionRef.current) return; // P0-1: user edited
      updateData({
        characters: result.characters || [],
        geography: result.geography || [],
        factions: result.factions || [],
        power_system: result.power_system || [],
        history: result.history || [],
        conflicts: result.conflicts || [],
        special_settings: result.special_settings || [],
        raw_text: importText, // P0-4: always use latest importText
        source: mode === "hybrid" ? "hybrid" : "imported",
      });
      setSource(mode === "hybrid" ? "hybrid" : "imported");
      setImportResult({ count: result.element_count, done: true });
    } catch {
      if (gen !== generationRef.current) return;
      setStatusMessage("导入解析失败，请检查文档内容后重试");
    } finally {
      if (gen !== generationRef.current) return;
      setImporting(false);
    }
  }

  // ── P0-1, P0-2, P0-5: Save with full race-condition protection ──
  async function handleSave() {
    const gen = generationRef.current;
    const capturedEditRev = editRevisionRef.current;
    // Capture complete snapshot at request start
    const snapshot: SaveSnapshot = {
      userId,
      projectId,
      data: dataRef.current,
      importText: importTextRef.current,
      mode: modeRef.current,
      source: sourceRef.current,
    };
    const capturedKey = snapshotKey(snapshot);

    setLoading(true);
    setStatusMessage("正在保存到服务器…");
    setSaveError(false);
    setCopyFailed(false);

    try {
      // P0-5: always use latest importText as raw_text in payload, even if empty
      const payload: WorldviewData = {
        ...snapshot.data,
        source: snapshot.source,
        raw_text: snapshot.importText,
      };
      await api.setWorldview(snapshot.projectId, payload);

      // P0-3: Scope guard — if scope changed, don't touch ANY state
      if (gen !== generationRef.current) return;

      // P0-1: Check if new edits were made during save
      const currentKey = snapshotKey({
        data: dataRef.current,
        importText: importTextRef.current,
        mode: modeRef.current,
        source: sourceRef.current,
      });

      if (currentKey === capturedKey && snapshot.userId === userId && snapshot.projectId === projectId) {
        // P0-1: No new edits — don't re-GET, use snapshot as server-saved baseline
        skipAutosaveRef.current = true;
        setDirty(false);
        setSaveError(false);
        setSaved(true); // P1-2: allow "进入下一步" after first save
        setStatusMessage("保存成功");

        // Clear draft for the PRECISE scope that was saved
        const clearResult = clearDraft(snapshot.userId, snapshot.projectId);
        if (!clearResult.success) {
          // P1-5: persistent visible warning + localSaveError
          setLocalSaveError(true);
          setStatusMessage("服务器已保存，但本机旧草稿未清除，请勿恢复旧草稿/可重试清理");
        } else {
          // P1-5: clear stale local errors on full success
          setLocalSaveError(false);
          setCopyFailed(false);
          setSaveError(false);
        }
      } else {
        // P0-1: New edits during save — preserve them
        setDirty(true);
        setSaveError(false);
        setSaved(true); // P1-2: POST succeeded — allow "进入下一步"
        setStatusMessage("服务器已保存较早版本，当前新修改仍未提交/已保存在本机");

        // Save current (newer) state as draft so it's not lost
        const draftResult = saveDraft(userId, projectId, {
          data: dataRef.current,
          importText: importTextRef.current,
          mode: modeRef.current,
          source: sourceRef.current,
          savedAt: Date.now(),
          schemaVersion: 1,
        });
        if (!draftResult.success) {
          setLocalSaveError(true);
          setStatusMessage("服务器已保存较早版本，但本机草稿保存失败：" + draftResult.message);
        } else {
          setLocalSaveError(false); // P1-5: draft saved OK
        }
      }
    } catch (e) {
      if (gen !== generationRef.current) return;

      // P0-2: Server save failed — save draft FIRST, then set text based on result
      const draftResult = saveDraft(userId, projectId, {
        data: dataRef.current,
        importText: importTextRef.current,
        mode: modeRef.current,
        source: sourceRef.current,
        savedAt: Date.now(),
        schemaVersion: 1,
      });

      const isAuthError = e instanceof Error && e.message.includes("登录已过期");

      if (draftResult.success) {
        // P1-6: Include local safety result in auth error message
        const msg = isAuthError
          ? "登录已过期；当前内容已保存为本机草稿"
          : "服务器保存失败，当前内容已保存为本机草稿";
        setStatusMessage(msg);
        setSaveError(true);
        setLocalSaveError(false);
      } else {
        // P1-6: Include local safety result in auth error message
        const msg = isAuthError
          ? "登录已过期；服务器和本机草稿均保存失败，内容仅保留在当前页面，请立即复制"
          : "服务器和本机草稿均保存失败，内容仅保留在当前页面，请立即复制";
        setStatusMessage(msg);
        setSaveError(true);
        setLocalSaveError(true);
      }
    } finally {
      if (gen === generationRef.current) setLoading(false);
    }
  }

  // ── P0-4, P0-5: Mode switching bumps edit revision ──
  function switchMode(newMode: EditorMode) {
    // P1-2: clicking the current mode must not create a false dirty
    if (newMode === mode) return;
    if (newMode === "manual" && mode !== "manual" && importText.trim()) {
      updateData({ ...data, raw_text: importText });
    } else {
      // P1-2: actual mode change is a user edit — mark dirty + bump revision
      setDirty(true);
      bumpEditRevision();
    }
    setMode(newMode);
    if (newMode === "manual") setSource("manual");
    else if (newMode === "import" && !importResult?.done) setSource("imported");
    else if (newMode === "hybrid") setSource("hybrid");
  }

  // ── P1-6: Back / next protection with focus management ──
  function handleBackProtected() {
    if (dirty) {
      setShowUnsavedConfirm("back");
    } else {
      onBack();
    }
  }

  function handleCompleteProtected() {
    if (dirty) {
      setShowUnsavedConfirm("next");
    } else {
      onComplete();
    }
  }

  function closeUnsavedConfirm() {
    setShowUnsavedConfirm(null);
    setLeaveSaveFailed(false);
    unsavedFocusCapturedRef.current = false;
    setTimeout(() => lastFocusedRef.current?.focus(), 0);
  }

  // P0-1: Check draft save result before leaving
  function confirmLeave() {
    const action = showUnsavedConfirm;
    const result = saveDraft(userId, projectId, {
      data: dataRef.current,
      importText: importTextRef.current,
      mode: modeRef.current,
      source: sourceRef.current,
      savedAt: Date.now(),
      schemaVersion: 1,
    });
    if (!result.success) {
      // Block leaving — draft save failed
      setLeaveSaveFailed(true);
      setStatusMessage("未保存到本机：" + result.message);
      setLocalSaveError(true);
      return;
    }
    setShowUnsavedConfirm(null);
    setLeaveSaveFailed(false);
    unsavedFocusCapturedRef.current = false;
    if (action === "back") onBack();
    else if (action === "next") onComplete();
  }

  // Force leave after explicit risk acceptance (P0-1)
  function forceLeave() {
    const action = showUnsavedConfirm;
    setShowUnsavedConfirm(null);
    setLeaveSaveFailed(false);
    unsavedFocusCapturedRef.current = false;
    if (action === "back") onBack();
    else if (action === "next") onComplete();
  }

  // ── Recovery handlers ──
  function handleRestoreDraft() {
    if (recovery.kind !== "ok") return;
    const { draft } = recovery;
    // P0-2: Bump edit revision + invalidate load token so a late initial GET
    // cannot overwrite restored data/importText/source or setDirty(false)
    bumpEditRevision();
    loadTokenRef.current++;
    skipAutosaveRef.current = true;
    setData(draft.data);
    setImportText(draft.importText);
    setMode(draft.mode);
    setSource(draft.source);
    setDirty(true);
    setSaved(false);
    setRecovery({ kind: "none" });
    recoveryFocusCapturedRef.current = false;
    setStatusMessage("草稿已恢复");
    setTimeout(() => lastFocusedRef.current?.focus(), 0);
  }

  function handleDiscardDraft() {
    // Only for valid drafts — corrupt drafts require secondary confirmation
    if (recovery.kind !== "ok") return;
    setClearDraftError(""); // clear previous error on retry
    const result = clearDraft(userId, projectId);
    if (!result.success) {
      // P1-5: persistent visible error inside dialog, not just sr-only
      setClearDraftError("本机草稿清除失败：" + result.message + "，可重试或复制内容");
      setStatusMessage("本机草稿清除失败：" + result.message + "，可重试或复制内容");
      return;
    }
    // P1-5: clear stale local errors on successful discard
    setClearDraftError("");
    setLocalSaveError(false);
    setCopyFailed(false);
    setSaveError(false);
    setRecovery({ kind: "none" });
    recoveryFocusCapturedRef.current = false;
    setStatusMessage("");
    setTimeout(() => lastFocusedRef.current?.focus(), 0);
  }

  // P0-5: Corrupt draft — secondary confirmation required
  function handleConfirmDiscardCorrupt() {
    setConfirmDiscardCorrupt(true);
  }

  function handleActuallyDiscardCorrupt() {
    setClearDraftError(""); // clear previous error on retry
    const result = clearDraft(userId, projectId);
    if (!result.success) {
      // P1-5: go back to first dialog, show persistent visible error
      setConfirmDiscardCorrupt(false);
      setClearDraftError("本机草稿清除失败：" + result.message + "，可重试或复制内容");
      setStatusMessage("本机草稿清除失败：" + result.message + "，可重试或复制内容");
      return;
    }
    // P1-5: clear stale local errors on successful discard
    setClearDraftError("");
    setLocalSaveError(false);
    setCopyFailed(false);
    setSaveError(false);
    setConfirmDiscardCorrupt(false);
    setRecovery({ kind: "none" });
    recoveryFocusCapturedRef.current = false;
    setStatusMessage("");
    setTimeout(() => lastFocusedRef.current?.focus(), 0);
  }

  function cancelDiscardCorrupt() {
    // P1: Cancel returns to first dialog, focus safe button
    setConfirmDiscardCorrupt(false);
    setTimeout(() => {
      const firstBtn = recoveryDialogRef.current?.querySelector<HTMLElement>("button");
      firstBtn?.focus();
    }, 0);
  }

  // P1-3: Exit for storage-error recovery — allows editing without local draft
  function handleContinueWithoutDraft() {
    setRecovery({ kind: "none" });
    setLocalSaveError(true);
    setClearDraftError("");
    recoveryFocusCapturedRef.current = false;
    setStatusMessage("本地存储不可用，草稿功能已禁用，可继续编辑");
    setTimeout(() => lastFocusedRef.current?.focus(), 0);
  }

  // P0-3: Overlay/Escape does NOT close recovery dialog — user must choose
  function rejectRecoveryClose() {
    setStatusMessage(recovery.kind === "error" ? "请选择继续编辑" : "请选择恢复或丢弃");
    // Keep focus in dialog
    setTimeout(() => {
      const firstBtn = recoveryDialogRef.current?.querySelector<HTMLElement>("button");
      firstBtn?.focus();
    }, 0);
  }

  // ── P0-2: handleCopyContent checks real results ──
  // Round 5: copy complete draft/snapshot including importText — never lose
  // the latest import text when falling back to copy.
  async function handleCopyContent() {
    const content = recovery.kind === "corrupt"
      ? recovery.raw || getDraftRaw(userId, projectId)
      : recovery.kind === "ok"
        ? JSON.stringify(
            {
              data: recovery.draft.data,
              importText: recovery.draft.importText,
              mode: recovery.draft.mode,
              source: recovery.draft.source,
              savedAt: recovery.draft.savedAt,
              schemaVersion: recovery.draft.schemaVersion,
            },
            null,
            2,
          )
        : JSON.stringify(
            {
              data: dataRef.current,
              importText: importTextRef.current,
              mode: modeRef.current,
              source: sourceRef.current,
            },
            null,
            2,
          );

    setCopyContent(content);

    // Try navigator.clipboard first
    let clipboardSuccess = false;
    try {
      await navigator.clipboard.writeText(content);
      clipboardSuccess = true;
    } catch {
      // Fall through to execCommand
    }

    if (!clipboardSuccess) {
      // Try execCommand — check its real return value
      let execSuccess = false;
    try {
        const ta = document.createElement("textarea");
        ta.value = content;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        ta.style.top = "0";
        document.body.appendChild(ta);
        ta.select();
        execSuccess = document.execCommand("copy");
        document.body.removeChild(ta);
      } catch {
        execSuccess = false;
      }

      if (!execSuccess) {
        // P0-2: Both methods failed — show manual copy UI
        setCopyFailed(true);
        setStatusMessage("复制失败，请手动复制以下内容");
        return;
      }
    }

    setCopyFailed(false);
    setCopyContent(""); // P1-4: clear stale copy content on success
    setStatusMessage("内容已复制到剪贴板");
  }

  // ── Dialog keydown handlers (P1-6) ──
  function handleRecoveryKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      if (confirmDiscardCorrupt) {
        // P1: Secondary confirmation — cancel back to first dialog
        cancelDiscardCorrupt();
      } else {
        // P0-3: First dialog — ESC does NOT close, prompt instead
        rejectRecoveryClose();
      }
      return;
    }
    if (e.key === "Tab" && recoveryDialogRef.current) {
      const focusable = recoveryDialogRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  function handleUnsavedKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      closeUnsavedConfirm();
      return;
    }
    if (e.key === "Tab" && unsavedDialogRef.current) {
      const focusable = unsavedDialogRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  // CRUD helpers — use updateData wrapper to track dirty state + bump revision
  function addCharacter() { updateData({ ...data, characters: [...data.characters, { name: "", personality: "", background: "", motivation: "", ability: "", relations: [] }] }); }
  function updateCharacter(i: number, field: keyof Character, value: string) { const c = [...data.characters]; c[i] = { ...c[i], [field]: value }; updateData({ ...data, characters: c }); }
  function removeCharacter(i: number) { updateData({ ...data, characters: data.characters.filter((_, idx) => idx !== i) }); }
  function addGeography() { updateData({ ...data, geography: [...data.geography, { name: "", description: "", significance: "" }] }); }
  function updateGeography(i: number, field: keyof Geography, value: string) { const g = [...data.geography]; g[i] = { ...g[i], [field]: value }; updateData({ ...data, geography: g }); }
  function removeGeography(i: number) { updateData({ ...data, geography: data.geography.filter((_, idx) => idx !== i) }); }
  function addFaction() { updateData({ ...data, factions: [...data.factions, { name: "", stance: "", power_level: "", relations: [] }] }); }
  function updateFaction(i: number, field: keyof Faction, value: string) { const f = [...data.factions]; f[i] = { ...f[i], [field]: value }; updateData({ ...data, factions: f }); }
  function removeFaction(i: number) { updateData({ ...data, factions: data.factions.filter((_, idx) => idx !== i) }); }
  function addPowerSystem() { updateData({ ...data, power_system: [...data.power_system, { name: "", levels: "", rules: "", limitations: "" }] }); }
  function updatePowerSystem(i: number, field: keyof PowerSystem, value: string) { const p = [...data.power_system]; p[i] = { ...p[i], [field]: value }; updateData({ ...data, power_system: p }); }
  function removePowerSystem(i: number) { updateData({ ...data, power_system: data.power_system.filter((_, idx) => idx !== i) }); }
  function addHistory() { updateData({ ...data, history: [...data.history, { event: "", time: "", description: "", impact: "" }] }); }
  function updateHistory(i: number, field: keyof HistoryEvent, value: string) { const h = [...data.history]; h[i] = { ...h[i], [field]: value }; updateData({ ...data, history: h }); }
  function removeHistory(i: number) { updateData({ ...data, history: data.history.filter((_, idx) => idx !== i) }); }
  function addConflict() { updateData({ ...data, conflicts: [...data.conflicts, { name: "", type: "", parties: "", stakes: "", resolution_hint: "" }] }); }
  function updateConflict(i: number, field: keyof Conflict, value: string) { const c = [...data.conflicts]; c[i] = { ...c[i], [field]: value }; updateData({ ...data, conflicts: c }); }
  function removeConflict(i: number) { updateData({ ...data, conflicts: data.conflicts.filter((_, idx) => idx !== i) }); }
  function addSpecial() { updateData({ ...data, special_settings: [...data.special_settings, { name: "", description: "", rules: "" }] }); }
  function updateSpecial(i: number, field: keyof SpecialSetting, value: string) { const s = [...data.special_settings]; s[i] = { ...s[i], [field]: value }; updateData({ ...data, special_settings: s }); }
  function removeSpecial(i: number) { updateData({ ...data, special_settings: data.special_settings.filter((_, idx) => idx !== i) }); }

  const MODES = [
    { key: "manual" as const, label: "手动创建", desc: "逐项填写世界观模板" },
    { key: "import" as const, label: "导入文档", desc: "上传/粘贴文本，AI 自动提取" },
    { key: "hybrid" as const, label: "混合模式", desc: "导入后可手动追加修改" },
  ];

  const isReadOnly = mode === "import" && importResult?.done;
  const showEditor = mode === "manual" || importResult?.done === true;

  return (
    <div>
      {/* Single polite live region for status announcements (P1-7) */}
      <div role="status" aria-live="polite" className="sr-only">{statusMessage}</div>

      <button className="btn-back" onClick={handleBackProtected}>← 返回项目详情</button>

      {/* P1-4: Copy failure — show manual copy textarea (global area only when no dialog active) */}
      {copyFailed && recovery.kind === "none" && !showUnsavedConfirm && (
        <div className="card" style={{ background: "var(--red-light)", borderColor: "#f5c6ce", borderLeftWidth: "3px", borderLeftColor: "var(--red)" }}>
          <p style={{ color: "var(--red)", fontSize: "13px", marginBottom: "0.5rem" }}>复制失败，请手动复制以下内容：</p>
          <textarea
            readOnly
            aria-label="手动复制内容"
            ref={copyTextareaRef}
            value={copyContent}
            style={{ width: "100%", minHeight: "120px", fontFamily: "var(--font-mono)", fontSize: "12px", resize: "vertical", border: "1px solid var(--border)", borderRadius: "var(--r-sm)", padding: "0.5rem" }}
            onFocus={(e) => e.target.select()}
          />
        </div>
      )}

      {/* P0-1: Local save error banner with copy button */}
      {localSaveError && !saveError && (
        <div className="card" style={{ background: "var(--red-light)", borderColor: "#f5c6ce", borderLeftWidth: "3px", borderLeftColor: "var(--red)" }}>
          <div style={{ display: "flex", gap: "0.625rem", alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontSize: "13px", color: "var(--red)", flex: "1" }}>{statusMessage}</span>
            <button className="btn btn-sm" onClick={handleCopyContent}>复制当前内容</button>
          </div>
        </div>
      )}

      {/* Save error banner with copy + retry */}
      {saveError && (
        <div className="card" style={{ background: "var(--red-light)", borderColor: "#f5c6ce", borderLeftWidth: "3px", borderLeftColor: "var(--red)" }}>
          <div style={{ display: "flex", gap: "0.625rem", alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontSize: "13px", color: "var(--red)", flex: "1" }}>{statusMessage}</span>
            <button className="btn btn-sm" onClick={handleCopyContent}>复制当前内容</button>
            <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={loading}>重试保存</button>
          </div>
        </div>
      )}

      {/* ── P0-5: Recovery dialog — safe corrupt handling ── */}
      {recovery.kind !== "none" && !confirmDiscardCorrupt && (
        <div
          className="draft-overlay"
          onClick={(e) => { if (e.target === e.currentTarget) rejectRecoveryClose(); }}
          onKeyDown={handleRecoveryKeyDown}
        >
          <div
            ref={recoveryDialogRef}
            className="card draft-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="recovery-title"
          >
            <div className="wv-section-title" id="recovery-title">
              {recovery.kind === "corrupt" ? "草稿已损坏" : recovery.kind === "error" ? "存储不可用" : "发现未保存的草稿"}
            </div>
            {recovery.kind === "ok" && (
              <p className="form-hint" style={{ marginBottom: "0.875rem", fontSize: "13px" }}>
                上次编辑的世界观内容已自动保存为草稿（{new Date(recovery.draft.savedAt).toLocaleString("zh-CN")}）。
                你可以恢复草稿继续编辑，或丢弃后重新开始。
              </p>
            )}
            {recovery.kind === "corrupt" && (
              <p className="form-hint" style={{ marginBottom: "0.875rem", fontSize: "13px", color: "var(--red)" }}>
                草稿数据已损坏，无法恢复。你可以先复制原始数据以备份，然后确认丢弃后重新开始。
              </p>
            )}
            {recovery.kind === "error" && (
              <p className="form-hint" style={{ marginBottom: "0.875rem", fontSize: "13px", color: "var(--red)" }}>
                {recovery.message}
              </p>
            )}
            {/* P1-5: persistent visible error inside dialog when clearDraft fails */}
            {clearDraftError && (
              <p style={{ color: "var(--red)", fontSize: "13px", marginBottom: "0.5rem", fontWeight: 600 }}>
                {clearDraftError}
              </p>
            )}
            {/* P1-4: manual copy fallback inside active dialog */}
            {copyFailed && copyContent && (
              <div style={{ marginTop: "0.5rem", marginBottom: "0.5rem" }}>
                <p style={{ color: "var(--red)", fontSize: "13px", marginBottom: "0.25rem" }}>复制失败，请手动复制以下内容：</p>
                <textarea
                  readOnly
                  aria-label="手动复制内容"
                  ref={copyTextareaRef}
                  value={copyContent}
                  style={{ width: "100%", minHeight: "120px", fontFamily: "var(--font-mono)", fontSize: "12px", resize: "vertical", border: "1px solid var(--border)", borderRadius: "var(--r-sm)", padding: "0.5rem" }}
                  onFocus={(e) => e.target.select()}
                />
              </div>
            )}
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              {recovery.kind === "ok" && (
                <button className="btn btn-primary" onClick={handleRestoreDraft}>恢复草稿</button>
              )}
              <button className="btn" onClick={handleCopyContent}>复制内容</button>
              {recovery.kind === "corrupt" ? (
                <button
                  className="btn btn-danger"
                  onClick={handleConfirmDiscardCorrupt}
                  style={{ marginLeft: "auto" }}
                >
                  确认丢弃
                </button>
              ) : recovery.kind === "ok" ? (
                <button
                  className="btn btn-danger"
                  onClick={handleDiscardDraft}
                  style={{ marginLeft: "auto" }}
                >
                  丢弃草稿
                </button>
              ) : recovery.kind === "error" ? (
                <button
                  className="btn btn-primary"
                  onClick={handleContinueWithoutDraft}
                  style={{ marginLeft: "auto" }}
                >
                  继续编辑（无法使用本机草稿）
                </button>
              ) : null}
            </div>
          </div>
        </div>
      )}

      {/* P0-5: Secondary confirmation for corrupt draft discard */}
      {recovery.kind === "corrupt" && confirmDiscardCorrupt && (
        <div
          className="draft-overlay"
          onClick={(e) => { if (e.target === e.currentTarget) cancelDiscardCorrupt(); }}
          onKeyDown={handleRecoveryKeyDown}
        >
          <div
            ref={recoveryDialogRef}
            className="card draft-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="corrupt-confirm-title"
          >
            <div className="wv-section-title" id="corrupt-confirm-title" style={{ color: "var(--red)" }}>
              确认永久丢弃
            </div>
            <p className="form-hint" style={{ marginBottom: "0.875rem", fontSize: "13px" }}>
              确定要永久丢弃损坏的草稿吗？此操作不可撤销。
              建议先复制原始数据以备份。
            </p>
            {/* P1-5: persistent visible error inside dialog when clearDraft fails */}
            {clearDraftError && (
              <p style={{ color: "var(--red)", fontSize: "13px", marginBottom: "0.5rem", fontWeight: 600 }}>
                {clearDraftError}
              </p>
            )}
            {/* P1-4: manual copy fallback inside active dialog */}
            {copyFailed && copyContent && (
              <div style={{ marginTop: "0.5rem", marginBottom: "0.5rem" }}>
                <p style={{ color: "var(--red)", fontSize: "13px", marginBottom: "0.25rem" }}>复制失败，请手动复制以下内容：</p>
                <textarea
                  readOnly
                  aria-label="手动复制内容"
                  ref={copyTextareaRef}
                  value={copyContent}
                  style={{ width: "100%", minHeight: "120px", fontFamily: "var(--font-mono)", fontSize: "12px", resize: "vertical", border: "1px solid var(--border)", borderRadius: "var(--r-sm)", padding: "0.5rem" }}
                  onFocus={(e) => e.target.select()}
                />
              </div>
            )}
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button className="btn" onClick={handleCopyContent}>复制内容</button>
              <button className="btn" onClick={cancelDiscardCorrupt}>取消</button>
              <button
                className="btn btn-danger"
                onClick={handleActuallyDiscardCorrupt}
                style={{ marginLeft: "auto" }}
              >
                确认丢弃
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── P1-6: Unsaved changes confirmation ── */}
      {showUnsavedConfirm && (
        <div
          className="draft-overlay"
          onClick={(e) => { if (e.target === e.currentTarget) closeUnsavedConfirm(); }}
          onKeyDown={handleUnsavedKeyDown}
        >
          <div className="card draft-dialog" ref={unsavedDialogRef} role="dialog" aria-modal="true" aria-labelledby="unsaved-title">
            <div className="wv-section-title" id="unsaved-title">未保存的修改</div>
            {!leaveSaveFailed ? (
              <>
                <p className="form-hint" style={{ marginBottom: "0.875rem", fontSize: "13px" }}>
                  你有未保存到服务器的修改。确认离开前将尝试保存本机草稿。
                  确定要{showUnsavedConfirm === "back" ? "返回" : "进入下一步"}吗？
                </p>
                <div style={{ display: "flex", gap: "0.5rem" }}>
                  <button className="btn btn-primary" onClick={confirmLeave}>确认离开</button>
                  <button className="btn" onClick={closeUnsavedConfirm}>继续编辑</button>
                </div>
              </>
            ) : (
              <>
                <p className="form-hint" style={{ marginBottom: "0.875rem", fontSize: "13px", color: "var(--red)" }}>
                  草稿保存失败，离开可能导致内容丢失。请先复制内容，或承担风险强制离开。
                </p>
                <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                  <button className="btn" onClick={handleCopyContent}>复制当前内容</button>
                  <button className="btn" onClick={closeUnsavedConfirm}>继续编辑</button>
                  <button className="btn btn-danger" onClick={forceLeave} style={{ marginLeft: "auto" }}>承担风险离开</button>
                </div>
              </>
            )}
            {/* P1-4: manual copy fallback inside active dialog */}
            {copyFailed && copyContent && (
              <div style={{ marginTop: "0.5rem" }}>
                <p style={{ color: "var(--red)", fontSize: "13px", marginBottom: "0.25rem" }}>复制失败，请手动复制以下内容：</p>
                <textarea
                  readOnly
                  aria-label="手动复制内容"
                  ref={copyTextareaRef}
                  value={copyContent}
                  style={{ width: "100%", minHeight: "120px", fontFamily: "var(--font-mono)", fontSize: "12px", resize: "vertical", border: "1px solid var(--border)", borderRadius: "var(--r-sm)", padding: "0.5rem" }}
                  onFocus={(e) => e.target.select()}
                />
              </div>
            )}
          </div>
        </div>
      )}

      {/* Mode selector */}
      <div className="card">
        <div className="wv-section-title">世界观创建方式</div>
        <div className="wv-mode-row">
          {MODES.map((m) => (
            <button
              key={m.key}
              className="btn wv-mode-btn"
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
            粘贴或上传包含世界观设定的文档（支持 .txt / .md / .doc / .docx），AI 将自动提取角色、地理、势力、体系等结构化要素。
          </p>
          <textarea
            className="form-textarea"
            style={{ minHeight: "140px", fontFamily: "var(--font-mono)", fontSize: "12px" }}
            placeholder={"在此粘贴世界观文档内容...\n\n例如：\n林远，男，18岁，性格坚韧...\n苍澜大陆分为东南西北四大区域...\n修炼境界：聚气境、筑基境、金丹境..."}
            value={importText}
            onChange={(e) => { setImportText(e.target.value); setDirty(true); bumpEditRevision(); }}
            disabled={importing}
          />
          <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.625rem", alignItems: "center" }}>
            <input ref={fileInputRef} type="file" accept=".txt,.md,.markdown,.doc,.docx" onChange={handleFileUpload} style={{ display: "none" }} />
            <button className="btn" onClick={() => fileInputRef.current?.click()} disabled={importing}>
              {importing ? "解析文件中..." : "上传文件"}
            </button>
            <button className="btn btn-primary" onClick={handleImport} disabled={importing || !importText.trim()}>
              {importing ? "AI 解析中..." : "开始导入解析"}
            </button>
            {importResult?.done && (
              <span className="tag tag-gold" style={{ fontWeight: 600 }}>已提取 {importResult.count} 个要素</span>
            )}
          </div>
          {mode === "hybrid" && importResult?.done && (
            <div style={{ marginTop: "0.625rem", padding: "0.5rem 0.75rem", background: "var(--gold-light)", borderRadius: "var(--r-md)", fontSize: "12px", color: "var(--gold-dark)", borderLeft: "3px solid var(--gold)" }}>
              混合模式已激活 — 下方表单已填充提取的要素，你可以自由编辑、添加或删除任意内容
            </div>
          )}
          {importResult?.done && !saved && (
            <div style={{ marginTop: "0.625rem", padding: "0.5rem 0.75rem", background: "#fef9e7", borderRadius: "var(--r-md)", fontSize: "13px", color: "#7d6608", borderLeft: "3px solid #f39c12" }}>
              ⚠️ 世界观已提取但尚未保存。请点击下方「保存世界观」按钮保存后，再进入下一步生成大纲。
            </div>
          )}
        </div>
      )}

      {/* Worldview editor */}
      {showEditor && (
        <>
          {/* Characters */}
          <div className="card">
            <div className="wv-section-title">角色 ({data.characters.length})</div>
            {data.characters.map((c, i) => (
              <div key={i} className="wv-entry">
                <input className="form-input" placeholder="姓名" value={c.name} onChange={(e) => updateCharacter(i, "name", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="性格" value={c.personality} onChange={(e) => updateCharacter(i, "personality", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="背景" value={c.background} onChange={(e) => updateCharacter(i, "background", e.target.value)} readOnly={isReadOnly} />
                <div style={{ display: "flex", gap: "0.375rem" }}>
                  <input className="form-input" placeholder="动机" value={c.motivation} onChange={(e) => updateCharacter(i, "motivation", e.target.value)} readOnly={isReadOnly} />
                  {!isReadOnly && <button className="btn btn-ghost btn-sm" aria-label={`删除角色 ${i + 1}${c.name ? `：${c.name}` : ""}`} onClick={() => removeCharacter(i)} style={{ color: "var(--red)" }}>✕</button>}
                </div>
                <input className="form-input" placeholder="能力/特长" value={c.ability} onChange={(e) => updateCharacter(i, "ability", e.target.value)} readOnly={isReadOnly} style={{ gridColumn: "1 / -1" }} />
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addCharacter}>+ 添加角色</button>}
          </div>

          {/* Geography */}
          <div className="card">
            <div className="wv-section-title">地理设定 ({data.geography.length})</div>
            {data.geography.map((g, i) => (
              <div key={i} className="wv-entry">
                <input className="form-input" placeholder="地名" value={g.name} onChange={(e) => updateGeography(i, "name", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="描述" value={g.description} onChange={(e) => updateGeography(i, "description", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="重要性" value={g.significance} onChange={(e) => updateGeography(i, "significance", e.target.value)} readOnly={isReadOnly} />
                {!isReadOnly && <button className="btn btn-ghost btn-sm" aria-label={`删除地理 ${i + 1}${g.name ? `：${g.name}` : ""}`} onClick={() => removeGeography(i)} style={{ color: "var(--red)" }}>✕</button>}
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addGeography}>+ 添加地点</button>}
          </div>

          {/* Factions */}
          <div className="card">
            <div className="wv-section-title">势力组织 ({data.factions.length})</div>
            {data.factions.map((f, i) => (
              <div key={i} className="wv-entry">
                <input className="form-input" placeholder="势力名称" value={f.name} onChange={(e) => updateFaction(i, "name", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="立场" value={f.stance} onChange={(e) => updateFaction(i, "stance", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="实力等级" value={f.power_level} onChange={(e) => updateFaction(i, "power_level", e.target.value)} readOnly={isReadOnly} />
                {!isReadOnly && <button className="btn btn-ghost btn-sm" aria-label={`删除势力 ${i + 1}${f.name ? `：${f.name}` : ""}`} onClick={() => removeFaction(i)} style={{ color: "var(--red)" }}>✕</button>}
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addFaction}>+ 添加势力</button>}
          </div>

          {/* Power System */}
          <div className="card">
            <div className="wv-section-title">力量体系 ({data.power_system.length})</div>
            {data.power_system.map((ps, i) => (
              <div key={i} className="wv-entry">
                <input className="form-input" placeholder="体系名称" value={ps.name} onChange={(e) => updatePowerSystem(i, "name", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="等级划分" value={ps.levels} onChange={(e) => updatePowerSystem(i, "levels", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="规则" value={ps.rules} onChange={(e) => updatePowerSystem(i, "rules", e.target.value)} readOnly={isReadOnly} />
                <div style={{ display: "flex", gap: "0.375rem" }}>
                  <input className="form-input" placeholder="限制" value={ps.limitations} onChange={(e) => updatePowerSystem(i, "limitations", e.target.value)} readOnly={isReadOnly} />
                  {!isReadOnly && <button className="btn btn-ghost btn-sm" aria-label={`删除体系 ${i + 1}${ps.name ? `：${ps.name}` : ""}`} onClick={() => removePowerSystem(i)} style={{ color: "var(--red)" }}>✕</button>}
                </div>
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addPowerSystem}>+ 添加体系</button>}
          </div>

          {/* History */}
          <div className="card">
            <div className="wv-section-title">历史事件 ({data.history.length})</div>
            {data.history.map((h, i) => (
              <div key={i} className="wv-entry">
                <input className="form-input" placeholder="事件名称" value={h.event} onChange={(e) => updateHistory(i, "event", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="时间" value={h.time} onChange={(e) => updateHistory(i, "time", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="描述" value={h.description} onChange={(e) => updateHistory(i, "description", e.target.value)} readOnly={isReadOnly} />
                <div style={{ display: "flex", gap: "0.375rem" }}>
                  <input className="form-input" placeholder="影响" value={h.impact} onChange={(e) => updateHistory(i, "impact", e.target.value)} readOnly={isReadOnly} />
                  {!isReadOnly && <button className="btn btn-ghost btn-sm" aria-label={`删除事件 ${i + 1}${h.event ? `：${h.event}` : ""}`} onClick={() => removeHistory(i)} style={{ color: "var(--red)" }}>✕</button>}
                </div>
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addHistory}>+ 添加事件</button>}
          </div>

          {/* Conflicts */}
          <div className="card">
            <div className="wv-section-title">核心矛盾 ({data.conflicts.length})</div>
            {data.conflicts.map((c, i) => (
              <div key={i} className="wv-entry">
                <input className="form-input" placeholder="矛盾名称" value={c.name} onChange={(e) => updateConflict(i, "name", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="类型" value={c.type} onChange={(e) => updateConflict(i, "type", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="涉及方" value={c.parties} onChange={(e) => updateConflict(i, "parties", e.target.value)} readOnly={isReadOnly} />
                <div style={{ display: "flex", gap: "0.375rem" }}>
                  <input className="form-input" placeholder="利害关系" value={c.stakes} onChange={(e) => updateConflict(i, "stakes", e.target.value)} readOnly={isReadOnly} />
                  {!isReadOnly && <button className="btn btn-ghost btn-sm" aria-label={`删除矛盾 ${i + 1}${c.name ? `：${c.name}` : ""}`} onClick={() => removeConflict(i)} style={{ color: "var(--red)" }}>✕</button>}
                </div>
                <input className="form-input" placeholder="解决线索" value={c.resolution_hint} onChange={(e) => updateConflict(i, "resolution_hint", e.target.value)} readOnly={isReadOnly} style={{ gridColumn: "1 / -1" }} />
              </div>
            ))}
            {!isReadOnly && <button className="wv-add-btn" onClick={addConflict}>+ 添加矛盾</button>}
          </div>

          {/* Special Settings */}
          <div className="card">
            <div className="wv-section-title">特殊设定 ({data.special_settings.length})</div>
            {data.special_settings.map((ss, i) => (
              <div key={i} className="wv-entry">
                <input className="form-input" placeholder="设定名称" value={ss.name} onChange={(e) => updateSpecial(i, "name", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="描述" value={ss.description} onChange={(e) => updateSpecial(i, "description", e.target.value)} readOnly={isReadOnly} />
                <input className="form-input" placeholder="规则" value={ss.rules} onChange={(e) => updateSpecial(i, "rules", e.target.value)} readOnly={isReadOnly} />
                {!isReadOnly && <button className="btn btn-ghost btn-sm" aria-label={`删除设定 ${i + 1}${ss.name ? `：${ss.name}` : ""}`} onClick={() => removeSpecial(i)} style={{ color: "var(--red)" }}>✕</button>}
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

          {/* Actions — bottom navigation buttons */}
          <div className="wv-action-row">
            <button className="btn btn-primary btn-lg" onClick={handleSave} disabled={loading}>
              {loading ? "保存中..." : "保存世界观"}
            </button>
            {(hasWorldview || saved) && (
              <button className="btn btn-danger btn-lg" onClick={handleCompleteProtected}>
                进入下一步 →
              </button>
            )}
          </div>
        </>
      )}

      {mode === "manual" && !hasWorldview && (
        <div className="card" style={{ background: "var(--gold-light)", borderColor: "var(--gold-border)", borderLeftWidth: "3px", borderLeftColor: "var(--gold)" }}>
          <p style={{ fontSize: "13px", color: "var(--gold-dark)", lineHeight: 1.7 }}>
            填写世界观架构。系统会自动解析为结构化要素并分配优先级（核心/重要/次要/背景），
            然后根据渐进式揭示策略安排各章节的信息展开节奏。
          </p>
        </div>
      )}
    </div>
  );
}
