import { Check, File, Folder } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from "react";

import { cx } from "../lib/cx";
import { highlight } from "../lib/highlight";
import { AGENTS } from "../lib/stages";
import { sample } from "./sample";
import { Design, Entities, StageVisual } from "./StageVisuals";
import {
  bands,
  CODE_FILES,
  codeFollow,
  codeLines,
  FADE,
  REVIEW_LINES,
  treeRows,
  VERDICT,
  type Range,
} from "./storyModel";

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

/** What each agent hands to the next (src/autoswe/pipeline/agents.py). */
const ARTIFACTS = ["Requirements", "ArchitectureSpec", "FileBundle", "FileBundle", "ReviewReport"];

const ROWS = treeRows();
const LAST = AGENTS.length - 1;

/** Custom properties for one animated element: its entrance and/or exit range. */
function sx(
  enter?: Range | null,
  exit?: Range | null,
  extra?: Record<string, string | number>,
): CSSProperties {
  const style: Record<string, string | number> = { ...extra };
  if (enter) Object.assign(style, { "--ia": enter.a, "--ib": enter.b });
  if (exit) Object.assign(style, { "--oa": exit.a, "--ob": exit.b });
  return style as CSSProperties;
}

/** A cross-fading layer for agent k: in as its band starts, out as it ends (not the first in, nor the last out). */
function layer(k: number): { className: string; style: CSSProperties } {
  const { start, end } = bands[k]!;
  const enter = k === 0 ? null : { a: start, b: start + FADE };
  const exit = k === LAST ? null : { a: end - FADE, b: end };
  return {
    className: enter && exit ? "sx-io" : enter ? "sx-in" : "sx-out",
    style: sx(enter, exit),
  };
}

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

function StepCopy({ index }: { index: number }) {
  const agent = AGENTS[index]!;
  const copy = COPY[index]!;
  return (
    <>
      <p className="t-eyebrow text-ink-2">
        <span className="text-accent-ink">{String(index + 1).padStart(2, "0")}</span> · {agent.label}
      </p>
      <h3 className="t-tile mt-3 text-ink">{copy.title}</h3>
      <p className="t-body-lg mt-4 text-ink-2">{copy.body}</p>
      <StepLog index={index} />
    </>
  );
}

/**
 * Each agent's copy beside what it really produced: the prerendered story, kept by phones, short
 * or narrow windows, reduced motion and readers without JavaScript. Every word visible, no motion
 * beyond the page's one-shot reveals.
 */
function StoryStatic() {
  const list = useRef<HTMLOListElement>(null);
  const highlighted = useHighlightedLines(list);
  const extra: Record<number, ReactNode> = {
    2: <CodeCard path={CODE_FILES[0].path} highlighted={highlighted[CODE_FILES[0].path]} />,
    3: <CodeCard path={CODE_FILES[1].path} highlighted={highlighted[CODE_FILES[1].path]} />,
    4: <ReviewChecks />,
  };
  return (
    <ol ref={list} className="story-static wrap mt-8 lg:mt-16">
      {COPY.map((_, index) => (
        <li
          key={index}
          className="grid items-center gap-x-14 gap-y-8 py-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)] lg:py-16"
        >
          <div data-reveal>
            <StepCopy index={index} />
          </div>
          <div data-reveal className="grid min-w-0 gap-4">
            {index === LAST && extra[index]}
            <StageVisual index={index} />
            {index !== LAST && extra[index]}
          </div>
        </li>
      ))}
    </ol>
  );
}

/** A file the agent wrote, complete, in a scrollable frame (the static story's code view). */
function CodeCard({ path, highlighted }: { path: string; highlighted?: string[] }) {
  const stage = CODE_FILES.find((f) => f.path === path)!.stage;
  const lines = codeLines(path).map((line) => line.text);
  return (
    <div className="theme-dark min-w-0 overflow-hidden rounded-[20px] bg-[#0b0b0c] ring-1 ring-white/10">
      <p className="flex h-10 items-center gap-2 border-b border-white/10 px-5 t-eyebrow text-ink-2">
        <span className="text-ink normal-case">{path}</span> · {AGENTS[stage]!.label}
      </p>
      <div
        className="scrollbar-thin max-h-[360px] overflow-auto py-3"
        tabIndex={0}
        role="region"
        aria-label={`${path}, written by the ${AGENTS[stage]!.label.toLowerCase()}`}
      >
        <pre className="shiki min-w-max font-mono text-[12px] leading-5 text-ink">
          {lines.map((line, i) => (
            <span key={i} className="flex gap-4 pr-5">
              <span className="w-8 shrink-0 text-right text-ink-3 select-none" aria-hidden>
                {i + 1}
              </span>
              {highlighted?.[i] !== undefined ? (
                <span dangerouslySetInnerHTML={{ __html: highlighted[i]! }} />
              ) : (
                <span>{line}</span>
              )}
            </span>
          ))}
        </pre>
      </div>
    </div>
  );
}

/** What the reviewer ran over the project, in its order (the static story's review view). */
function ReviewChecks() {
  return (
    <div className="theme-dark rounded-[20px] bg-[#0b0b0c] p-5 ring-1 ring-white/10 md:p-6">
      <p className="t-eyebrow mb-3 text-ink-2">Reviewer · checks</p>
      <ul className="font-mono text-[12px] leading-[22px]">
        {REVIEW_LINES.map((line) => (
          <li
            key={line.tool}
            className="grid grid-cols-[minmax(0,128px)_minmax(0,1fr)_auto] items-center gap-3"
          >
            <span className="text-accent-ink">{line.tool}</span>
            <span className="truncate text-ink-2">{line.detail}</span>
            <Check className="size-3.5 text-ok" strokeWidth={3} aria-hidden />
            <span className="sr-only">passed</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// --- The pinned story ---------------------------------------------------------------------------

/** The five agents: each lights while it works, completes, and hands its typed artifact on. */
function StoryRail() {
  return (
    <ol className="grid grid-cols-5 px-6 pt-4 pb-3" aria-label="The five agents">
      {AGENTS.map((agent, k) => {
        const { start, end } = bands[k]!;
        const Icon = agent.icon;
        const done = { a: end - 0.018, b: end - 0.004 };
        return (
          <li key={agent.name} className="relative flex flex-col items-center text-center">
            {k < LAST && (
              <span
                aria-hidden
                className="absolute top-5 left-[calc(50%+28px)] h-px w-[calc(100%-56px)] overflow-hidden bg-white/15"
              >
                {/* The handoff: the line fills as this agent's artifact passes to the next. */}
                <span
                  className="sx-fill block h-full origin-left bg-accent-ink"
                  style={sx({ a: end - 0.02, b: end + 0.006 })}
                />
              </span>
            )}
            <span className="relative grid size-10 place-items-center rounded-full text-ink-3 ring-1 ring-white/20 ring-inset">
              <Icon className="size-[18px]" strokeWidth={1.75} aria-hidden />
              <span
                aria-hidden
                className="sx-io absolute inset-0 grid place-items-center rounded-full bg-accent text-white shadow-[0_0_0_5px_var(--accent-soft),0_0_28px_var(--accent-glow)]"
                style={sx({ a: start, b: start + 0.012 }, done, { "--rise": "0px" })}
              >
                <Icon className="size-[18px]" strokeWidth={1.75} />
              </span>
              <span
                aria-hidden
                className="sx-pop absolute inset-0 grid place-items-center rounded-full bg-ink text-canvas"
                style={sx(done)}
              >
                <Check className="size-[18px]" strokeWidth={2.5} />
              </span>
            </span>
            <span className="mt-2 text-[13px] leading-[18px] font-medium tracking-[-0.01em] text-ink">
              {agent.short}
            </span>
            <span
              className="sx-in mt-0.5 font-mono text-[11px] leading-4 text-accent-ink"
              style={sx(done, null, { "--rise": "4px" })}
            >
              <span className="sr-only">hands over </span>
              {ARTIFACTS[k]}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** The real project tree, growing as the agents write it; the reviewer ticks every file it checks. */
function StoryTree() {
  return (
    <div className="relative min-w-0 border-r border-white/10 px-4 py-3">
      <p className="flex h-7 items-center t-eyebrow text-ink-2">
        {sample.requirements.project_name}/ · {sample.files.length} files
      </p>
      <div className="relative">
        <p
          className="sx-out absolute inset-x-0 top-1 text-[13px] leading-5 text-ink-2"
          style={sx(null, { a: bands[1]!.start - 0.004, b: bands[1]!.start + 0.012 })}
        >
          No files yet. The analyst hands over a typed Requirements artifact, not code.
        </p>
        <ul aria-label="Generated files" className="font-mono text-[12px]">
          {ROWS.map((row) => (
            <li
              key={row.path}
              className="sx-tree h-[var(--row)]"
              style={{ "--y1": row.offsets[0], "--y2": row.offsets[1] } as CSSProperties}
            >
              <span
                className="sx-in flex h-full items-center gap-1.5"
                style={sx(row.enter, null, { "--rise": "6px", paddingLeft: row.depth * 14 })}
              >
                {row.file ? (
                  <File className="size-3 shrink-0 text-ink-3" aria-hidden />
                ) : (
                  <Folder className="size-3 shrink-0 text-accent-ink" aria-hidden />
                )}
                <span className="truncate text-ink">{row.name}</span>
                {row.check && (
                  <Check
                    className="sx-pop ml-auto size-3 shrink-0 text-ok"
                    strokeWidth={3}
                    style={sx(row.check)}
                    aria-hidden
                  />
                )}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** Shiki's lines for a file, once highlighted in the worker (plain text until then). */
function useHighlightedLines(target: React.RefObject<HTMLElement | null>) {
  const [lines, setLines] = useState<Record<string, string[]>>({});
  useEffect(() => {
    const element = target.current;
    if (!element || typeof IntersectionObserver === "undefined") return;
    let cancelled = false;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry?.isIntersecting) return;
        observer.disconnect();
        for (const { path } of CODE_FILES) {
          const file = sample.showcase.find((f) => f.path === path)!;
          highlight(file.content, file.language)
            .then((html) => {
              const doc = new DOMParser().parseFromString(html, "text/html");
              const rendered = [...doc.querySelectorAll(".line")].map((line) => line.innerHTML);
              if (!cancelled) setLines((previous) => ({ ...previous, [path]: rendered }));
            })
            .catch(() => undefined); // plain text stays
        }
      },
      { rootMargin: "600px 0px" },
    );
    observer.observe(element);
    return () => {
      cancelled = true;
      observer.disconnect();
    };
  }, [target]);
  return lines;
}

function CodePane({ path, highlighted }: { path: string; highlighted?: string[] }) {
  const lines = codeLines(path);
  const follow = codeFollow(path, ROWS.length);
  const stage = CODE_FILES.find((f) => f.path === path)!.stage;
  return (
    <div className="flex h-full min-w-0 flex-col">
      <p className="flex h-7 shrink-0 items-center gap-2 px-5 t-eyebrow text-ink-2">
        <span className="text-ink normal-case">{path}</span> · {AGENTS[stage]!.label}
      </p>
      <div className="code-mask relative min-h-0 flex-1 overflow-hidden">
        <pre
          className={cx("shiki font-mono text-[12px] leading-[var(--row)] text-ink", follow && "sx-follow")}
          style={follow ? sx(follow.at, null, { "--follow": follow.rows }) : undefined}
        >
          {lines.map((line, i) => (
            <span
              key={i}
              className="sx-in flex h-[var(--row)] gap-4 pr-5 whitespace-pre"
              style={sx(line.at, null, { "--rise": "6px" })}
            >
              <span className="w-8 shrink-0 text-right text-ink-3 select-none" aria-hidden>
                {i + 1}
              </span>
              {highlighted?.[i] !== undefined ? (
                <span dangerouslySetInnerHTML={{ __html: highlighted[i]! }} />
              ) : (
                <span>{line.text}</span>
              )}
            </span>
          ))}
        </pre>
      </div>
    </div>
  );
}

function ReviewPane() {
  const review = sample.stages[LAST]!;
  return (
    <div className="flex h-full flex-col px-5">
      <p className="flex h-7 shrink-0 items-center t-eyebrow text-ink-2">Reviewer · checks</p>
      <ul className="mt-2 font-mono text-[12px] leading-[22px]">
        {REVIEW_LINES.map((line) => (
          <li
            key={line.tool}
            className="sx-in grid grid-cols-[136px_minmax(0,1fr)_auto] items-center gap-3"
            style={sx(line.at, null, { "--rise": "6px" })}
          >
            <span className="text-accent-ink">{line.tool}</span>
            <span className="truncate text-ink-2">{line.detail}</span>
            <Check className="size-3.5 text-ok" strokeWidth={3} aria-hidden />
            <span className="sr-only">passed</span>
          </li>
        ))}
      </ul>
      <div className="sx-in mt-5 border-t border-white/10 pt-5" style={sx(VERDICT)}>
        <p className="font-mono text-[12px] leading-[18px] text-ink-2">› {review.logs[0]}</p>
        <p className="mt-3 text-[56px] leading-[56px] font-semibold tracking-[-0.01em] text-ink tabular-nums">
          {sample.review.score}
          <span className="text-[21px] leading-6 font-medium text-ink-2">/100</span>
        </p>
        <ul className="mt-4 space-y-2">
          {sample.review.findings.map((finding) => (
            <li key={finding.message} className="text-[13px] leading-[18px]">
              <p className="text-ink">{finding.message}</p>
              <p className="text-ink-2">{finding.suggestion}</p>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/**
 * The pinned story: a 100vh stage holds while the reader scrolls through five agents' worth of
 * page. The copy cross-fades from agent to agent; the rail lights each one in turn; the real file
 * tree grows as files are written; real code lines appear as the coder and DevOps engineer write
 * them; and the reviewer ticks off every file. All of it is scrubbed by scroll position and
 * plays backwards when the reader scrolls up.
 */
function StoryPinned({ track }: { track: React.RefObject<HTMLDivElement | null> }) {
  const highlighted = useHighlightedLines(track);
  const panes = [
    <div key="analyze" className="px-5 pt-2">
      <Entities bare />
    </div>,
    <div key="architect" className="px-5 pt-2">
      <Design bare />
    </div>,
    <CodePane key="implement" path={CODE_FILES[0].path} highlighted={highlighted[CODE_FILES[0].path]} />,
    <CodePane key="devops" path={CODE_FILES[1].path} highlighted={highlighted[CODE_FILES[1].path]} />,
    <ReviewPane key="review" />,
  ];

  return (
    <div className="story-pinned">
      <div ref={track} className="story-track relative mt-12">
        <div
          className="story-stage sticky top-[52px] flex h-[calc(100vh-52px)] items-center"
          style={{ "--rows": ROWS.length } as CSSProperties}
        >
          <div className="mx-auto grid w-full max-w-[1260px] grid-cols-[340px_minmax(0,1fr)] items-center gap-14 px-[22px]">
            <ol className="grid">
              {COPY.map((_, k) => {
                const { className, style } = layer(k);
                return (
                  <li key={k} className={cx(className, "col-start-1 row-start-1")} style={style}>
                    <StepCopy index={k} />
                  </li>
                );
              })}
            </ol>

            <figure className="theme-dark overflow-hidden rounded-[28px] bg-[#0b0b0c] ring-1 ring-white/10">
              <figcaption className="flex h-10 items-center gap-3 border-b border-white/10 px-5">
                <span className="font-mono text-[13px] text-ink">{sample.requirements.project_name}</span>
                <span className="ml-auto font-mono text-[12px] text-ink-2">
                  {sample.provider.name} · {sample.provider.model}
                </span>
              </figcaption>
              <StoryRail />
              <div
                className="grid grid-cols-[256px_minmax(0,1fr)] border-t border-white/10"
                style={{ height: "calc(var(--rows) * var(--row) + 52px)" }}
              >
                <StoryTree />
                <div className="grid min-w-0 grid-rows-[minmax(0,1fr)] overflow-hidden py-3">
                  {panes.map((pane, k) => {
                    const { className, style } = layer(k);
                    return (
                      <div
                        key={k}
                        className={cx(className, "col-start-1 row-start-1 min-w-0 bg-[#0b0b0c]")}
                        style={style}
                      >
                        {pane}
                      </div>
                    );
                  })}
                </div>
              </div>
            </figure>
          </div>
        </div>
      </div>
    </div>
  );
}

const PINNED_MEDIA =
  "(min-width: 1200px) and (min-height: 800px) and (prefers-reduced-motion: no-preference)";

function subscribe(onChange: () => void): () => void {
  const media = window.matchMedia(PINNED_MEDIA);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

/**
 * Whether this window gets the pinned story: large enough to hold it, and motion allowed. False
 * when prerendering and on first hydration, so the HTML always carries the static story.
 */
function usePinned(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(PINNED_MEDIA).matches,
    () => false,
  );
}

const nativeTimeline = () => typeof CSS !== "undefined" && CSS.supports("animation-timeline: view()");

/**
 * Browsers without CSS scroll-driven animations get the same story from Motion: `scroll()`
 * reports progress through the track and every CSS animation in it is seeked to that point.
 * Loaded only where it is needed; Chromium and Safari run the story in CSS alone.
 */
function useScrubFallback(track: React.RefObject<HTMLDivElement | null>, active: boolean) {
  useEffect(() => {
    const target = track.current;
    if (!active || !target) return;
    let stop: (() => void) | undefined;
    let disposed = false;
    void import("motion").then(({ scroll }) => {
      if (disposed) return;
      stop = scroll(
        (progress: number) => {
          for (const animation of target.getAnimations({ subtree: true })) {
            animation.currentTime = progress * 1000;
          }
        },
        { target, offset: ["start 52px", "end end"] },
      );
    });
    return () => {
      disposed = true;
      stop?.();
    };
  }, [track, active]);
}

/** "How it works": five agents, one spec, every handoff typed. */
export function Story() {
  const track = useRef<HTMLDivElement>(null);
  const pinned = usePinned();
  const fallback = pinned && !nativeTimeline();
  useScrubFallback(track, fallback);

  return (
    <section
      id="how-it-works"
      aria-labelledby="how-title"
      data-scrub={fallback ? "" : undefined}
      className="story bg-canvas py-24 md:py-40"
    >
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
      {pinned ? <StoryPinned track={track} /> : <StoryStatic />}
    </section>
  );
}
