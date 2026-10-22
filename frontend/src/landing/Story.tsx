import { useEffect, useRef, useState } from "react";

import { PipelineRail } from "../components/PipelineRail";
import { cx } from "../lib/cx";
import { AGENTS } from "../lib/stages";
import type { StageStatus } from "../lib/types";
import { sample } from "./sample";
import { StageVisual } from "./StageVisuals";

const COPY: { title: string; body: string }[] = [
  {
    title: "Reads the spec. Names the things.",
    body: "The requirements analyst turns your paragraph into a typed Requirements artifact: entities and their fields, user stories, functional and non-functional requirements, assumptions and what is out of scope.",
  },
  {
    title: "Designs before it writes.",
    body: "The architect chooses components and a tech stack, lists every endpoint, plans the deployment and records its decisions as ADRs. The code that follows is traced back to this design.",
  },
  {
    title: "Writes the service. And its tests.",
    body: "The code generator produces a FastAPI service with SQLAlchemy models, Pydantic schemas, one router per entity and a pytest suite that runs against an isolated database.",
  },
  {
    title: "Ready to ship, not just to run.",
    body: "The DevOps engineer adds a multi-stage, non-root Dockerfile with a health check, a GitHub Actions workflow, Kubernetes manifests and Terraform for ECR and ECS Fargate.",
  },
  {
    title: "Checks everything. Repairs what fails.",
    body: "The reviewer parses every file, applies security and operability rules, sends errors back to the code generator for one bounded repair round, then writes the review.",
  },
];

function StepLog({ index }: { index: number }) {
  const stage = sample.stages[index]!;
  return (
    <div className="mt-6 space-y-1 font-mono text-[12px] leading-[18px] md:text-[13px] md:leading-5">
      {stage.logs.map((line) => (
        <p key={line} className="text-ink-2">
          <span aria-hidden className="mr-2 text-ink-3">
            ›
          </span>
          {line}
        </p>
      ))}
      <p className="text-ink">
        <span aria-hidden className="mr-2 text-ok">
          ✓
        </span>
        {stage.summary}
      </p>
    </div>
  );
}

/**
 * "How it works": the five agents pin beside the copy and light up one by one as you scroll,
 * each showing what it really produced for the library spec.
 */
export function Story() {
  const steps = useRef<(HTMLLIElement | null)[]>([]);
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) setActive(Number((entry.target as HTMLElement).dataset.step));
        }
      },
      { rootMargin: "-48% 0px -48% 0px" },
    );
    steps.current.forEach((step) => step && observer.observe(step));
    return () => observer.disconnect();
  }, []);

  const states: StageStatus[] = AGENTS.map((_, index) =>
    index < active ? "succeeded" : index === active ? "running" : "pending",
  );

  return (
    <section id="how-it-works" aria-labelledby="how-title" className="bg-canvas py-24 md:py-40">
      <div className="wrap" data-reveal>
        <p className="t-eyebrow text-accent-ink">How it works</p>
        <h2 id="how-title" className="t-section mt-3 max-w-[760px] text-ink">
          One spec. Five specialists. Every handoff typed.
        </h2>
        <figure className="mt-10 max-w-[760px] rounded-[20px] bg-surface-2 p-5 md:p-7">
          <figcaption className="t-eyebrow text-ink-2">The spec used on this page</figcaption>
          <blockquote className="mt-2 text-[21px] leading-[29px] tracking-[0.011em] text-ink">
            “{sample.spec}”
          </blockquote>
        </figure>
      </div>

      <div className="wrap mt-8 grid gap-x-12 lg:mt-0 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
        <ol>
          {COPY.map((copy, index) => {
            const agent = AGENTS[index]!;
            return (
              <li
                key={agent.name}
                ref={(element) => {
                  steps.current[index] = element;
                }}
                data-step={index}
                className="flex flex-col justify-center py-12 lg:min-h-[78vh] lg:py-0"
              >
                <div data-reveal className="relative lg:pl-7">
                  <span
                    aria-hidden
                    className={cx(
                      "absolute top-0 bottom-0 left-0 hidden w-0.5 rounded-full bg-accent-ink transition-opacity duration-500 ease-apple lg:block",
                      active === index ? "opacity-100" : "opacity-0",
                    )}
                  />
                  <p className="t-eyebrow text-ink-2">
                    <span className={cx(active === index ? "text-accent-ink" : "")}>
                      {String(index + 1).padStart(2, "0")}
                    </span>{" "}
                    · {agent.label}
                  </p>
                  <h3 className="t-tile mt-3 text-ink">{copy.title}</h3>
                  <p className="t-body-lg mt-4 text-ink-2">{copy.body}</p>
                  <StepLog index={index} />
                </div>
                <div className="mt-8 lg:hidden">
                  <StageVisual index={index} />
                </div>
              </li>
            );
          })}
        </ol>

        <div className="hidden lg:block">
          <div className="sticky top-[84px] flex h-[calc(100vh-112px)] max-h-[760px] min-h-[600px] flex-col rounded-[28px] bg-surface-2 p-8">
            <PipelineRail states={states} highlight={active} caption="Agent shown alongside the text" />
            <div className="relative mt-8 flex-1">
              {AGENTS.map((_, index) => (
                <div
                  key={index}
                  aria-hidden={active !== index}
                  className={cx(
                    "absolute inset-0 flex flex-col overflow-hidden transition-[opacity,transform] duration-500 ease-apple [justify-content:safe_center]",
                    active === index ? "opacity-100" : "pointer-events-none translate-y-2 opacity-0",
                  )}
                >
                  <StageVisual index={index} />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
