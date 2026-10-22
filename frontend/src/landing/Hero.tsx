import { ArrowRight, Check, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { PipelineRail } from "../components/PipelineRail";
import { pill } from "../lib/pill";
import { cx } from "../lib/cx";
import { useReducedMotion } from "../lib/useReducedMotion";
import type { StageStatus } from "../lib/types";
import { sample } from "./sample";

const STEP_MS = 620;
const STAGES = sample.stages.length;

/**
 * Replays the real offline run once (about three seconds, then it stops): each agent starts,
 * reports its real summary and hands over. Reduced motion shows the finished run immediately.
 */
function useReplay() {
  const reduce = useReducedMotion();
  // step = number of finished stages; STAGES means the run is complete.
  const [step, setStep] = useState(0);
  const [run, setRun] = useState(0);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (reduce) return;
    let current = 0;
    const tick = () => {
      current += 1;
      setStep(current);
      if (current < STAGES) timer.current = window.setTimeout(tick, STEP_MS);
    };
    timer.current = window.setTimeout(tick, 700);
    return () => window.clearTimeout(timer.current);
  }, [run, reduce]);

  const replay = () => {
    setStep(0);
    setRun((value) => value + 1);
  };
  const shown = reduce ? STAGES : step;
  return { step: shown, done: shown >= STAGES, replay };
}

function HeroPanel() {
  const { step, done, replay } = useReplay();
  const states: StageStatus[] = sample.stages.map((_, index) =>
    index < step ? "succeeded" : index === step ? "running" : "pending",
  );
  const runningLog = sample.stages[step]?.logs[0];

  return (
    <figure className="relative mx-auto mt-14 w-full max-w-[1080px] md:mt-20">
      <div
        aria-hidden
        className="pointer-events-none absolute -inset-x-24 -top-40 -bottom-10 bg-[radial-gradient(closest-side,rgb(91_75_255/0.30),rgb(91_75_255/0.08)_60%,transparent)]"
      />
      <div className="relative overflow-hidden rounded-[28px] bg-[#0b0b0c] ring-1 ring-white/10 md:rounded-[36px]">
        {/* Header: the job, as the app shows it. */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-white/10 px-5 py-4 md:px-8 md:py-5">
          <span className="font-mono text-[13px] text-ink">{sample.requirements.project_name}</span>
          <span
            className={cx(
              "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[12px] leading-4 font-medium",
              done ? "bg-ok-soft text-ok" : "bg-accent-soft text-accent-ink",
            )}
          >
            {done ? "Succeeded" : "Running"}
          </span>
          <span className="ml-auto font-mono text-[12px] text-ink-2">
            {sample.provider.name} · {sample.provider.model}
          </span>
        </div>

        <div className="px-5 pt-6 md:px-8 md:pt-8">
          <p className="max-w-3xl text-[14px] leading-5 text-ink-2 md:text-[17px] md:leading-6">
            <span className="mr-2 font-mono text-[12px] tracking-[0.04em] text-ink-3 uppercase">Spec</span>
            <span className="text-ink">“{sample.spec}”</span>
          </p>
        </div>

        <div className="px-3 py-8 md:px-8 md:py-10">
          <PipelineRail states={states} size="lg" labels="full" caption="Replayed run of the library spec" />
        </div>

        <div className="grid border-t border-white/10 md:grid-cols-[1.4fr_1fr]">
          <ol
            className="min-h-[232px] space-y-2.5 px-5 py-5 font-mono text-[12px] leading-[18px] md:min-h-[212px] md:px-8 md:py-6 md:text-[13px] md:leading-5"
            aria-label="Stage summaries"
          >
            {sample.stages.map((stage, index) => (
              <li key={stage.name} className={cx("flex gap-3", index < step ? "visible" : "invisible")}>
                <Check className="mt-0.5 size-3.5 shrink-0 text-ok" aria-hidden />
                <span className="grid min-w-0 flex-1 gap-x-3 sm:grid-cols-[10ch_minmax(0,1fr)]">
                  <span className="text-ink-2">{stage.name}</span>
                  <span className="text-ink">{stage.summary}</span>
                </span>
              </li>
            ))}
            {!done && runningLog && (
              <li className="flex gap-3 text-accent-ink" aria-hidden>
                <span className="mt-1.5 size-1.5 shrink-0 animate-pulse-soft rounded-full bg-accent-ink" />
                <span>{runningLog}…</span>
              </li>
            )}
          </ol>
          <dl className="grid grid-cols-3 border-t border-white/10 md:grid-cols-1 md:border-t-0 md:border-l">
            {[
              { label: "Files", value: String(sample.files.length) },
              { label: "Validation errors", value: String(sample.validation.errors) },
              { label: "Review score", value: `${sample.review.score}` },
            ].map((stat) => (
              <div
                key={stat.label}
                className="flex flex-col justify-center border-white/10 px-5 py-4 not-last:border-r md:px-8 md:not-last:border-r-0 md:not-last:border-b"
              >
                <dt className="text-[12px] leading-4 text-ink-2">{stat.label}</dt>
                <dd
                  className={cx(
                    "mt-1 text-[28px] leading-8 font-semibold tracking-[0.007em] tabular-nums md:text-[40px] md:leading-[44px]",
                    done ? "text-ink" : "text-ink-2",
                  )}
                >
                  {done ? stat.value : "—"}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
      <figcaption className="mt-4 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-center text-[12px] leading-4 text-ink-2">
        <span>A replay of a real offline run on this spec. Every number comes from that run.</span>
        <button
          type="button"
          onClick={replay}
          disabled={!done}
          className="hit-44 inline-flex min-h-6 items-center gap-1 rounded-full px-1 text-accent-ink hover:underline disabled:opacity-0"
        >
          <RotateCcw className="size-3" aria-hidden /> Replay
        </button>
      </figcaption>
    </figure>
  );
}

export function Hero() {
  return (
    <section
      id="overview"
      aria-labelledby="hero-title"
      className="theme-dark relative overflow-hidden bg-black"
    >
      <div className="wrap-wide pt-14 pb-20 md:pt-24 md:pb-28">
        <div className="mx-auto max-w-[980px] text-center">
          <p className="text-[21px] leading-[25px] font-semibold tracking-[0.011em] text-ink md:text-[28px] md:leading-8">
            AutoSWE
          </p>
          <h1 id="hero-title" className="t-statement mt-3 text-ink">
            Plain English in.
            <br />
            <span className="text-gradient">Deployable project out.</span>
          </h1>
          <p className="t-lead mx-auto mt-6 max-w-[720px] text-ink">
            Five agents on Claude write the code, tests and infrastructure, then validate every file.
          </p>
          <div className="mt-9 flex flex-wrap items-center justify-center gap-4">
            <Link to="/app" className={pill("primary")}>
              Try it now
            </Link>
            <a href="#how-it-works" className={cx(pill("secondary"), "gap-1")}>
              See how it works <ArrowRight className="size-4" aria-hidden />
            </a>
          </div>
          <p className="mt-5 text-[14px] leading-5 text-ink-2">
            No API key? The deterministic offline provider runs the whole pipeline.
          </p>
        </div>
        <HeroPanel />
      </div>
    </section>
  );
}
