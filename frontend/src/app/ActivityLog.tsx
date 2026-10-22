import { AnimatePresence, m } from "motion/react";
import { useEffect, useRef } from "react";

import { Skeleton } from "../components/ui";
import type { StreamStatus } from "../hooks/useJobEvents";
import { cx } from "../lib/cx";
import { formatDuration, formatTime } from "../lib/format";
import type { JobEvent } from "../lib/types";

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
    case "job.failed":
      return { text: event.error ?? "Job failed", tone: "font-semibold text-bad" };
    case "job.cancel_requested":
      return { text: "Cancellation requested", tone: "text-warn" };
    case "job.cancelled":
      return { text: "Job cancelled", tone: "font-semibold text-warn" };
    case "job.requeued":
      return { text: "Worker restarted; job requeued", tone: "text-warn" };
    default:
      return { text: event.event, tone: "text-ink-2" };
  }
}

const STREAM_LABEL: Record<StreamStatus, string> = {
  connecting: "Connecting",
  open: "Live",
  reconnecting: "Reconnecting",
  closed: "Complete",
};

/** Streaming event log (SSE): replayed history, then live events as they are stored. */
export function ActivityLog({ events, status }: { events: JobEvent[]; status: StreamStatus }) {
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = scroller.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [events.length]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-hairline px-5 py-3">
        <h2 className="text-[14px] leading-5 font-semibold tracking-[-0.016em] text-ink">Activity</h2>
        <span className="flex items-center gap-1.5 text-[12px] leading-4 text-ink-2">
          <span
            className={cx(
              "size-1.5 rounded-full",
              status === "open" ? "animate-pulse-soft bg-ok" : status === "closed" ? "bg-ink-3" : "bg-warn",
            )}
            aria-hidden
          />
          {STREAM_LABEL[status]}
        </span>
      </div>
      <div
        ref={scroller}
        className="scrollbar-thin max-h-80 min-h-48 flex-1 overflow-y-auto px-5 py-4 lg:max-h-none"
        role="log"
        aria-live="polite"
        aria-label="Job activity"
        tabIndex={0}
      >
        {events.length === 0 ? (
          <div className="space-y-4" aria-hidden>
            {[0.6, 0.85, 0.5, 0.75].map((width, index) => (
              <div key={index}>
                <Skeleton className="h-2.5 w-24" />
                <Skeleton className="mt-2 h-3.5" />
                <div style={{ width: `${width * 100}%` }}>
                  <Skeleton className="mt-1.5 h-3.5" />
                </div>
              </div>
            ))}
          </div>
        ) : (
          <ul className="space-y-3">
            <AnimatePresence initial={false}>
              {events.map((event, index) => {
                const { text, tone } = describe(event);
                return (
                  <m.li
                    key={event.id ?? `local-${index}`}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.24, ease: [0.25, 0.1, 0.25, 1] }}
                  >
                    <div className="flex items-center gap-2 font-mono text-[11px] leading-4 text-ink-2">
                      <time className="tabular-nums">{formatTime(event.at)}</time>
                      {event.stage && (
                        <span className="text-accent-ink">{LABELS[event.stage] ?? event.stage}</span>
                      )}
                    </div>
                    <p className={cx("text-[13px] leading-[18px] break-words", tone)}>{text}</p>
                  </m.li>
                );
              })}
            </AnimatePresence>
          </ul>
        )}
      </div>
    </div>
  );
}
