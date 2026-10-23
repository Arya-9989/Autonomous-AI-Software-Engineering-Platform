import { formatDuration } from "../lib/format";
import type { JobEvent } from "../lib/types";

export const LABELS: Record<string, string> = {
  analyze: "analyst",
  architect: "architect",
  implement: "coder",
  devops: "devops",
  review: "reviewer",
};

/** One activity log line for an event (previous: the line above it). */
export function describe(event: JobEvent, previous?: JobEvent): { text: string; tone: string } {
  switch (event.event) {
    case "job.queued":
      return { text: `Queued for the ${String(event.provider ?? "default")} provider`, tone: "text-ink-2" };
    case "job.started":
      return { text: `Worker picked up the job (${String(event.model ?? "")})`, tone: "text-ink-2" };
    case "stage.started":
      return { text: `${event.label} started`, tone: "text-ink" };
    case "stage.log":
      return { text: event.message ?? "", tone: "text-ink-2" };
    case "stage.completed":
      return {
        text: `${event.summary ?? "Completed"} (${formatDuration(event.duration_s)})`,
        tone: "text-ok",
      };
    case "stage.failed":
      return { text: event.error ?? "Stage failed", tone: "text-bad" };
    case "job.succeeded":
      return { text: "Project ready", tone: "font-semibold text-ok" };
    case "job.failed": {
      // A stage failure already printed this message on the line above; do not repeat it.
      const repeated = previous?.event === "stage.failed" && previous.error === event.error;
      return {
        text: repeated ? "Job failed" : (event.error ?? "Job failed"),
        tone: "font-semibold text-bad",
      };
    }
    case "job.cancel_requested":
      return { text: "Cancellation requested", tone: "text-warn" };
    case "job.cancelled":
      return { text: "Job cancelled", tone: "font-semibold text-warn" };
    case "job.requeued":
      return { text: "Worker restarted; job requeued", tone: "text-warn" };
    case "job.retry_scheduled":
      return {
        text: `Provider error; retry ${String(event.attempt ?? 1)} in ${formatDuration(Number(event.delay_s ?? 0))}, resuming at the ${LABELS[String(event.stage)] ?? String(event.stage)}`,
        tone: "text-warn",
      };
    case "job.resumed": {
      const stages = Array.isArray(event.stages) ? event.stages.length : 0;
      return {
        text: `Resumed; ${stages} completed stage${stages === 1 ? "" : "s"} kept`,
        tone: "text-ink-2",
      };
    }
    case "job.retried":
      return { text: "Retry requested", tone: "text-warn" };
    default:
      return { text: event.event, tone: "text-ink-2" };
  }
}
