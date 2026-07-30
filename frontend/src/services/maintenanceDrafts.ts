export const DRAFT_SCHEMA_VERSION = 1 as const;
export const DEFAULT_DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000;

const DRAFT_KEY_PREFIX = "novel:draft:v1";
const SHA256_FINGERPRINT = /^[a-f0-9]{64}$/;

export type DraftKind = "worldview" | "outline" | "chapter";

export interface DraftScope {
  userId: string;
  projectId: string;
  kind: DraftKind;
  objectId: string;
}

export interface DraftEnvelope<T> {
  schemaVersion: typeof DRAFT_SCHEMA_VERSION;
  scope: DraftScope;
  savedAt: number;
  expiresAt: number;
  baseFingerprint: string | null;
  payload: T;
}

export type SaveDraftResult<T> =
  | { status: "saved"; draft: DraftEnvelope<T> }
  | { status: "unavailable"; reason: "storage" | "serialization" };

export type LoadDraftResult<T> =
  | { status: "missing" }
  | { status: "available"; draft: DraftEnvelope<T> }
  | { status: "expired"; draft: DraftEnvelope<T> }
  | { status: "corrupt" }
  | { status: "unavailable"; reason: "storage" };

export type ClearDraftResult =
  | { status: "cleared" | "missing" }
  | { status: "unavailable"; reason: "storage" };

export type FingerprintResult =
  | { status: "available"; value: string }
  | { status: "unknown" };

function isNonEmpty(value: string): boolean {
  return value.trim().length > 0;
}

function isDraftScope(value: unknown): value is DraftScope {
  if (!value || typeof value !== "object") return false;
  const scope = value as Partial<DraftScope>;
  return (
    typeof scope.userId === "string" &&
    isNonEmpty(scope.userId) &&
    typeof scope.projectId === "string" &&
    isNonEmpty(scope.projectId) &&
    (scope.kind === "worldview" ||
      scope.kind === "outline" ||
      scope.kind === "chapter") &&
    typeof scope.objectId === "string" &&
    isNonEmpty(scope.objectId)
  );
}

function sameScope(left: DraftScope, right: DraftScope): boolean {
  return (
    left.userId === right.userId &&
    left.projectId === right.projectId &&
    left.kind === right.kind &&
    left.objectId === right.objectId
  );
}

function resolveStorage(storage?: Storage): Storage | null {
  if (storage) return storage;
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export function draftStorageKey(scope: DraftScope): string {
  if (!isDraftScope(scope)) {
    throw new TypeError("Draft scope must contain non-empty identifiers");
  }
  return [
    DRAFT_KEY_PREFIX,
    encodeURIComponent(scope.userId),
    encodeURIComponent(scope.projectId),
    scope.kind,
    encodeURIComponent(scope.objectId),
  ].join(":");
}

function isDraftEnvelope<T>(
  value: unknown,
  expectedScope: DraftScope
): value is DraftEnvelope<T> {
  if (!value || typeof value !== "object") return false;
  const envelope = value as Partial<DraftEnvelope<T>>;
  return (
    envelope.schemaVersion === DRAFT_SCHEMA_VERSION &&
    isDraftScope(envelope.scope) &&
    sameScope(envelope.scope, expectedScope) &&
    typeof envelope.savedAt === "number" &&
    Number.isFinite(envelope.savedAt) &&
    envelope.savedAt >= 0 &&
    typeof envelope.expiresAt === "number" &&
    Number.isFinite(envelope.expiresAt) &&
    envelope.expiresAt >= envelope.savedAt &&
    (envelope.baseFingerprint === null ||
      (typeof envelope.baseFingerprint === "string" &&
        SHA256_FINGERPRINT.test(envelope.baseFingerprint))) &&
    Object.hasOwn(envelope, "payload")
  );
}

export function saveDraft<T>(
  scope: DraftScope,
  payload: T,
  baseFingerprint: string | null,
  options: {
    storage?: Storage;
    now?: number;
    ttlMs?: number;
  } = {}
): SaveDraftResult<T> {
  const storage = resolveStorage(options.storage);
  if (!storage) return { status: "unavailable", reason: "storage" };

  const savedAt = options.now ?? Date.now();
  const ttlMs = options.ttlMs ?? DEFAULT_DRAFT_TTL_MS;
  if (
    !Number.isFinite(savedAt) ||
    savedAt < 0 ||
    !Number.isFinite(ttlMs) ||
    ttlMs <= 0 ||
    (baseFingerprint !== null &&
      !SHA256_FINGERPRINT.test(baseFingerprint))
  ) {
    return { status: "unavailable", reason: "serialization" };
  }
  const expiresAt = savedAt + ttlMs;
  if (!Number.isFinite(expiresAt)) {
    return { status: "unavailable", reason: "serialization" };
  }
  const draft: DraftEnvelope<T> = {
    schemaVersion: DRAFT_SCHEMA_VERSION,
    scope: { ...scope },
    savedAt,
    expiresAt,
    baseFingerprint,
    payload,
  };

  let serialized: string;
  try {
    serialized = JSON.stringify(draft);
  } catch {
    return { status: "unavailable", reason: "serialization" };
  }

  try {
    storage.setItem(draftStorageKey(scope), serialized);
    return { status: "saved", draft };
  } catch {
    return { status: "unavailable", reason: "storage" };
  }
}

export function loadDraft<T>(
  scope: DraftScope,
  options: { storage?: Storage; now?: number } = {}
): LoadDraftResult<T> {
  const storage = resolveStorage(options.storage);
  if (!storage) return { status: "unavailable", reason: "storage" };

  let serialized: string | null;
  try {
    serialized = storage.getItem(draftStorageKey(scope));
  } catch {
    return { status: "unavailable", reason: "storage" };
  }
  if (serialized === null) return { status: "missing" };

  let parsed: unknown;
  try {
    parsed = JSON.parse(serialized);
  } catch {
    return { status: "corrupt" };
  }
  if (!isDraftEnvelope<T>(parsed, scope)) return { status: "corrupt" };

  if (parsed.expiresAt <= (options.now ?? Date.now())) {
    return { status: "expired", draft: parsed };
  }
  return { status: "available", draft: parsed };
}

export function clearDraft(
  scope: DraftScope,
  storageOverride?: Storage
): ClearDraftResult {
  const storage = resolveStorage(storageOverride);
  if (!storage) return { status: "unavailable", reason: "storage" };

  try {
    const key = draftStorageKey(scope);
    if (storage.getItem(key) === null) return { status: "missing" };
    storage.removeItem(key);
    return { status: "cleared" };
  } catch {
    return { status: "unavailable", reason: "storage" };
  }
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, child]) => [key, canonicalize(child)])
    );
  }
  return value;
}

export async function fingerprintDraftBase(
  value: unknown
): Promise<FingerprintResult> {
  try {
    const subtle = globalThis.crypto?.subtle;
    if (!subtle) return { status: "unknown" };
    const serialized = JSON.stringify(canonicalize(value));
    if (serialized === undefined) return { status: "unknown" };
    const digest = await subtle.digest(
      "SHA-256",
      new TextEncoder().encode(serialized)
    );
    const fingerprint = Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
    return { status: "available", value: fingerprint };
  } catch {
    return { status: "unknown" };
  }
}
