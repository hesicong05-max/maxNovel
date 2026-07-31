// localStorage-based draft storage for WorldviewEditor.
// User+project isolated, no backend dependencies.
// Works with old production backend (no maintenance API or freeze status).
//
// P0-1: saveDraft / clearDraft return explicit success/failure results.
//       No silent exception swallowing — callers can react to failures.
// P0-4: isValidDraft validates ALL required arrays + element-level structure.
//       loadDraft distinguishes "corrupt payload" from "storage unavailable".
// P0-5: raw_text/source optional-but-type-checked semantics.

import type { WorldviewData, WorldviewSource } from "@/types";

export type EditorMode = "manual" | "import" | "hybrid";

export interface WorldviewDraft {
  data: WorldviewData;
  importText: string;
  mode: EditorMode;
  source: WorldviewSource;
  savedAt: number;
  schemaVersion: 1;
}

const SCHEMA_VERSION = 1 as const;

const REQUIRED_ARRAY_FIELDS = [
  "characters",
  "geography",
  "factions",
  "power_system",
  "history",
  "conflicts",
  "special_settings",
] as const;

const VALID_MODES: EditorMode[] = ["manual", "import", "hybrid"];

const VALID_SOURCES: WorldviewSource[] = ["manual", "imported", "hybrid"];

/** Exported for testing — the exact localStorage key for a scope. */
export function draftKey(userId: string, projectId: string): string {
  return `wv-draft:${userId}:${projectId}`;
}

// ── P0-4: Element-level validators ──
// Each validator checks that the item is an object with the correct
// field types required for safe rendering and editing.

function isStr(v: unknown): v is string {
  return typeof v === "string";
}

function isValidCharacter(c: unknown): boolean {
  if (typeof c !== "object" || c === null) return false;
  const ch = c as Record<string, unknown>;
  return (
    isStr(ch.name) &&
    isStr(ch.personality) &&
    isStr(ch.background) &&
    isStr(ch.motivation) &&
    isStr(ch.ability) &&
    Array.isArray(ch.relations)
  );
}

function isValidGeography(g: unknown): boolean {
  if (typeof g !== "object" || g === null) return false;
  const ge = g as Record<string, unknown>;
  return (
    isStr(ge.name) &&
    isStr(ge.description) &&
    isStr(ge.significance)
  );
}

function isValidFaction(f: unknown): boolean {
  if (typeof f !== "object" || f === null) return false;
  const fa = f as Record<string, unknown>;
  return (
    isStr(fa.name) &&
    isStr(fa.stance) &&
    isStr(fa.power_level) &&
    Array.isArray(fa.relations)
  );
}

function isValidPowerSystem(p: unknown): boolean {
  if (typeof p !== "object" || p === null) return false;
  const ps = p as Record<string, unknown>;
  return (
    isStr(ps.name) &&
    isStr(ps.levels) &&
    isStr(ps.rules) &&
    isStr(ps.limitations)
  );
}

function isValidHistoryEvent(h: unknown): boolean {
  if (typeof h !== "object" || h === null) return false;
  const he = h as Record<string, unknown>;
  return (
    isStr(he.event) &&
    isStr(he.time) &&
    isStr(he.description) &&
    isStr(he.impact)
  );
}

function isValidConflict(c: unknown): boolean {
  if (typeof c !== "object" || c === null) return false;
  const co = c as Record<string, unknown>;
  return (
    isStr(co.name) &&
    isStr(co.type) &&
    isStr(co.parties) &&
    isStr(co.stakes) &&
    isStr(co.resolution_hint)
  );
}

function isValidSpecialSetting(s: unknown): boolean {
  if (typeof s !== "object" || s === null) return false;
  const ss = s as Record<string, unknown>;
  return (
    isStr(ss.name) &&
    isStr(ss.description) &&
    isStr(ss.rules)
  );
}

const ELEMENT_VALIDATORS: Record<string, (item: unknown) => boolean> = {
  characters: isValidCharacter,
  geography: isValidGeography,
  factions: isValidFaction,
  power_system: isValidPowerSystem,
  history: isValidHistoryEvent,
  conflicts: isValidConflict,
  special_settings: isValidSpecialSetting,
};

function isValidDraft(obj: unknown): obj is WorldviewDraft {
  if (typeof obj !== "object" || obj === null) return false;
  const d = obj as Record<string, unknown>;

  // data must be an object with ALL required arrays
  if (typeof d.data !== "object" || d.data === null) return false;
  const data = d.data as Record<string, unknown>;
  for (const field of REQUIRED_ARRAY_FIELDS) {
    if (!Array.isArray(data[field])) return false;
  }

  // P0-4: Validate each element's structure within every array
  for (const field of REQUIRED_ARRAY_FIELDS) {
    const validator = ELEMENT_VALIDATORS[field];
    const arr = data[field] as unknown[];
    for (const item of arr) {
      if (!validator(item)) return false;
    }
  }

  // raw_text: optional, but if present must be string or null
  if (
    data.raw_text !== undefined &&
    data.raw_text !== null &&
    typeof data.raw_text !== "string"
  ) {
    return false;
  }

  // data.source: optional, but if present must be valid
  if (
    data.source !== undefined &&
    data.source !== null &&
    !VALID_SOURCES.includes(data.source as WorldviewSource)
  ) {
    return false;
  }

  // importText must be a string
  if (typeof d.importText !== "string") return false;

  // mode must be valid
  if (!VALID_MODES.includes(d.mode as EditorMode)) return false;

  // source (top-level) must be valid
  if (!VALID_SOURCES.includes(d.source as WorldviewSource)) return false;

  // savedAt must be a number
  if (typeof d.savedAt !== "number") return false;

  // schemaVersion must match
  if (d.schemaVersion !== SCHEMA_VERSION) return false;

  return true;
}

// ── Result types (P0-1) ──

export type DraftSaveResult =
  | { success: true }
  | { success: false; error: "quota" | "unavailable"; message: string };

export type DraftClearResult =
  | { success: true }
  | { success: false; error: "unavailable"; message: string };

export type DraftLoadResult =
  | { status: "ok"; draft: WorldviewDraft }
  | { status: "none" }
  | { status: "corrupt"; raw: string }
  | { status: "error"; error: "unavailable"; message: string };

// ── Core operations ──

export function loadDraft(userId: string, projectId: string): DraftLoadResult {
  let raw: string | null;
  try {
    raw = localStorage.getItem(draftKey(userId, projectId));
  } catch {
    // localStorage.getItem threw — storage is unavailable, not corrupt
    return { status: "error", error: "unavailable", message: "本地存储不可用，无法读取草稿" };
  }
  if (raw === null) return { status: "none" };
  try {
    const parsed = JSON.parse(raw);
    if (!isValidDraft(parsed)) return { status: "corrupt", raw };
    return { status: "ok", draft: parsed };
  } catch {
    // JSON.parse failed — stored data is malformed
    return { status: "corrupt", raw };
  }
}

export function saveDraft(
  userId: string,
  projectId: string,
  draft: WorldviewDraft,
): DraftSaveResult {
  try {
    localStorage.setItem(draftKey(userId, projectId), JSON.stringify(draft));
    return { success: true };
  } catch (e) {
    // Distinguish quota exceeded from other storage errors
    if (
      e instanceof DOMException &&
      (e.name === "QuotaExceededError" ||
        e.name === "NS_ERROR_DOM_QUOTA_REACHED" ||
        e.code === 22)
    ) {
      return {
        success: false,
        error: "quota",
        message: "本地存储空间已满，无法保存草稿",
      };
    }
    return {
      success: false,
      error: "unavailable",
      message: "本地存储不可用，无法保存草稿",
    };
  }
}

export function clearDraft(
  userId: string,
  projectId: string,
): DraftClearResult {
  try {
    localStorage.removeItem(draftKey(userId, projectId));
    return { success: true };
  } catch {
    return {
      success: false,
      error: "unavailable",
      message: "本地存储不可用，无法清除草稿",
    };
  }
}

/** Returns the raw string of a corrupt draft for copy-to-clipboard fallback. */
export function getDraftRaw(userId: string, projectId: string): string {
  try {
    return localStorage.getItem(draftKey(userId, projectId)) ?? "";
  } catch {
    return "";
  }
}
