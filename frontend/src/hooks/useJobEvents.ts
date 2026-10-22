import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { followJobEvents } from "../lib/sse";
import type { JobEvent } from "../lib/types";

export type StreamStatus = "connecting" | "open" | "reconnecting" | "closed";

interface StreamState {
  jobId: string;
  events: JobEvent[];
  status: StreamStatus;
}

/** Query keys an event makes stale (log lines change nothing the REST API returns). */
export function staleQueries(event: JobEvent, jobId: string): string[][] {
  if (event.event === "stage.log") return [];
  const keys = [["job", jobId]];
  if (event.event === "stage.completed") {
    for (const resource of ["files", "requirements", "architecture", "review"]) {
      keys.push(["job", jobId, resource]);
    }
  }
  if (event.event.startsWith("job.")) keys.push(["jobs"]);
  return keys;
}

const COALESCE_MS = 120;

/**
 * Subscribe to a job's SSE stream; keeps the event log and refreshes affected queries.
 * `refresh` is false while the job is known to be finished: its replayed history cannot change
 * data that was loaded after it happened.
 */
export function useJobEvents(jobId: string | undefined, { refresh = true }: { refresh?: boolean } = {}) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<StreamState | null>(null);
  const refreshing = useRef(refresh);
  useEffect(() => {
    refreshing.current = refresh;
  }, [refresh]);

  useEffect(() => {
    if (!jobId) return;
    const controller = new AbortController();
    const seen = new Set<number>();
    // A finished job replays its whole history at once: refetch once per burst, not per event.
    const stale = new Map<string, string[]>();
    let timer: number | undefined;
    const flush = () => {
      timer = undefined;
      for (const queryKey of stale.values()) {
        // The job itself exactly; a resource key also covers its children (file contents).
        void queryClient.invalidateQueries({ queryKey, exact: queryKey.length === 2 });
      }
      stale.clear();
    };
    // State is keyed by job, so switching jobs never shows the previous job's events.
    const update = (change: (current: StreamState) => StreamState) =>
      setState((previous) =>
        change(previous?.jobId === jobId ? previous : { jobId, events: [], status: "connecting" }),
      );

    void followJobEvents(jobId, {
      signal: controller.signal,
      onStatus: (status) => update((current) => ({ ...current, status })),
      onEvent: (event) => {
        if (event.id !== null) {
          if (seen.has(event.id)) return;
          seen.add(event.id);
        }
        update((current) => ({ ...current, events: [...current.events, event] }));
        const keys = refreshing.current ? staleQueries(event, jobId) : [];
        if (keys.length === 0) return;
        for (const key of keys) stale.set(key.join("/"), key);
        timer ??= window.setTimeout(flush, COALESCE_MS);
      },
    });
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [jobId, queryClient]);

  const current = state?.jobId === jobId ? state : null;
  return { events: current?.events ?? [], status: current?.status ?? "connecting" };
}
