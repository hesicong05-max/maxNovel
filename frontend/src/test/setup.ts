import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// Node 24 may expose an unusable localStorage placeholder unless a storage
// file is configured. Install a deterministic in-memory implementation so
// tests behave consistently across Node versions and CI runners.
const storage = new Map<string, string>();
const localStorageMock: Storage = {
  get length() {
    return storage.size;
  },
  clear() {
    storage.clear();
  },
  getItem(key: string) {
    return storage.has(key) ? storage.get(key)! : null;
  },
  key(index: number) {
    return Array.from(storage.keys())[index] ?? null;
  },
  removeItem(key: string) {
    storage.delete(key);
  },
  setItem(key: string, value: string) {
    storage.set(String(key), String(value));
  },
};

Object.defineProperty(window, "localStorage", {
  configurable: true,
  value: localStorageMock,
});
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: localStorageMock,
});

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
