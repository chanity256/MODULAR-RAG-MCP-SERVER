import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  THEME_STORAGE_KEY,
  getStoredTheme,
  getThemeSnapshot,
  setTheme,
  subscribeTheme,
  toggleTheme,
} from "./theme";
import { NAV_ITEMS, isActive, normalizePathname } from "./nav";

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.classList.remove("dark");
});

describe("theme", () => {
  it("setTheme toggles the dark class, persists, and notifies subscribers", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeTheme(listener);
    setTheme("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(getThemeSnapshot()).toBe("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(getStoredTheme()).toBe("dark");
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
    setTheme("light");
    expect(listener).toHaveBeenCalledTimes(1);
    expect(getThemeSnapshot()).toBe("light");
  });

  it("toggleTheme flips and returns the new theme", () => {
    expect(toggleTheme("light")).toBe("dark");
    expect(toggleTheme("dark")).toBe("light");
  });

  it("ignores garbage in storage", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "purple");
    expect(getStoredTheme()).toBeNull();
  });
});

describe("nav", () => {
  it("lists the eight spec navigation entries with Chat at the root", () => {
    expect(NAV_ITEMS.map((i) => i.label)).toEqual([
      "Chat",
      "Overview",
      "Ingestion Traces",
      "Query Traces",
      "Data Browser",
      "Ingestion Manager",
      "Evaluation",
      "Query Playground",
    ]);
    expect(NAV_ITEMS[0].href).toBe("/");
  });

  it("matches active routes regardless of trailingSlash", () => {
    expect(normalizePathname("/")).toBe("/");
    expect(normalizePathname("/overview/")).toBe("/overview");
    expect(isActive("/overview", "/overview/")).toBe(true);
    expect(isActive("/", "/overview/")).toBe(false);
  });
});
