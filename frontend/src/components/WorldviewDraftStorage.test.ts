import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { saveDraft, loadDraft, clearDraft, getDraftRaw, draftKey } from "./WorldviewDraftStorage";
import type { WorldviewData } from "@/types";

const EMPTY_DATA: WorldviewData = {
  characters: [],
  geography: [],
  factions: [],
  power_system: [],
  history: [],
  conflicts: [],
  special_settings: [],
};

const DRAFT = {
  data: {
    ...EMPTY_DATA,
    characters: [{ name: "Hero", personality: "brave", background: "", motivation: "", ability: "", relations: [] }],
    raw_text: "some raw text",
  },
  importText: "import text content",
  mode: "manual" as const,
  source: "manual" as const,
  savedAt: 1700000000000,
  schemaVersion: 1 as const,
};

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("WorldviewDraftStorage", () => {
  describe("loadDraft", () => {
    it("returns 'none' when no draft exists", () => {
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("none");
    });

    it("returns 'ok' with draft data after saveDraft", () => {
      saveDraft("user1", "proj1", DRAFT);
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("ok");
      if (result.status === "ok") {
        expect(result.draft.data.characters).toHaveLength(1);
        expect(result.draft.data.characters[0].name).toBe("Hero");
        expect(result.draft.data.raw_text).toBe("some raw text");
        expect(result.draft.importText).toBe("import text content");
        expect(result.draft.mode).toBe("manual");
        expect(result.draft.source).toBe("manual");
        expect(result.draft.savedAt).toBe(1700000000000);
        expect(result.draft.schemaVersion).toBe(1);
      }
    });

    it("returns 'corrupt' for invalid JSON", () => {
      localStorage.setItem("wv-draft:user1:proj1", "{not valid json");
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("corrupt");
    });

    it("returns 'corrupt' when data field is not an object", () => {
      localStorage.setItem("wv-draft:user1:proj1", JSON.stringify({
        data: "not an object",
        importText: "ok",
        mode: "manual",
        source: "manual",
        savedAt: 1,
        schemaVersion: 1,
      }));
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("corrupt");
    });

    it("returns 'corrupt' when characters is not an array", () => {
      localStorage.setItem("wv-draft:user1:proj1", JSON.stringify({
        data: { characters: "not array" },
        importText: "ok",
        mode: "manual",
        source: "manual",
        savedAt: 1,
        schemaVersion: 1,
      }));
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("corrupt");
    });

    // P0-5: validate ALL required arrays, not just characters
    it("returns 'corrupt' when geography is not an array", () => {
      localStorage.setItem("wv-draft:user1:proj1", JSON.stringify({
        data: { characters: [], geography: "not array" },
        importText: "ok",
        mode: "manual",
        source: "manual",
        savedAt: 1,
        schemaVersion: 1,
      }));
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("corrupt");
    });

    it("returns 'corrupt' when factions is not an array", () => {
      localStorage.setItem("wv-draft:user1:proj1", JSON.stringify({
        data: { characters: [], geography: [], factions: null },
        importText: "ok",
        mode: "manual",
        source: "manual",
        savedAt: 1,
        schemaVersion: 1,
      }));
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("corrupt");
    });

    it("returns 'corrupt' when power_system is not an array", () => {
      localStorage.setItem("wv-draft:user1:proj1", JSON.stringify({
        data: { characters: [], geography: [], factions: [], power_system: "nope" },
        importText: "ok",
        mode: "manual",
        source: "manual",
        savedAt: 1,
        schemaVersion: 1,
      }));
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("corrupt");
    });

    it("returns 'corrupt' when history is not an array", () => {
      localStorage.setItem("wv-draft:user1:proj1", JSON.stringify({
        data: { characters: [], geography: [], factions: [], power_system: [], history: 42 },
        importText: "ok",
        mode: "manual",
        source: "manual",
        savedAt: 1,
        schemaVersion: 1,
      }));
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("corrupt");
    });

    it("returns 'corrupt' when conflicts is not an array", () => {
      localStorage.setItem("wv-draft:user1:proj1", JSON.stringify({
        data: { characters: [], geography: [], factions: [], power_system: [], history: [], conflicts: {} },
        importText: "ok",
        mode: "manual",
        source: "manual",
        savedAt: 1,
        schemaVersion: 1,
      }));
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("corrupt");
    });

    it("returns 'corrupt' when special_settings is not an array", () => {
      localStorage.setItem("wv-draft:user1:proj1", JSON.stringify({
        data: { characters: [], geography: [], factions: [], power_system: [], history: [], conflicts: [], special_settings: "nope" },
        importText: "ok",
        mode: "manual",
        source: "manual",
        savedAt: 1,
        schemaVersion: 1,
      }));
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("corrupt");
    });

    // P0-5: validate source field
    it("returns 'corrupt' when source is invalid", () => {
      localStorage.setItem("wv-draft:user1:proj1", JSON.stringify({
        data: { characters: [], geography: [], factions: [], power_system: [], history: [], conflicts: [], special_settings: [] },
        importText: "ok",
        mode: "manual",
        source: "invalid_source",
        savedAt: 1,
        schemaVersion: 1,
      }));
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("corrupt");
    });

    it("returns 'corrupt' when source is missing", () => {
      localStorage.setItem("wv-draft:user1:proj1", JSON.stringify({
        data: { characters: [], geography: [], factions: [], power_system: [], history: [], conflicts: [], special_settings: [] },
        importText: "ok",
        mode: "manual",
        savedAt: 1,
        schemaVersion: 1,
      }));
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("corrupt");
    });

    it("returns 'corrupt' when mode is invalid", () => {
      localStorage.setItem("wv-draft:user1:proj1", JSON.stringify({
        data: { characters: [], geography: [], factions: [], power_system: [], history: [], conflicts: [], special_settings: [] },
        importText: "ok",
        mode: "invalid_mode",
        source: "manual",
        savedAt: 1,
        schemaVersion: 1,
      }));
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("corrupt");
    });

    it("returns 'corrupt' when schemaVersion mismatch", () => {
      localStorage.setItem("wv-draft:user1:proj1", JSON.stringify({
        data: { characters: [], geography: [], factions: [], power_system: [], history: [], conflicts: [], special_settings: [] },
        importText: "ok",
        mode: "manual",
        source: "manual",
        savedAt: 1,
        schemaVersion: 99,
      }));
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("corrupt");
    });

    // P0-5: distinguish "storage unavailable" from "corrupt payload"
    it("returns 'error' when localStorage.getItem throws", () => {
      const origGetItem = localStorage.getItem;
      Object.defineProperty(localStorage, "getItem", {
        configurable: true,
        value: vi.fn(() => { throw new Error("SecurityError"); }),
      });
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("error");
      if (result.status === "error") {
        expect(result.error).toBe("unavailable");
        expect(result.message).toContain("不可用");
      }
      // Restore
      Object.defineProperty(localStorage, "getItem", { configurable: true, value: origGetItem });
    });
  });

  // P0-1: saveDraft returns explicit success/failure
  describe("saveDraft result", () => {
    it("returns { success: true } on normal save", () => {
      const result = saveDraft("user1", "proj1", DRAFT);
      expect(result.success).toBe(true);
    });

    it("returns { success: false, error: 'quota' } on QuotaExceededError", () => {
      const origSetItem = localStorage.setItem;
      Object.defineProperty(localStorage, "setItem", {
        configurable: true,
        value: vi.fn(() => {
          throw new DOMException("Quota exceeded", "QuotaExceededError");
        }),
      });
      const result = saveDraft("user1", "proj1", DRAFT);
      expect(result.success).toBe(false);
      if (!result.success) {
        expect(result.error).toBe("quota");
        expect(result.message).toContain("已满");
      }
      Object.defineProperty(localStorage, "setItem", { configurable: true, value: origSetItem });
    });

    it("returns { success: false, error: 'unavailable' } on other errors", () => {
      const origSetItem = localStorage.setItem;
      Object.defineProperty(localStorage, "setItem", {
        configurable: true,
        value: vi.fn(() => { throw new TypeError("Not available"); }),
      });
      const result = saveDraft("user1", "proj1", DRAFT);
      expect(result.success).toBe(false);
      if (!result.success) {
        expect(result.error).toBe("unavailable");
        expect(result.message).toContain("不可用");
      }
      Object.defineProperty(localStorage, "setItem", { configurable: true, value: origSetItem });
    });
  });

  // P0-1: clearDraft returns explicit success/failure
  describe("clearDraft result", () => {
    it("returns { success: true } on normal clear", () => {
      saveDraft("user1", "proj1", DRAFT);
      const result = clearDraft("user1", "proj1");
      expect(result.success).toBe(true);
    });

    it("returns { success: true } when no draft exists", () => {
      const result = clearDraft("user1", "proj1");
      expect(result.success).toBe(true);
    });

    it("returns { success: false } when removeItem throws", () => {
      const origRemoveItem = localStorage.removeItem;
      Object.defineProperty(localStorage, "removeItem", {
        configurable: true,
        value: vi.fn(() => { throw new Error("SecurityError"); }),
      });
      const result = clearDraft("user1", "proj1");
      expect(result.success).toBe(false);
      if (!result.success) {
        expect(result.error).toBe("unavailable");
      }
      Object.defineProperty(localStorage, "removeItem", { configurable: true, value: origRemoveItem });
    });

    it("only clears the specified draft (scope isolation)", () => {
      saveDraft("user1", "proj1", { ...DRAFT, importText: "keep" });
      saveDraft("user1", "proj2", { ...DRAFT, importText: "keep2" });
      clearDraft("user1", "proj1");
      expect(loadDraft("user1", "proj1").status).toBe("none");
      expect(loadDraft("user1", "proj2").status).toBe("ok");
    });
  });

  describe("user + project isolation", () => {
    it("isolates drafts by userId", () => {
      saveDraft("user1", "proj1", { ...DRAFT, importText: "user1-proj1" });
      saveDraft("user2", "proj1", { ...DRAFT, importText: "user2-proj1" });

      const r1 = loadDraft("user1", "proj1");
      const r2 = loadDraft("user2", "proj1");
      expect(r1.status).toBe("ok");
      expect(r2.status).toBe("ok");
      if (r1.status === "ok" && r2.status === "ok") {
        expect(r1.draft.importText).toBe("user1-proj1");
        expect(r2.draft.importText).toBe("user2-proj1");
      }
    });

    it("isolates drafts by projectId", () => {
      saveDraft("user1", "proj1", { ...DRAFT, importText: "user1-proj1" });
      saveDraft("user1", "proj2", { ...DRAFT, importText: "user1-proj2" });

      const r1 = loadDraft("user1", "proj1");
      const r2 = loadDraft("user1", "proj2");
      expect(r1.status).toBe("ok");
      expect(r2.status).toBe("ok");
      if (r1.status === "ok" && r2.status === "ok") {
        expect(r1.draft.importText).toBe("user1-proj1");
        expect(r2.draft.importText).toBe("user1-proj2");
      }
    });

    it("uses distinct localStorage keys per scope", () => {
      expect(draftKey("user1", "proj1")).toBe("wv-draft:user1:proj1");
      expect(draftKey("user1", "proj2")).toBe("wv-draft:user1:proj2");
      expect(draftKey("user2", "proj1")).toBe("wv-draft:user2:proj1");
      expect(draftKey("user1", "proj1")).not.toBe(draftKey("user1", "proj2"));
      expect(draftKey("user1", "proj1")).not.toBe(draftKey("user2", "proj1"));
    });
  });

  describe("getDraftRaw", () => {
    it("returns the raw string for an existing draft", () => {
      saveDraft("user1", "proj1", DRAFT);
      const raw = getDraftRaw("user1", "proj1");
      expect(raw).toContain("Hero");
      expect(raw).toContain("import text content");
    });

    it("returns empty string when no draft exists", () => {
      expect(getDraftRaw("user1", "proj1")).toBe("");
    });
  });

  // ═══════════════════════════════════════════════════
  // P0-4: Element-level structure validation — table-driven
  // ═══════════════════════════════════════════════════
  describe("P0-4 element-level validation", () => {
    const EMPTY_ARRAYS = {
      characters: [],
      geography: [],
      factions: [],
      power_system: [],
      history: [],
      conflicts: [],
      special_settings: [],
    };

    function makeDraftWith(overrides: Record<string, unknown>): string {
      return JSON.stringify({
        data: { ...EMPTY_ARRAYS, ...overrides },
        importText: "ok",
        mode: "manual",
        source: "manual",
        savedAt: 1,
        schemaVersion: 1,
      });
    }

    function expectCorrupt(key: string, value: unknown, field: string) {
      const overrides: Record<string, unknown> = { [field]: [value] };
      localStorage.setItem("wv-draft:user1:proj1", makeDraftWith(overrides));
      const result = loadDraft("user1", "proj1");
      expect(result.status).toBe("corrupt");
    }

    // ── characters ──
    it.each([
      ["null element", null],
      ["missing name field", { personality: "", background: "", motivation: "", ability: "", relations: [] }],
      ["name is number", { name: 123, personality: "", background: "", motivation: "", ability: "", relations: [] }],
      ["personality is number", { name: "A", personality: 1, background: "", motivation: "", ability: "", relations: [] }],
      ["background is null", { name: "A", personality: "", background: null, motivation: "", ability: "", relations: [] }],
      ["motivation is array", { name: "A", personality: "", background: "", motivation: [], ability: "", relations: [] }],
      ["ability is object", { name: "A", personality: "", background: "", motivation: "", ability: {}, relations: [] }],
      ["relations is not array", { name: "A", personality: "", background: "", motivation: "", ability: "", relations: "not array" }],
      ["relations is null", { name: "A", personality: "", background: "", motivation: "", ability: "", relations: null }],
    ])("returns corrupt when characters has %s", (_label, badValue) => {
      expectCorrupt("characters", badValue, "characters");
    });

    // ── geography ──
    it.each([
      ["null element", null],
      ["missing name", { description: "", significance: "" }],
      ["missing description", { name: "", significance: "" }],
      ["missing significance", { name: "", description: "" }],
      ["name is number", { name: 1, description: "", significance: "" }],
      ["description is null", { name: "", description: null, significance: "" }],
      ["significance is array", { name: "", description: "", significance: [] }],
    ])("returns corrupt when geography has %s", (_label, badValue) => {
      expectCorrupt("geography", badValue, "geography");
    });

    // ── factions ──
    it.each([
      ["null element", null],
      ["missing name", { stance: "", power_level: "", relations: [] }],
      ["missing stance", { name: "", power_level: "", relations: [] }],
      ["missing power_level", { name: "", stance: "", relations: [] }],
      ["name is number", { name: 1, stance: "", power_level: "", relations: [] }],
      ["stance is null", { name: "", stance: null, power_level: "", relations: [] }],
      ["power_level is array", { name: "", stance: "", power_level: [], relations: [] }],
      ["relations is not array", { name: "", stance: "", power_level: "", relations: "no" }],
      ["relations is null", { name: "", stance: "", power_level: "", relations: null }],
    ])("returns corrupt when factions has %s", (_label, badValue) => {
      expectCorrupt("factions", badValue, "factions");
    });

    // ── power_system ──
    it.each([
      ["null element", null],
      ["missing name", { levels: "", rules: "", limitations: "" }],
      ["missing levels", { name: "", rules: "", limitations: "" }],
      ["missing rules", { name: "", levels: "", limitations: "" }],
      ["missing limitations", { name: "", levels: "", rules: "" }],
      ["name is number", { name: 1, levels: "", rules: "", limitations: "" }],
      ["levels is null", { name: "", levels: null, rules: "", limitations: "" }],
      ["rules is array", { name: "", levels: "", rules: [], limitations: "" }],
      ["limitations is object", { name: "", levels: "", rules: "", limitations: {} }],
    ])("returns corrupt when power_system has %s", (_label, badValue) => {
      expectCorrupt("power_system", badValue, "power_system");
    });

    // ── history ──
    it.each([
      ["null element", null],
      ["missing event", { time: "", description: "", impact: "" }],
      ["missing time", { event: "", description: "", impact: "" }],
      ["missing description", { event: "", time: "", impact: "" }],
      ["missing impact", { event: "", time: "", description: "" }],
      ["event is number", { event: 1, time: "", description: "", impact: "" }],
      ["time is null", { event: "", time: null, description: "", impact: "" }],
      ["description is array", { event: "", time: "", description: [], impact: "" }],
      ["impact is object", { event: "", time: "", description: "", impact: {} }],
    ])("returns corrupt when history has %s", (_label, badValue) => {
      expectCorrupt("history", badValue, "history");
    });

    // ── conflicts ──
    it.each([
      ["null element", null],
      ["missing name", { type: "", parties: "", stakes: "", resolution_hint: "" }],
      ["missing type", { name: "", parties: "", stakes: "", resolution_hint: "" }],
      ["missing parties", { name: "", type: "", stakes: "", resolution_hint: "" }],
      ["missing stakes", { name: "", type: "", parties: "", resolution_hint: "" }],
      ["missing resolution_hint", { name: "", type: "", parties: "", stakes: "" }],
      ["name is number", { name: 1, type: "", parties: "", stakes: "", resolution_hint: "" }],
      ["type is null", { name: "", type: null, parties: "", stakes: "", resolution_hint: "" }],
      ["parties is array", { name: "", type: "", parties: [], stakes: "", resolution_hint: "" }],
      ["stakes is object", { name: "", type: "", parties: "", stakes: {}, resolution_hint: "" }],
      ["resolution_hint is number", { name: "", type: "", parties: "", stakes: "", resolution_hint: 1 }],
    ])("returns corrupt when conflicts has %s", (_label, badValue) => {
      expectCorrupt("conflicts", badValue, "conflicts");
    });

    // ── special_settings ──
    it.each([
      ["null element", null],
      ["missing name", { description: "", rules: "" }],
      ["missing description", { name: "", rules: "" }],
      ["missing rules", { name: "", description: "" }],
      ["name is number", { name: 1, description: "", rules: "" }],
      ["description is null", { name: "", description: null, rules: "" }],
      ["rules is array", { name: "", description: "", rules: [] }],
    ])("returns corrupt when special_settings has %s", (_label, badValue) => {
      expectCorrupt("special_settings", badValue, "special_settings");
    });

    // ── raw_text and source type checks ──
    it("returns corrupt when raw_text is a number", () => {
      localStorage.setItem("wv-draft:user1:proj1", makeDraftWith({ raw_text: 123 }));
      expect(loadDraft("user1", "proj1").status).toBe("corrupt");
    });

    it("returns corrupt when raw_text is an array", () => {
      localStorage.setItem("wv-draft:user1:proj1", makeDraftWith({ raw_text: [] }));
      expect(loadDraft("user1", "proj1").status).toBe("corrupt");
    });

    it("returns corrupt when data.source is invalid", () => {
      localStorage.setItem("wv-draft:user1:proj1", makeDraftWith({ source: "bad" }));
      expect(loadDraft("user1", "proj1").status).toBe("corrupt");
    });

    it("accepts raw_text as null", () => {
      localStorage.setItem("wv-draft:user1:proj1", makeDraftWith({ raw_text: null }));
      expect(loadDraft("user1", "proj1").status).toBe("ok");
    });

    it("accepts raw_text as string", () => {
      localStorage.setItem("wv-draft:user1:proj1", makeDraftWith({ raw_text: "hello" }));
      expect(loadDraft("user1", "proj1").status).toBe("ok");
    });

    it("accepts raw_text as undefined (missing)", () => {
      localStorage.setItem("wv-draft:user1:proj1", makeDraftWith({}));
      expect(loadDraft("user1", "proj1").status).toBe("ok");
    });

    it("accepts a well-formed draft with all element types populated", () => {
      localStorage.setItem("wv-draft:user1:proj1", JSON.stringify({
        data: {
          characters: [{ name: "A", personality: "B", background: "C", motivation: "D", ability: "E", relations: [{ name: "X", relation: "Y" }] }],
          geography: [{ name: "G", description: "D", significance: "S" }],
          factions: [{ name: "F", stance: "S", power_level: "P", relations: [] }],
          power_system: [{ name: "P", levels: "L", rules: "R", limitations: "L" }],
          history: [{ event: "E", time: "T", description: "D", impact: "I" }],
          conflicts: [{ name: "C", type: "T", parties: "P", stakes: "S", resolution_hint: "R" }],
          special_settings: [{ name: "SS", description: "D", rules: "R" }],
          raw_text: "text",
          source: "manual",
        },
        importText: "ok",
        mode: "manual",
        source: "manual",
        savedAt: 1,
        schemaVersion: 1,
      }));
      expect(loadDraft("user1", "proj1").status).toBe("ok");
    });
  });
});
