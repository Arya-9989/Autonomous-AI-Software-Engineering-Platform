import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { followJobEvents } from "../lib/sse";
import type { JobEvent } from "../lib/types";

export type StreamStatus = "connecting" | "open" | "reconnecting" | "closed";

interface StreamState {
  jobId: string;
  events: JobEvent[];
  status: StreamStatus;
}

/** Subscribe to a job's SSE stream; keeps the event log and refreshes affected queries. */
export function useJobEvents(jobId: string | undefined) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<StreamState | null>(null);

  useEffect(() => {
    if (!jobId) return;
    const controller = new AbortController();
    const seen = new Set<number>();
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
        void queryClient.invalidateQueries({ queryKey: ["job", jobId], exact: true });
        if (event.event === "stage.completed") {
          for (const resource of ["files", "requirements", "architecture", "review"]) {
            void queryClient.invalidateQueries({ queryKey: ["job", jobId, resource] });
          }
        }
        if (event.event.startsWith("job.")) {
          void queryClient.invalidateQueries({ queryKey: ["jobs"] });
        }
      },
    });
    return () => controller.abort();
  }, [jobId, queryClient]);

  const current = state?.jobId === jobId ? state : null;
  return { events: current?.events ?? [], status: current?.status ?? "connecting" };
}
