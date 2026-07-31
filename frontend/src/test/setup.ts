import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// Node.js 22+ exposes an experimental `localStorage` global that requires
// --localstorage-file and is undefined without it, shadowing jsdom's
// window.localStorage in the vitest test environment. This causes every
// bare `localStorage` access in source code (api.ts, WorldviewDraftStorage, etc.)
// to fail. Define a spec-compliant mock on globalThis so all tests work.
if (typeof globalThis.localStorage === "undefined" || globalThis.localStorage === undefined) {
  const _store = new Map<string, string>();
  const _ls: Storage = {
    getItem: (key: string) => _store.get(key) ?? null,
    setItem: (key: string, value: string) => void _store.set(key, String(value)),
    removeItem: (key: string) => void _store.delete(key),
    clear: () => _store.clear(),
    key: (i: number) => Array.from(_store.keys())[i] ?? null,
    get length() { return _store.size; },
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: _ls,
    writable: true,
    configurable: true,
  });
}

// Reset DOM and mocks after each test
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
});

// Mock matchMedia (used by some UI components)
beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});
