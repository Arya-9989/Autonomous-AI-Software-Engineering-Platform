import { useCallback, useSyncExternalStore } from "react";

const STORAGE = "autoswe.theme";
type Theme = "light" | "dark";

const listeners = new Set<() => void>();

function current(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useTheme(): [Theme, () => void] {
  const theme = useSyncExternalStore(subscribe, current, () => "light" as Theme);
  const toggle = useCallback(() => {
    const next: Theme = current() === "dark" ? "light" : "dark";
    document.documentElement.classList.toggle("dark", next === "dark");
    try {
      localStorage.setItem(STORAGE, next);
    } catch {
      /* ignore */
    }
    listeners.forEach((listener) => listener());
  }, []);
  return [theme, toggle];
}
