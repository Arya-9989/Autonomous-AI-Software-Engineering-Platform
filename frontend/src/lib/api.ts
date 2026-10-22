import type {
  Architecture,
  FileContent,
  FileEntry,
  JobDetail,
  JobPage,
  Requirements,
  Review,
  ServerInfo,
} from "./types";

const KEY_STORAGE = "autoswe.apiKey";

export function getApiKey(): string | null {
  try {
    return localStorage.getItem(KEY_STORAGE);
  } catch {
    return null;
  }
}

export function setApiKey(key: string | null): void {
  try {
    if (key) localStorage.setItem(KEY_STORAGE, key);
    else localStorage.removeItem(KEY_STORAGE);
  } catch {
    /* private mode: key lives for this page view only */
  }
}

export function authHeaders(): Record<string, string> {
  const key = getApiKey();
  return key ? { Authorization: `Bearer ${key}` } : {};
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function errorFrom(response: Response): Promise<ApiError> {
  let message = `${response.status} ${response.statusText}`;
  try {
    const body = (await response.json()) as { detail?: unknown; errors?: { msg: string }[] };
    if (body.errors?.length) message = body.errors.map((e) => e.msg).join("; ");
    else if (typeof body.detail === "string") message = body.detail;
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(response.status, message);
}

declare global {
  interface Window {
    /** A GET that boot.js started before the scripts loaded (job pages), used at most once. */
    __autosweEarly?: { url: string; response: Promise<Response> };
  }
}

function takeEarly(path: string, init: RequestInit): Promise<Response> | null {
  const early = typeof window === "undefined" ? undefined : window.__autosweEarly;
  if (!early || early.url !== path || (init.method ?? "GET") !== "GET") return null;
  window.__autosweEarly = undefined;
  return early.response;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await (takeEarly(path, init) ??
    fetch(path, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...authHeaders(),
        ...init.headers,
      },
    }));
  if (!response.ok) throw await errorFrom(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** The filename from `Content-Disposition: attachment; filename="x.zip"` (or project.zip). */
export function archiveName(disposition: string | null): string {
  return /filename="([^"]+)"/.exec(disposition ?? "")?.[1] ?? "project.zip";
}

const job = (id: string) => `/api/v1/jobs/${encodeURIComponent(id)}`;

export const api = {
  info: () => request<ServerInfo>("/api/v1/info"),
  listJobs: (limit = 30) => request<JobPage>(`/api/v1/jobs?limit=${limit}`),
  getJob: (id: string) => request<JobDetail>(job(id)),
  createJob: (body: { spec: string; title?: string; provider?: string }) =>
    request<JobDetail>("/api/v1/jobs", { method: "POST", body: JSON.stringify(body) }),
  cancelJob: (id: string) => request<JobDetail>(`${job(id)}/cancel`, { method: "POST" }),
  deleteJob: (id: string) => request<void>(job(id), { method: "DELETE" }),
  files: (id: string) => request<FileEntry[]>(`${job(id)}/files`),
  file: (id: string, path: string) =>
    request<FileContent>(`${job(id)}/files/${path.split("/").map(encodeURIComponent).join("/")}`),
  requirements: (id: string) => request<Requirements>(`${job(id)}/requirements`),
  architecture: (id: string) => request<Architecture>(`${job(id)}/architecture`),
  review: (id: string) => request<Review>(`${job(id)}/review`),

  /** Saves the job's zip; returns the file name the server chose (same as the folder inside). */
  async downloadArchive(id: string): Promise<string> {
    const response = await fetch(`${job(id)}/archive`, { headers: authHeaders() });
    if (!response.ok) throw await errorFrom(response);
    const filename = archiveName(response.headers.get("content-disposition"));
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return filename;
  },
};
