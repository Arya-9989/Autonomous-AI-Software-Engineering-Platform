import { authHeaders } from "./api";
import type { JobEvent } from "./types";

export interface RawEvent {
  id: string | null;
  event: string;
  data: string;
}

/** Incremental Server-Sent Events parser: returns complete events and the unparsed tail. */
export function parseSSE(buffer: string): { events: RawEvent[]; rest: string } {
  const normalised = buffer.replace(/\r\n?/g, "\n");
  const blocks = normalised.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events: RawEvent[] = [];
  for (const block of blocks) {
    let id: string | null = null;
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (!line || line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "id") id = value;
      else if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (data.length) events.push({ id, event, data: data.join("\n") });
  }
  return { events, rest };
}

export function toJobEvent(raw: RawEvent): JobEvent | null {
  try {
    const payload = JSON.parse(raw.data) as Record<string, unknown>;
    return { ...payload, id: raw.id ? Number(raw.id) : null, event: raw.event };
  } catch {
    return null;
  }
}

interface StreamOptions {
  signal: AbortSignal;
  onEvent: (event: JobEvent) => void;
  onStatus?: (status: "connecting" | "open" | "reconnecting" | "closed") => void;
  lastEventId?: number;
}

/**
 * Follow a job's event stream with fetch (so the API key travels in a header, never the URL).
 * Reconnects with Last-Event-ID after network errors; resolves when the server sends `end`.
 */
export async function followJobEvents(jobId: string, options: StreamOptions): Promise<void> {
  let lastId = options.lastEventId ?? 0;
  let attempt = 0;
  while (!options.signal.aborted) {
    options.onStatus?.(attempt === 0 ? "connecting" : "reconnecting");
    try {
      const response = await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/events`, {
        headers: { Accept: "text/event-stream", "Last-Event-ID": String(lastId), ...authHeaders() },
        signal: options.signal,
      });
      if (!response.ok || !response.body) {
        if (response.status >= 400 && response.status < 500) break; // auth / not found: give up
        throw new Error(`stream failed: ${response.status}`);
      }
      options.onStatus?.("open");
      attempt = 0;
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        const parsed = parseSSE(buffer + value);
        buffer = parsed.rest;
        for (const raw of parsed.events) {
          if (raw.event === "end") {
            options.onStatus?.("closed");
            return;
          }
          const event = toJobEvent(raw);
          if (event) {
            if (event.id !== null) lastId = event.id;
            options.onEvent(event);
          }
        }
      }
    } catch (error) {
      if (options.signal.aborted) break;
      console.warn("event stream interrupted", error);
    }
    attempt += 1;
    await new Promise((resolve) => setTimeout(resolve, Math.min(1000 * 2 ** attempt, 10_000)));
  }
  options.onStatus?.("closed");
}
