import { useEffect, useState } from "react";

import { highlight } from "../lib/highlight";
import { sample } from "./sample";
import { CODE_FILES } from "./storyModel";

/** Shiki's output for the story's files, whole and split into lines, once highlighted in the worker. */
export function useHighlightedLines(target: React.RefObject<HTMLElement | null>) {
  const [lines, setLines] = useState<Record<string, string[]>>({});
  const [whole, setWhole] = useState<Record<string, string>>({});
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
              if (cancelled) return;
              setLines((previous) => ({ ...previous, [path]: rendered }));
              setWhole((previous) => ({ ...previous, [path]: html }));
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
  return { lines, html: whole };
}
