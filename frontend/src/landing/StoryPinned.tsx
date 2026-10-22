// Its stylesheet is only for large screens with motion allowed, so it stays off the first paint's
// critical path: the component asks for it (React hoists the link and suspends until it has
// loaded), and the static story shows meanwhile (Story.tsx).
import storyCss from "./story.css?url";

import { Check, File, Folder } from "lucide-react";
import { useEffect, useRef, type CSSProperties } from "react";

import { cx } from "../lib/cx";
import { AGENTS } from "../lib/stages";
import { sample } from "./sample";
import { Design, Entities } from "./StageVisuals";
import { COPY_COUNT, StepCopy } from "./StoryCopy";
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
import { useHighlightedLines } from "./useStoryHighlight";

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

/**
 * The pinned story: a 100vh stage holds while the reader scrolls through five agents' worth of
 * page. The copy cross-fades from agent to agent; the rail lights each one in turn; the real file
 * tree grows as files are written; real code lines appear as the coder and DevOps engineer write
 * them; and the reviewer ticks off every file. All of it is scrubbed by scroll position and
 * plays backwards when the reader scrolls up.
 */
/** `height`: the static story's, so the swap shifts nothing; never less than the story needs. */
export function StoryPinned({ height }: { height?: number }) {
  const track = useRef<HTMLDivElement>(null);
  const fallback = !nativeTimeline();
  useScrubFallback(track, fallback);
  const highlighted = useHighlightedLines(track);
  const panes = [
    <div key="analyze" className="px-5 pt-2">
      <Entities bare />
    </div>,
    <div key="architect" className="px-5 pt-2">
      <Design bare />
    </div>,
    <CodePane
      key="implement"
      path={CODE_FILES[0].path}
      highlighted={highlighted.lines[CODE_FILES[0].path]}
    />,
    <CodePane key="devops" path={CODE_FILES[1].path} highlighted={highlighted.lines[CODE_FILES[1].path]} />,
    <ReviewPane key="review" />,
  ];

  return (
    <div className="story-pinned" data-scrub={fallback ? "" : undefined}>
      <link rel="stylesheet" href={storyCss} precedence="story" />
      <div
        ref={track}
        className="story-track relative mt-8 lg:mt-16"
        style={height ? { height: `max(${height}px, 250vh)` } : undefined}
      >
        <div
          className="story-stage sticky top-[52px] flex h-[calc(100vh-52px)] items-center"
          style={{ "--rows": ROWS.length } as CSSProperties}
        >
          <div className="mx-auto grid w-full max-w-[1260px] grid-cols-[340px_minmax(0,1fr)] items-center gap-14 px-[22px]">
            <ol className="grid">
              {Array.from({ length: COPY_COUNT }, (_, k) => {
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
