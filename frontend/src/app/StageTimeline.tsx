import { AnimatePresence, m } from "motion/react";

import { PipelineRail } from "../components/PipelineRail";
import { cx } from "../lib/cx";
import { formatDuration, formatTokens } from "../lib/format";
import { STAGE_SPRING } from "../lib/motion";
import type { Stage } from "../lib/types";

const STATUS_TEXT: Record<Stage["status"], string> = {
  pending: "Waiting",
  running: "Working",
  succeeded: "Done",
  failed: "Failed",
  skipped: "Skipped",
};

/** The live job timeline: the five-agent rail plus each stage's status, timing and summary. */
export function StageTimeline({ stages }: { stages: Stage[] }) {
  return (
    <div>
      <PipelineRail
        states={stages.map((s) => s.status)}
        size="lg"
        labels="full"
        caption="Pipeline progress"
      />
      <ol
        className="mt-6 grid gap-2 md:max-lg:grid-cols-5 md:max-lg:gap-3 2xl:grid-cols-5 2xl:gap-3"
        aria-label="Stage details"
      >
        {stages.map((stage) => {
          const tokens = stage.input_tokens + stage.output_tokens;
          return (
            <m.li
              key={stage.name}
              layout
              transition={STAGE_SPRING}
              className={cx(
                "rounded-2xl px-4 py-3 transition-colors duration-300 ease-apple",
                stage.status === "running" ? "bg-accent-soft" : "bg-surface-2",
              )}
            >
              {/* Rows (narrow and mid widths) show the label; the five-column layouts rely on the rail. */}
              <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                <p className="text-[14px] leading-5 font-medium text-ink md:max-lg:hidden 2xl:hidden">
                  {stage.label}
                </p>
                <p
                  className={cx(
                    "text-[12px] leading-4 tabular-nums",
                    stage.status === "running"
                      ? "text-accent-ink"
                      : stage.status === "failed"
                        ? "text-bad"
                        : "text-ink-2",
                  )}
                >
                  {STATUS_TEXT[stage.status]}
                  {stage.duration_s !== null && ` · ${formatDuration(stage.duration_s)}`}
                  {tokens > 0 && ` · ${formatTokens(tokens)} tokens`}
                </p>
              </div>
              <AnimatePresence initial={false}>
                {stage.summary && (
                  <m.p
                    key={stage.summary}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.32, ease: [0.25, 0.1, 0.25, 1] }}
                    className="mt-1 text-[12px] leading-[18px] text-ink"
                  >
                    {stage.summary}
                  </m.p>
                )}
              </AnimatePresence>
            </m.li>
          );
        })}
      </ol>
    </div>
  );
}
