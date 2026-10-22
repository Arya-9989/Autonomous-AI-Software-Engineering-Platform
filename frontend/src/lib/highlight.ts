/**
 * Highlight code to HTML in a Web Worker (highlight.worker.ts), so tokenising a large file never
 * blocks the page. Callers keep showing the plain text until the promise resolves, and keep it
 * if it rejects.
 */

interface Reply {
  id: number;
  html?: string;
  error?: string;
}

let worker: Worker | null = null;
let nextId = 0;
const pending = new Map<number, { resolve: (html: string) => void; reject: (error: Error) => void }>();

function getWorker(): Worker {
  if (worker) return worker;
  worker = new Worker(new URL("./highlight.worker.ts", import.meta.url), { type: "module" });
  worker.addEventListener("message", (event: MessageEvent<Reply>) => {
    const { id, html, error } = event.data;
    const request = pending.get(id);
    pending.delete(id);
    if (!request) return;
    if (html === undefined) request.reject(new Error(error ?? "highlighting failed"));
    else request.resolve(html);
  });
  worker.addEventListener("error", () => {
    // A broken worker fails every outstanding and future request; text stays plain.
    for (const request of pending.values()) request.reject(new Error("highlighter unavailable"));
    pending.clear();
  });
  return worker;
}

export function highlight(code: string, language: string): Promise<string> {
  if (typeof Worker === "undefined") return Promise.reject(new Error("no Web Worker support"));
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    getWorker().postMessage({ id, code, language });
  });
}
