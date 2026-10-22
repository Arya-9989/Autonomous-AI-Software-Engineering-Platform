import { useCallback, useSyncExternalStore } from "react";

const STORAGE = "autoswe.theme";
export type Theme = "light" | "dark";

const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());

function current(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

function stored(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE);
    return value === "dark" || value === "light" ? value : null;
  } catch {
    return null;
  }
}

function apply(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
  notify();
}

// Follow the OS until the reader picks a theme explicitly (public/theme-init.js sets the first paint).
if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (event) => {
    if (!stored()) apply(event.matches ? "dark" : "light");
  });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE, theme);
  } catch {
    /* storage unavailable: the choice lasts for this page view */
  }
  apply(theme);
}

export function toggleTheme(): void {
  setTheme(current() === "dark" ? "light" : "dark");
}

export function useTheme(): [Theme, () => void] {
  const theme = useSyncExternalStore(subscribe, current, () => "dark" as Theme);
  const toggle = useCallback(() => toggleTheme(), []);
  return [theme, toggle];
}
