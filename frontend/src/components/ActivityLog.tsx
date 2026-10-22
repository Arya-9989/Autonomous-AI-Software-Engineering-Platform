import { Activity } from "lucide-react";
import { useEffect, useRef } from "react";

import type { StreamStatus } from "../hooks/useJobEvents";
import { formatDuration, formatTime } from "../lib/format";
import type { JobEvent } from "../lib/types";
import { cx } from "../lib/cx";

const LABELS: Record<string, string> = {
  analyze: "analyst",
  architect: "architect",
  implement: "coder",
  devops: "devops",
  review: "reviewer",
};

function describe(event: JobEvent): { text: string; tone: string } {
  switch (event.event) {
    case "job.queued":
      return {
        text: `Queued for the ${String(event.provider ?? "default")} provider`,
        tone: "text-zinc-500",
      };
    case "job.started":
      return { text: `Worker picked up the job (${String(event.model ?? "")})`, tone: "text-zinc-500" };
    case "stage.started":
      return { text: `${event.label} started`, tone: "text-zinc-700 dark:text-zinc-300" };
    case "stage.log":
      return { text: event.message ?? "", tone: "text-zinc-600 dark:text-zinc-400" };
    case "stage.completed":
      return {
        text: `${event.summary ?? "Completed"} (${formatDuration(event.duration_s)})`,
        tone: "text-emerald-700 dark:text-emerald-400",
      };
    case "stage.failed":
      return { text: event.error ?? "Stage failed", tone: "text-red-600 dark:text-red-400" };
    case "job.succeeded":
      return { text: "Project ready", tone: "font-medium text-emerald-700 dark:text-emerald-400" };
    case "job.failed":
      return { text: event.error ?? "Job failed", tone: "font-medium text-red-600 dark:text-red-400" };
    case "job.cancel_requested":
      return { text: "Cancellation requested", tone: "text-amber-600 dark:text-amber-400" };
    case "job.cancelled":
      return { text: "Job cancelled", tone: "font-medium text-amber-600 dark:text-amber-400" };
    case "job.requeued":
      return { text: "Worker restarted; job requeued", tone: "text-amber-600 dark:text-amber-400" };
    default:
      return { text: event.event, tone: "text-zinc-500" };
  }
}

export function ActivityLog({ events, status }: { events: JobEvent[]; status: StreamStatus }) {
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = scroller.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [events.length]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-zinc-100 px-4 py-2.5 dark:border-zinc-800">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Activity className="size-4 text-zinc-400" aria-hidden /> Activity
        </h3>
        <span className="flex items-center gap-1.5 text-[11px] text-zinc-500">
          <span
            className={cx(
              "size-1.5 rounded-full",
              status === "open"
                ? "animate-pulse bg-emerald-500"
                : status === "closed"
                  ? "bg-zinc-400"
                  : "bg-amber-500",
            )}
            aria-hidden
          />
          {status === "open" ? "Live" : status === "closed" ? "Complete" : "Connecting"}
        </span>
      </div>
      <div
        ref={scroller}
        className="scrollbar-thin max-h-80 min-h-40 flex-1 overflow-y-auto px-4 py-3 lg:max-h-none"
        aria-live="polite"
      >
        {events.length === 0 ? (
          <p className="text-sm text-zinc-400">Waiting for events…</p>
        ) : (
          <ul className="space-y-2.5">
            {events.map((event, index) => {
              const { text, tone } = describe(event);
              return (
                <li key={event.id ?? `local-${index}`} className="animate-fade-in">
                  <div className="flex items-center gap-2 font-mono text-[10.5px] text-zinc-400 dark:text-zinc-500">
                    <time className="tabular-nums">{formatTime(event.at)}</time>
                    {event.stage && (
                      <span className="text-brand-600 dark:text-brand-400">
                        {LABELS[event.stage] ?? event.stage}
                      </span>
                    )}
                  </div>
                  <p className={cx("text-[13px] leading-snug break-words", tone)}>{text}</p>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
