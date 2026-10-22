import { useEffect, useId, useState } from "react";

import { useTheme } from "../lib/theme";
import { Skeleton } from "./ui";

interface Rendered {
  key: string;
  svg: string | null;
}

/** Renders a Mermaid diagram; the library (large) is loaded only when a diagram is shown. */
export function Mermaid({ source, label }: { source: string; label: string }) {
  const [theme] = useTheme();
  const [rendered, setRendered] = useState<Rendered | null>(null);
  const id = useId().replace(/[^a-zA-Z0-9]/g, "");
  const key = `${theme}:${source}`;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let svg: string | null;
      try {
        const { default: mermaid } = await import("mermaid");
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "base",
          themeVariables:
            theme === "dark"
              ? {
                  primaryColor: "#1e1b4b",
                  primaryBorderColor: "#6366f1",
                  primaryTextColor: "#e0e7ff",
                  lineColor: "#71717a",
                  fontSize: "13px",
                }
              : {
                  primaryColor: "#eef2ff",
                  primaryBorderColor: "#818cf8",
                  primaryTextColor: "#1e1b4b",
                  lineColor: "#a1a1aa",
                  fontSize: "13px",
                },
          fontFamily: "Inter Variable, ui-sans-serif, system-ui",
          flowchart: { curve: "basis", padding: 12 },
        });
        svg = (await mermaid.render(`mmd${id}${theme}`, source)).svg;
      } catch {
        svg = null; // render failed: show the source instead
      }
      if (!cancelled) setRendered({ key: `${theme}:${source}`, svg });
    })();
    return () => {
      cancelled = true;
    };
  }, [source, theme, id]);

  if (rendered?.key !== key) return <Skeleton className="h-48" />;
  if (rendered.svg === null) {
    return (
      <pre className="overflow-x-auto rounded-lg bg-zinc-50 p-4 font-mono text-xs dark:bg-zinc-950">
        {source}
      </pre>
    );
  }
  return (
    <div
      role="img"
      aria-label={label}
      className="mermaid-host flex justify-center overflow-x-auto"
      dangerouslySetInnerHTML={{ __html: rendered.svg }}
    />
  );
}
