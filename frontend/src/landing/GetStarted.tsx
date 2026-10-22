import { ArrowRight, Check, Copy } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { toast } from "../lib/toast";

import { pill } from "../lib/pill";
import { REPO_URL } from "./links";

/** Commands copied from the README's Quickstart, unchanged. */
const WAYS: { title: string; detail: string; command: string }[] = [
  {
    title: "Docker",
    detail: "PostgreSQL and the app. Opens on localhost:8000, offline unless you pass ANTHROPIC_API_KEY.",
    command: "docker compose up --build",
  },
  {
    title: "Local",
    detail: "Python 3.12+, uv and Node 24. The UI is served by FastAPI from frontend/dist.",
    command: [
      "uv sync",
      "(cd frontend && npm ci && npm run build)",
      "cp .env.example .env",
      "uv run autoswe serve",
    ].join("\n"),
  },
  {
    title: "Command line",
    detail: "Runs the whole pipeline once and writes the project to a folder.",
    command:
      'uv run autoswe generate "A REST API for a small library where members borrow books and librarians track loans with due dates." --out ./library-api',
  },
];

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      toast.success("Copied to clipboard");
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      toast.error("Copy failed. Select the text and copy it instead.");
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      aria-label={label}
      className="inline-grid size-9 shrink-0 place-items-center rounded-full text-ink-2 transition-colors hover:bg-ink/[0.07] hover:text-ink pointer-coarse:size-11"
    >
      {copied ? <Check className="size-4 text-ok" aria-hidden /> : <Copy className="size-4" aria-hidden />}
    </button>
  );
}

export function GetStarted() {
  return (
    <section id="get-started" aria-labelledby="start-title" className="bg-canvas py-24 md:py-40">
      <div className="wrap">
        <div className="text-center" data-reveal>
          <h2 id="start-title" className="t-headline text-ink">
            Run it yourself.
          </h2>
          <p className="t-body-lg mx-auto mt-5 max-w-[640px] text-ink-2">
            No API key is needed to try it. Add one and the same pipeline runs on Claude.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-4">
            <Link to="/app" className={pill("primary")}>
              Open the app
            </Link>
            <a href={REPO_URL} className={pill("secondary")}>
              View on GitHub <ArrowRight className="size-4" aria-hidden />
            </a>
          </div>
        </div>

        <ul className="mt-16 grid gap-3 lg:grid-cols-3" data-reveal>
          {WAYS.map((way) => (
            <li key={way.title} className="flex flex-col rounded-[20px] bg-surface-2 p-6">
              <h3 className="text-[21px] leading-[25px] font-semibold tracking-[0.011em] text-ink">
                {way.title}
              </h3>
              <p className="mt-2 flex-1 text-[14px] leading-5 text-ink-2">{way.detail}</p>
              <div className="mt-5 flex items-start gap-2 rounded-xl bg-code p-3 ring-1 ring-hairline">
                {/* Wrapped, never clipped: continuation lines hang by two characters. */}
                <pre className="min-w-0 flex-1 py-1.5 pl-1 font-mono text-[12px] leading-5 text-ink">
                  <code className="block">
                    {way.command.split("\n").map((line) => (
                      <span
                        key={line}
                        className="block pl-[2ch] -indent-[2ch] whitespace-pre-wrap [overflow-wrap:anywhere]"
                      >
                        {line}
                      </span>
                    ))}
                  </code>
                </pre>
                <CopyButton text={way.command} label={`Copy the ${way.title} command`} />
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
