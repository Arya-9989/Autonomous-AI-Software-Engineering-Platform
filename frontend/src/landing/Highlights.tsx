import { Pause, Play } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { cx } from "../lib/cx";
import { useReducedMotion } from "../lib/useReducedMotion";
import { AGENTS } from "../lib/stages";
import { sample } from "./sample";

const AUTOPLAY_MS = 6000;

/** Artifact types each agent returns (src/autoswe/pipeline/agents.py). */
const ARTIFACTS = ["Requirements", "ArchitectureSpec", "FileBundle", "FileBundle", "ReviewReport"];

const count = (predicate: (path: string) => boolean) => sample.files.filter((f) => predicate(f.path)).length;

const PARSERS = [
  { kind: "Python", parser: "ast", files: count((p) => p.endsWith(".py")) },
  { kind: "YAML", parser: "PyYAML", files: count((p) => /\.ya?ml$/.test(p)) },
  { kind: "Terraform", parser: "python-hcl2", files: count((p) => p.endsWith(".tf")) },
  { kind: "JSON", parser: "json", files: count((p) => p.endsWith(".json")) },
  { kind: "TOML", parser: "tomllib", files: count((p) => p.endsWith(".toml")) },
  { kind: "Dockerfile", parser: "policy rules", files: count((p) => p.endsWith("Dockerfile")) },
];

function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cx("font-mono text-[13px] leading-5 tracking-normal", className)}>{children}</span>;
}

const TILES: { title: ReactNode; visual: ReactNode }[] = [
  {
    title: (
      <>
        Five specialists.
        <br />
        One typed handoff each.
      </>
    ),
    visual: (
      <ol className="w-full max-w-[520px] space-y-2.5">
        {AGENTS.map((agent, index) => (
          <li
            key={agent.name}
            className="flex items-center gap-4 rounded-2xl bg-canvas/70 px-4 py-3 ring-1 ring-hairline md:px-5"
          >
            <agent.icon className="size-5 shrink-0 text-ink-2" strokeWidth={1.75} aria-hidden />
            <span className="text-[14px] leading-5 font-medium text-ink md:text-[17px]">{agent.label}</span>
            <Mono className="ml-auto text-accent-ink">{ARTIFACTS[index]}</Mono>
          </li>
        ))}
      </ol>
    ),
  },
  {
    title: (
      <>
        Parsed with real parsers.
        <br />
        Not pattern-matched.
      </>
    ),
    visual: (
      <table className="w-full max-w-[520px] text-left">
        <caption className="sr-only">Parsers used on the library example’s files</caption>
        <thead>
          <tr className="text-[12px] leading-4 text-ink-2">
            <th scope="col" className="pb-3 font-normal">
              Format
            </th>
            <th scope="col" className="pb-3 font-normal">
              Checked with
            </th>
            <th scope="col" className="pb-3 text-right font-normal">
              Files in the example
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-hairline">
          {PARSERS.map((row) => (
            <tr key={row.kind}>
              <td className="py-2.5 pr-3 text-[14px] leading-6 text-ink md:text-[17px]">{row.kind}</td>
              <td className="py-2.5 pr-3">
                <Mono className="text-accent-ink">{row.parser}</Mono>
              </td>
              <td className="py-2.5 text-right text-[17px] tabular-nums text-ink">{row.files}</td>
            </tr>
          ))}
        </tbody>
      </table>
    ),
  },
  {
    title: (
      <>
        Claude when you want it.
        <br />
        Offline when you don’t.
      </>
    ),
    visual: (
      <div className="grid w-full max-w-[560px] gap-3 sm:grid-cols-2">
        {[
          {
            name: "anthropic",
            model: "claude-opus-5-5",
            points: [
              "Structured outputs from each artifact’s schema",
              "Adaptive thinking, per-agent effort",
              "Streamed, with typed errors",
            ],
          },
          {
            name: "offline",
            model: sample.provider.model,
            points: [
              "Deterministic heuristics and templates",
              "No API key, no network",
              "Reports zero tokens, honestly",
            ],
          },
        ].map((provider) => (
          <div key={provider.name} className="rounded-2xl bg-canvas/70 p-5 ring-1 ring-hairline">
            <Mono className="text-ink-2">provider</Mono>
            <p className="mt-1 text-[21px] leading-[25px] font-semibold text-ink">{provider.name}</p>
            <Mono className="mt-1 block text-accent-ink">{provider.model}</Mono>
            <ul className="mt-4 space-y-1.5 text-[14px] leading-5 text-ink-2">
              {provider.points.map((point) => (
                <li key={point}>{point}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    ),
  },
  {
    title: (
      <>
        Watch it work.
        <br />
        Resume where you left off.
      </>
    ),
    visual: (
      <div className="w-full max-w-[560px] rounded-2xl bg-canvas/70 p-5 ring-1 ring-hairline">
        <Mono className="text-ink-2">GET /api/v1/jobs/&#123;id&#125;/events · text/event-stream</Mono>
        <ol className="mt-4 space-y-2">
          {[
            ["job.started", `${sample.provider.name} · ${sample.provider.model}`],
            ["stage.started", sample.stages[0]!.label],
            ["stage.log", sample.stages[0]!.logs[0]!],
            ["stage.completed", sample.stages[0]!.summary],
            ["stage.started", sample.stages[1]!.label],
            ["…", ""],
            ["job.succeeded", ""],
          ].map(([event, text], index) => (
            <li key={index} className="flex gap-3">
              <Mono className="w-[124px] shrink-0 text-accent-ink">{event}</Mono>
              <Mono className="min-w-0 truncate text-ink">{text}</Mono>
            </li>
          ))}
        </ol>
        <p className="mt-4 text-[14px] leading-5 text-ink-2">
          Every event is stored first, then streamed. Reconnect with <Mono>Last-Event-ID</Mono> and the log
          replays from there.
        </p>
      </div>
    ),
  },
  {
    title: (
      <>
        Security is a check.
        <br />
        Not a hope.
      </>
    ),
    visual: (
      <div className="w-full max-w-[560px]">
        <p className="mb-3 text-[14px] leading-5 text-ink-2">Checked in every generated project:</p>
        <ul className="grid gap-2.5 sm:grid-cols-2">
          {[
            ["Root users", "Dockerfile and Kubernetes"],
            ["Privileged containers", "Kubernetes"],
            ["Unpinned base images", "Dockerfile"],
            ["Unpinned actions", "GitHub Actions"],
            ["Hard-coded credentials", "AWS, Anthropic and GitHub keys, private keys"],
            ["Unsafe paths", "Absolute paths, “..” and .git in any letter case"],
          ].map(([rule, scope]) => (
            <li key={rule} className="rounded-2xl bg-canvas/70 px-4 py-3 ring-1 ring-hairline">
              <p className="text-[17px] leading-6 font-medium text-ink">{rule}</p>
              <p className="text-[12px] leading-4 text-ink-2">{scope}</p>
            </li>
          ))}
        </ul>
      </div>
    ),
  },
];

/** "Get the highlights." A paged carousel with a visible pause control, like apple.com's. */
export function Highlights() {
  const track = useRef<HTMLUListElement>(null);
  const section = useRef<HTMLElement>(null);
  const [index, setIndex] = useState(0);
  const reduce = useReducedMotion();
  // null until the reader presses play or pause: autoplay by default, not with reduced motion.
  const [choice, setPlaying] = useState<boolean | null>(null);
  const playing = choice ?? !reduce;
  const [inView, setInView] = useState(false);
  const [hovering, setHovering] = useState(false);

  const go = useCallback(
    (target: number) => {
      const element = track.current;
      const tile = element?.children[target] as HTMLElement | undefined;
      if (!element || !tile) return;
      element.scrollTo({ left: tile.offsetLeft - element.offsetLeft, behavior: reduce ? "auto" : "smooth" });
    },
    [reduce],
  );

  // Keep the pager in sync with manual swipes and scrolls.
  useEffect(() => {
    const element = track.current;
    if (!element) return;
    const onScroll = () => {
      const tiles = [...element.children] as HTMLElement[];
      const left = element.scrollLeft;
      let nearest = 0;
      tiles.forEach((tile, i) => {
        const current = tiles[nearest]!;
        if (
          Math.abs(tile.offsetLeft - element.offsetLeft - left) <
          Math.abs(current.offsetLeft - element.offsetLeft - left)
        )
          nearest = i;
      });
      setIndex(nearest);
    };
    element.addEventListener("scroll", onScroll, { passive: true });
    return () => element.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const element = section.current;
    if (!element || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => setInView(Boolean(entry?.isIntersecting)), {
      threshold: 0.35,
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Autoplay only while visible, not hovered, and not paused.
  useEffect(() => {
    if (!playing || !inView || hovering) return;
    const timer = window.setTimeout(() => go((index + 1) % TILES.length), AUTOPLAY_MS);
    return () => window.clearTimeout(timer);
  }, [playing, inView, hovering, index, go]);

  return (
    <section
      ref={section}
      aria-labelledby="highlights-title"
      className="overflow-hidden bg-surface-2 py-20 md:py-32"
    >
      <div className="wrap" data-reveal>
        <h2 id="highlights-title" className="t-section text-ink">
          Get the highlights.
        </h2>
      </div>
      <div
        className="mt-10 md:mt-14"
        onPointerEnter={() => setHovering(true)}
        onPointerLeave={() => setHovering(false)}
        onFocus={() => setHovering(true)}
        onBlur={() => setHovering(false)}
      >
        <ul
          ref={track}
          aria-label="Highlights"
          tabIndex={0}
          aria-roledescription="carousel"
          className="no-scrollbar flex snap-x snap-mandatory gap-5 overflow-x-auto scroll-px-[max(20px,calc((100vw-980px)/2))] px-[max(20px,calc((100vw-980px)/2))]"
        >
          {TILES.map((tile, i) => (
            <li
              key={i}
              aria-roledescription="slide"
              aria-label={`${i + 1} of ${TILES.length}`}
              className="relative flex min-h-[540px] w-[min(86vw,980px)] shrink-0 snap-start flex-col rounded-[28px] bg-surface p-7 shadow-[var(--shadow-tile)] md:min-h-[600px] md:p-12"
            >
              <h3 className="t-lead max-w-[560px] text-ink">{tile.title}</h3>
              <div className="flex flex-1 items-center justify-center pt-8">{tile.visual}</div>
            </li>
          ))}
        </ul>
        <div className="mt-8 flex items-center justify-center gap-3">
          <div className="flex h-14 items-center gap-1 rounded-full bg-surface px-3 shadow-[var(--shadow-tile)]">
            {TILES.map((_, i) => (
              <button
                key={i}
                type="button"
                aria-label={`Show highlight ${i + 1}`}
                aria-current={index === i ? "true" : undefined}
                onClick={() => go(i)}
                className="grid h-11 place-items-center px-1.5"
              >
                <span
                  className={cx(
                    "block h-2 rounded-full transition-[width,background-color] duration-300 ease-apple",
                    index === i ? "w-12 bg-ink" : "w-2 bg-ink/40 hover:bg-ink/60",
                  )}
                />
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => setPlaying(!playing)}
            aria-label={playing ? "Pause highlights" : "Play highlights"}
            className="grid size-14 place-items-center rounded-full bg-surface text-ink shadow-[var(--shadow-tile)] transition-colors hover:bg-raised"
          >
            {playing ? <Pause className="size-5 fill-current" /> : <Play className="size-5 fill-current" />}
          </button>
        </div>
      </div>
    </section>
  );
}
