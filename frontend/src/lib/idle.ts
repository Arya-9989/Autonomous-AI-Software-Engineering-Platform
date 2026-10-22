/**
 * Run `task` once the page is idle (or after `fallbackMs` where requestIdleCallback is missing,
 * as in Safari). Used to fetch code for things a reader may open next, after first paint.
 * Returns a cancel function for effect cleanup.
 */
export function whenIdle(task: () => void, fallbackMs = 1500): () => void {
  if (typeof window.requestIdleCallback === "function") {
    const handle = window.requestIdleCallback(task, { timeout: 4000 });
    return () => window.cancelIdleCallback(handle);
  }
  const timer = window.setTimeout(task, fallbackMs);
  return () => window.clearTimeout(timer);
}
