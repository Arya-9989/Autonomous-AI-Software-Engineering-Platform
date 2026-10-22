import { Suspense, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";

import { AGENTS } from "../lib/stages";
import { sample } from "./sample";
import { StageVisual } from "./StageVisuals";
import { COPY_COUNT, StepCopy } from "./StoryCopy";
import { CODE_FILES, REVIEW_LINES } from "./storyModel";
import { StoryPinned } from "./StoryPinned";
import { useHighlightedLines } from "./useStoryHighlight";

const LAST = AGENTS.length - 1;

/**
 * Each agent's copy beside what it really produced: the prerendered story, kept by phones, short
 * or narrow windows, reduced motion and readers without JavaScript. Every word visible, no motion
 * beyond the page's one-shot reveals.
 */
function StoryStatic({ onHeight }: { onHeight: (height: number) => void }) {
  const list = useRef<HTMLOListElement>(null);
  const highlighted = useHighlightedLines(list);
  // The pinned story takes exactly this height, so swapping one for the other moves nothing
  // below it: no layout shift, and an anchor the reader jumped to stays put.
  useLayoutEffect(() => {
    const element = list.current;
    if (!element) return;
    onHeight(element.offsetHeight);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => onHeight(element.offsetHeight));
    observer.observe(element);
    return () => observer.disconnect();
  }, [onHeight]);
  const extra: Record<number, ReactNode> = {
    2: <CodeCard path={CODE_FILES[0].path} html={highlighted.html[CODE_FILES[0].path]} />,
    3: <CodeCard path={CODE_FILES[1].path} html={highlighted.html[CODE_FILES[1].path]} />,
    4: <ReviewChecks />,
  };
  return (
    <ol ref={list} className="story-static wrap mt-8 lg:mt-16">
      {Array.from({ length: COPY_COUNT }, (_, index) => (
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
function CodeCard({ path, html }: { path: string; html?: string }) {
  const stage = CODE_FILES.find((f) => f.path === path)!.stage;
  const file = sample.showcase.find((f) => f.path === path)!;
  return (
    <div className="theme-dark min-w-0 overflow-hidden rounded-[20px] bg-[#0b0b0c] ring-1 ring-white/10">
      <p className="flex h-10 items-center gap-2 border-b border-white/10 px-5 t-eyebrow text-ink-2">
        <span className="text-ink normal-case">{path}</span> · {AGENTS[stage]!.label}
      </p>
      <div
        className="code-view scrollbar-thin max-h-[360px] overflow-auto"
        tabIndex={0}
        role="region"
        aria-label={`${path}, written by the ${AGENTS[stage]!.label.toLowerCase()}`}
      >
        {html ? (
          <div dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <pre className="px-5 text-ink">{file.content}</pre>
        )}
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
            <span className="text-ok">
              <span aria-hidden>✓</span>
              <span className="sr-only">passed</span>
            </span>
          </li>
        ))}
      </ul>
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

/** "How it works": five agents, one spec, every handoff typed. */
export function Story() {
  const pinned = usePinned();
  const [height, onHeight] = useState<number | undefined>(undefined);

  return (
    <section id="how-it-works" aria-labelledby="how-title" className="story bg-canvas py-24 md:py-40">
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
      {pinned ? (
        <Suspense fallback={<StoryStatic onHeight={onHeight} />}>
          <StoryPinned height={height} />
        </Suspense>
      ) : (
        <StoryStatic onHeight={onHeight} />
      )}
    </section>
  );
}
