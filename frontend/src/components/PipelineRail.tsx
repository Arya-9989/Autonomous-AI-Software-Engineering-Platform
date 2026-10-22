import { Ban, Check, X } from "lucide-react";

import { cx } from "../lib/cx";
import { AGENTS } from "../lib/stages";
import type { StageStatus } from "../lib/types";

const STATUS_TEXT: Record<StageStatus, string> = {
  pending: "waiting",
  running: "working",
  succeeded: "done",
  failed: "failed",
  skipped: "skipped",
};

/**
 * The five-agent pipeline: AutoSWE's visual motif. Used in the hero, the scroll story, the
 * composer and the live job view, always with real stage states.
 */
export function PipelineRail({
  states,
  highlight,
  size = "md",
  labels = "short",
  className,
  caption,
}: {
  states: StageStatus[];
  /** Index of the stage to emphasise (e.g. the one being described), if any. */
  highlight?: number;
  size?: "md" | "lg";
  labels?: "short" | "full" | "none";
  className?: string;
  caption?: string;
}) {
  const node = size === "lg" ? "size-12 md:size-14" : "size-10";
  const icon = size === "lg" ? "size-5 md:size-6" : "size-[18px]";
  return (
    <ol className={cx("grid grid-cols-5", className)} aria-label={caption ?? "Pipeline stages"}>
      {AGENTS.map((agent, index) => {
        const status = states[index] ?? "pending";
        const Icon = agent.icon;
        const dim = highlight !== undefined && highlight !== index;
        const nextDone = status === "succeeded";
        return (
          <li key={agent.name} className={cx("relative flex flex-col items-center text-center")}>
            {index < AGENTS.length - 1 && (
              <span
                aria-hidden
                className={cx(
                  "absolute top-[var(--r)] left-[calc(50%_+_var(--r)_+_6px)] h-px w-[calc(100%_-_2*var(--r)_-_12px)] overflow-hidden bg-hairline-strong",
                  size === "lg" ? "[--r:24px] md:[--r:28px]" : "[--r:20px]",
                )}
              >
                <span
                  className={cx(
                    "block h-full origin-left bg-ink transition-transform duration-500 ease-apple",
                    nextDone ? "scale-x-100" : "scale-x-0",
                  )}
                />
              </span>
            )}
            <span
              className={cx(
                "relative grid shrink-0 place-items-center rounded-full transition-[background-color,color,box-shadow,opacity] duration-300 ease-apple",
                node,
                dim && "opacity-40",
                status === "succeeded" && "bg-ink text-canvas",
                status === "running" &&
                  "bg-accent text-white shadow-[0_0_0_6px_var(--accent-soft),0_0_32px_var(--accent-glow)]",
                status === "failed" && "bg-bad text-white",
                status === "skipped" && "bg-surface-2 text-ink-3",
                status === "pending" && "bg-canvas text-ink-3 ring-1 ring-hairline-strong ring-inset",
              )}
            >
              {status === "succeeded" ? (
                <Check className={icon} strokeWidth={2.5} aria-hidden />
              ) : status === "failed" ? (
                <X className={icon} strokeWidth={2.5} aria-hidden />
              ) : status === "skipped" ? (
                <Ban className={icon} aria-hidden />
              ) : (
                <Icon className={icon} strokeWidth={1.75} aria-hidden />
              )}
              {status === "running" && (
                <span
                  aria-hidden
                  className="absolute inset-0 animate-pulse-soft rounded-full ring-2 ring-accent-ink/60"
                />
              )}
            </span>
            {labels !== "none" && (
              <span className="mt-3 flex flex-col items-center gap-0.5 px-0.5" aria-hidden>
                <span className="t-eyebrow text-ink-3">{String(index + 1).padStart(2, "0")}</span>
                <span
                  className={cx(
                    "text-[12px] leading-4 font-medium tracking-[-0.01em] transition-colors duration-300 md:text-[14px] md:leading-5",
                    dim ? "text-ink-2" : "text-ink",
                    labels === "full" ? "max-md:hidden" : "",
                  )}
                >
                  {labels === "full" ? agent.label : agent.short}
                </span>
                {labels === "full" && (
                  <span
                    className={cx(
                      "text-[12px] leading-4 font-medium md:hidden",
                      dim ? "text-ink-2" : "text-ink",
                    )}
                  >
                    {agent.short}
                  </span>
                )}
              </span>
            )}
            <span className="sr-only">
              {agent.label}: {STATUS_TEXT[status]}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
