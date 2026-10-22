import { ArrowRight } from "lucide-react";
import { Tabs } from "radix-ui";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { pill } from "../lib/pill";
import { cx } from "../lib/cx";
import { formatBytes } from "../lib/format";
import { highlight } from "../lib/highlight";
import { sample } from "./sample";
import { CATEGORIES } from "./categories";

const STAGE_NAME: Record<string, string> = {
  implement: "Code generator",
  devops: "DevOps engineer",
  architect: "Architect",
};

const sizeOf = (path: string) => sample.files.find((f) => f.path === path)?.size ?? 0;
const stageOf = (path: string) => sample.files.find((f) => f.path === path)?.stage ?? "";

/** Highlight the showcase files once the section is near the viewport (Shiki loads lazily). */
function useHighlighted(target: React.RefObject<HTMLElement | null>) {
  const [html, setHtml] = useState<Record<string, string>>({});
  useEffect(() => {
    const element = target.current;
    if (!element) return;
    let cancelled = false;
    const run = () => {
      for (const file of sample.showcase) {
        highlight(file.content, file.language)
          .then((rendered) => !cancelled && setHtml((previous) => ({ ...previous, [file.path]: rendered })))
          .catch(() => undefined); // the plain-text rendering stays
      }
    };
    if (typeof IntersectionObserver === "undefined") {
      run();
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          observer.disconnect();
          run();
        }
      },
      { rootMargin: "800px 0px" },
    );
    observer.observe(element);
    return () => {
      cancelled = true;
      observer.disconnect();
    };
  }, [target]);
  return html;
}

export function Output() {
  const section = useRef<HTMLElement>(null);
  const html = useHighlighted(section);
  const [selected, setSelected] = useState(sample.showcase[0]!.path);

  return (
    <section
      ref={section}
      id="output"
      aria-labelledby="output-title"
      className="theme-dark bg-black py-24 md:py-40"
    >
      <div className="wrap" data-reveal>
        <p className="t-eyebrow text-accent-ink">Output</p>
        <h2 id="output-title" className="t-section mt-3 text-ink">
          Real code. <span className="text-gradient">Unedited.</span>
        </h2>
        <p className="t-body-lg mt-5 max-w-[720px] text-ink-2">
          This is what the offline provider wrote for the library spec, byte for byte. Pick a file to read the
          service, its tests and the infrastructure around it.
        </p>
      </div>

      <div className="wrap-wide mt-12 md:mt-16" data-reveal>
        <Tabs.Root
          value={selected}
          onValueChange={setSelected}
          orientation="vertical"
          className="mx-auto max-w-[1180px] overflow-hidden rounded-[28px] bg-[#0b0b0c] ring-1 ring-white/10 md:grid md:grid-cols-[280px_minmax(0,1fr)] md:rounded-[36px]"
        >
          <div className="border-b border-white/10 md:border-r md:border-b-0">
            <p className="hidden px-6 pt-6 pb-2 t-eyebrow text-ink-2 md:block">
              {sample.requirements.project_name}
            </p>
            <Tabs.List
              aria-label="Generated files"
              className="no-scrollbar flex gap-1 overflow-x-auto p-3 md:flex-col md:overflow-visible md:px-3 md:pt-0 md:pb-6"
            >
              {sample.showcase.map((file) => (
                <Tabs.Trigger
                  key={file.path}
                  value={file.path}
                  className={cx(
                    "group flex shrink-0 flex-col items-start rounded-xl px-3.5 py-2.5 text-left transition-colors duration-200 ease-apple",
                    "data-[state=active]:bg-white/[0.08] data-[state=inactive]:hover:bg-white/[0.04]",
                  )}
                >
                  <span className="font-mono text-[13px] leading-5 text-ink-2 group-data-[state=active]:text-ink">
                    {file.path}
                  </span>
                  <span className="hidden text-[12px] leading-4 text-ink-2 md:block">
                    {STAGE_NAME[stageOf(file.path)]} · {formatBytes(sizeOf(file.path))}
                  </span>
                </Tabs.Trigger>
              ))}
            </Tabs.List>
            <p className="hidden px-6 pb-6 text-[12px] leading-4 text-ink-2 md:block">
              {sample.files.length - sample.showcase.length} more files in the download.
            </p>
          </div>
          {sample.showcase.map((file) => (
            <Tabs.Content key={file.path} value={file.path} className="min-w-0 outline-none">
              <div className="flex items-center gap-3 border-b border-white/10 px-5 py-3.5">
                <span className="truncate font-mono text-[13px] text-ink">{file.path}</span>
                <span className="ml-auto shrink-0 rounded-full bg-white/[0.08] px-2 py-0.5 font-mono text-[12px] leading-4 text-ink-2">
                  {file.language}
                </span>
              </div>
              <div
                className="code-view scrollbar-thin h-[480px] overflow-auto md:h-[600px]"
                tabIndex={0}
                role="region"
                aria-label={`${file.path}, generated source`}
              >
                {html[file.path] ? (
                  <div dangerouslySetInnerHTML={{ __html: html[file.path]! }} />
                ) : (
                  <pre className="px-5 text-ink">{file.content}</pre>
                )}
              </div>
            </Tabs.Content>
          ))}
        </Tabs.Root>
      </div>

      <div className="wrap mt-20 md:mt-28">
        <h3 className="t-tile text-ink" data-reveal>
          What’s in the box.
        </h3>
        <ul className="mt-8 grid grid-cols-2 gap-3 md:grid-cols-4" data-reveal>
          {CATEGORIES.map((category) => (
            <li key={category.name} className="rounded-[20px] bg-surface p-5">
              <p className="text-[40px] leading-[44px] font-semibold text-ink tabular-nums">
                {category.files.length}
              </p>
              <p className="mt-2 text-[17px] leading-6 font-semibold text-ink">{category.name}</p>
              <p className="mt-1 text-[14px] leading-5 text-ink-2">{category.detail}</p>
            </li>
          ))}
        </ul>
        <div className="mt-10 flex flex-wrap items-center gap-4" data-reveal>
          <Link to="/app" className={pill("primary")}>
            Generate your own <ArrowRight className="size-4" aria-hidden />
          </Link>
          <p className="text-[14px] leading-5 text-ink-2">
            Each finished job downloads as a deterministic .zip.
          </p>
        </div>
      </div>
    </section>
  );
}
