import { Ban, Check, Loader2, X } from "lucide-react";

import { formatDuration, formatTokens } from "../lib/format";
import type { Stage } from "../lib/types";
import { cx } from "../lib/cx";

function StageIcon({ stage, index }: { stage: Stage; index: number }) {
  const base = "grid size-9 shrink-0 place-items-center rounded-full text-sm font-semibold ring-4";
  switch (stage.status) {
    case "succeeded":
      return (
        <span className={cx(base, "bg-emerald-500 text-white ring-emerald-500/15")}>
          <Check className="size-4" strokeWidth={3} aria-hidden />
        </span>
      );
    case "running":
      return (
        <span className={cx(base, "animate-pulse-ring bg-brand-600 text-white ring-brand-500/20")}>
          <Loader2 className="size-4 animate-spin" aria-hidden />
        </span>
      );
    case "failed":
      return (
        <span className={cx(base, "bg-red-500 text-white ring-red-500/15")}>
          <X className="size-4" strokeWidth={3} aria-hidden />
        </span>
      );
    case "skipped":
      return (
        <span
          className={cx(
            base,
            "bg-zinc-100 text-zinc-400 ring-transparent dark:bg-zinc-800 dark:text-zinc-500",
          )}
        >
          <Ban className="size-4" aria-hidden />
        </span>
      );
    default:
      return (
        <span
          className={cx(
            base,
            "border border-zinc-200 bg-white text-zinc-400 ring-transparent dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-500",
          )}
        >
          {index + 1}
        </span>
      );
  }
}

const STATUS_TEXT: Record<Stage["status"], string> = {
  pending: "Waiting",
  running: "Working…",
  succeeded: "Done",
  failed: "Failed",
  skipped: "Skipped",
};

export function StageTracker({ stages }: { stages: Stage[] }) {
  return (
    <ol className="grid grid-cols-1 gap-0 md:grid-cols-5 md:gap-3" aria-label="Pipeline stages">
      {stages.map((stage, index) => {
        const last = index === stages.length - 1;
        const tokens = stage.input_tokens + stage.output_tokens;
        return (
          <li key={stage.name} className="relative flex gap-3 pb-5 md:flex-col md:gap-2.5 md:pb-0">
            {!last && (
              <span
                aria-hidden
                className={cx(
                  "absolute left-[17px] top-10 h-[calc(100%-2.75rem)] w-0.5 rounded md:left-11 md:top-[17px] md:h-0.5 md:w-[calc(100%-2.25rem)]",
                  stage.status === "succeeded" ? "bg-emerald-400/70" : "bg-zinc-200 dark:bg-zinc-800",
                )}
              />
            )}
            <StageIcon stage={stage} index={index} />
            <div className="min-w-0 pt-1 md:pt-0">
              <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{stage.label}</p>
              <p
                className={cx(
                  "text-xs",
                  stage.status === "running"
                    ? "text-brand-600 dark:text-brand-400"
                    : "text-zinc-500 dark:text-zinc-400",
                )}
              >
                {STATUS_TEXT[stage.status]}
                {stage.duration_s !== null && ` · ${formatDuration(stage.duration_s)}`}
                {tokens > 0 && ` · ${formatTokens(tokens)} tok`}
              </p>
              {stage.summary && (
                <p
                  className="mt-1 text-xs leading-relaxed text-zinc-600 md:line-clamp-3 dark:text-zinc-400"
                  title={stage.summary}
                >
                  {stage.summary}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
