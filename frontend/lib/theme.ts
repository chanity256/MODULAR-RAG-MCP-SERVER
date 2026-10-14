/*
 * Derived from sirchmunk `web/lib/theme.ts` (Apache-2.0). Modified: project-owned
 * storage key, useSyncExternalStore-friendly subscribe/snapshot API. See ../NOTICE.
 */

export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "rag-theme";

type Listener = () => void;
const listeners = new Set<Listener>();

export function getStoredTheme(): Theme | null {
  if (typeof window === "undefined") return null;
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : null;
  } catch {
    return null;
  }
}

export function getSystemTheme(): Theme {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyThemeToDocument(theme: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", theme === "dark");
}

/** Current theme as seen by the page (the `dark` class set by ThemeScript is authoritative). */
export function getThemeSnapshot(): Theme {
  if (typeof document === "undefined") return "light";
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

export function subscribeTheme(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function setTheme(theme: Theme): void {
  applyThemeToDocument(theme);
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Persisting is best effort.
  }
  listeners.forEach((l) => l());
}

export function toggleTheme(current: Theme): Theme {
  const next: Theme = current === "dark" ? "light" : "dark";
  setTheme(next);
  return next;
}
