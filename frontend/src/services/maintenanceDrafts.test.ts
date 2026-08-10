import { describe, expect, it, vi } from "vitest";
import {
  DRAFT_SCHEMA_VERSION,
  clearDraft,
  draftStorageKey,
  fingerprintDraftBase,
  loadDraft,
  saveDraft,
  type DraftScope,
} from "./maintenanceDrafts";

const SCOPE: DraftScope = {
  userId: "user/一",
  projectId: "project:一",
  kind: "chapter",
  objectId: "1",
};

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => Array.from(values.keys())[index] ?? null,
    removeItem: (key) => values.delete(key),
    setItem: (key, value) => values.set(key, value),
  };
}

describe("maintenance draft store", () => {
  it("isolates user, project, kind, and object identifiers", () => {
    const scopes = [
      SCOPE,
      { ...SCOPE, userId: "other-user" },
      { ...SCOPE, projectId: "other-project" },
      { ...SCOPE, kind: "lore-create" as const },
      { ...SCOPE, kind: "lore-migration" as const },
      { ...SCOPE, objectId: "2" },
    ];
    expect(new Set(scopes.map(draftStorageKey)).size).toBe(scopes.length);
    expect(draftStorageKey(SCOPE)).not.toContain("user/一");
  });

  it("saves and loads a versioned envelope with exact scope", () => {
    const storage = memoryStorage();
    const saved = saveDraft(
      SCOPE,
      { title: "标题", content: "正文" },
      "a".repeat(64),
      { storage, now: 100, ttlMs: 1_000 }
    );
    expect(saved.status).toBe("saved");

    const loaded = loadDraft<{ title: string; content: string }>(SCOPE, {
      storage,
      now: 200,
    });
    expect(loaded.status).toBe("available");
    if (loaded.status === "available") {
      expect(loaded.draft.schemaVersion).toBe(DRAFT_SCHEMA_VERSION);
      expect(loaded.draft.payload.content).toBe("正文");
      expect(loaded.draft.baseFingerprint).toBe("a".repeat(64));
    }
  });

  it("rejects a copied envelope whose embedded scope does not match", () => {
    const storage = memoryStorage();
    const otherScope = { ...SCOPE, objectId: "2" };
    const saved = saveDraft(otherScope, { content: "另一章" }, null, {
      storage,
      now: 100,
    });
    expect(saved.status).toBe("saved");
    storage.setItem(
      draftStorageKey(SCOPE),
      storage.getItem(draftStorageKey(otherScope))!
    );
    expect(loadDraft(SCOPE, { storage, now: 200 }).status).toBe("corrupt");
  });

  it("reports an expired draft without deleting it", () => {
    const storage = memoryStorage();
    saveDraft(SCOPE, { content: "仍可复制" }, null, {
      storage,
      now: 100,
      ttlMs: 50,
    });
    const key = draftStorageKey(SCOPE);
    expect(loadDraft(SCOPE, { storage, now: 150 }).status).toBe("expired");
    expect(storage.getItem(key)).not.toBeNull();
  });

  it("keeps corrupt and unknown-schema records for explicit user action", () => {
    const storage = memoryStorage();
    const key = draftStorageKey(SCOPE);
    storage.setItem(key, "{bad json");
    expect(loadDraft(SCOPE, { storage }).status).toBe("corrupt");
    expect(storage.getItem(key)).toBe("{bad json");

    storage.setItem(
      key,
      JSON.stringify({
        schemaVersion: 99,
        scope: SCOPE,
        savedAt: 1,
        expiresAt: 2,
        baseFingerprint: null,
        payload: { content: "旧格式" },
      })
    );
    expect(loadDraft(SCOPE, { storage, now: 1 }).status).toBe("corrupt");
    expect(storage.getItem(key)).not.toBeNull();
  });

  it("fails safely when storage rejects a write and preserves old content", () => {
    const storage = memoryStorage();
    const key = draftStorageKey(SCOPE);
    storage.setItem(key, "old-value");
    const unavailableStorage: Storage = {
      ...storage,
      setItem: vi.fn(() => {
        throw new DOMException("quota", "QuotaExceededError");
      }),
    };
    expect(
      saveDraft(SCOPE, { content: "不能写入" }, null, {
        storage: unavailableStorage,
      }).status
    ).toBe("unavailable");
    expect(storage.getItem(key)).toBe("old-value");
  });

  it("does not throw for serialization or storage read failures", () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(
      saveDraft(SCOPE, circular, null, { storage: memoryStorage() })
    ).toEqual({ status: "unavailable", reason: "serialization" });

    const storage = memoryStorage();
    const unavailableStorage: Storage = {
      ...storage,
      getItem: vi.fn(() => {
        throw new DOMException("blocked", "SecurityError");
      }),
    };
    expect(loadDraft(SCOPE, { storage: unavailableStorage })).toEqual({
      status: "unavailable",
      reason: "storage",
    });
    expect(
      saveDraft(SCOPE, { content: "正文" }, "not-a-sha256", {
        storage,
      })
    ).toEqual({ status: "unavailable", reason: "serialization" });
  });

  it("clears only the exact requested scope", () => {
    const storage = memoryStorage();
    const otherScope = { ...SCOPE, objectId: "2" };
    saveDraft(SCOPE, { content: "one" }, null, { storage });
    saveDraft(otherScope, { content: "two" }, null, { storage });

    expect(clearDraft(SCOPE, storage).status).toBe("cleared");
    expect(loadDraft(SCOPE, { storage }).status).toBe("missing");
    expect(loadDraft(otherScope, { storage }).status).toBe("available");
  });

  it("creates stable SHA-256 fingerprints independent of object key order", async () => {
    const left = await fingerprintDraftBase({
      title: "一",
      nested: { z: 2, a: 1 },
    });
    const right = await fingerprintDraftBase({
      nested: { a: 1, z: 2 },
      title: "一",
    });
    expect(left.status).toBe("available");
    expect(right).toEqual(left);
    if (left.status === "available") expect(left.value).toHaveLength(64);
  });
});
