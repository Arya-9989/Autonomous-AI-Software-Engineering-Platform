import { ChevronDown, Moon, Sun } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { Logo } from "../components/Logo";
import { IconButton } from "../components/ui";
import { cx } from "../lib/cx";
import { useTheme } from "../lib/theme";
import { REPO_URL, SECTIONS } from "./links";

function GitHubIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden="true" fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

/** Global bar: 44px, translucent, scrolls away like apple.com's. */
export function GlobalNav() {
  const [theme, toggle] = useTheme();
  return (
    <header className="relative z-40 bg-[var(--nav-bg)] backdrop-blur-xl backdrop-saturate-[1.8]">
      <nav aria-label="Global" className="wrap flex h-11 items-center gap-1">
        <Logo className="-ml-1 px-1" wordmark={false} />
        <div className="ml-auto flex items-center gap-1 sm:gap-2">
          <Link
            to="/app"
            className="hidden min-h-11 items-center px-2.5 text-[12px] tracking-[-0.01em] text-ink/80 transition-colors hover:text-ink sm:inline-flex"
          >
            App
          </Link>
          <a
            href="/api/docs"
            className="hidden min-h-11 items-center px-2.5 text-[12px] tracking-[-0.01em] text-ink/80 transition-colors hover:text-ink sm:inline-flex"
          >
            API docs
          </a>
          <a
            href={REPO_URL}
            className="inline-flex min-h-11 items-center gap-1.5 px-2.5 text-[12px] tracking-[-0.01em] text-ink/80 transition-colors hover:text-ink"
          >
            <GitHubIcon className="size-4" />
            <span className="max-sm:sr-only">GitHub</span>
          </a>
          <IconButton
            tip="end"
            label={theme === "dark" ? "Switch to light appearance" : "Switch to dark appearance"}
            onClick={toggle}
          >
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </IconButton>
        </div>
      </nav>
    </header>
  );
}

function useActiveSection(): string {
  const [active, setActive] = useState<string>(SECTIONS[0].id);
  useEffect(() => {
    const elements = SECTIONS.map((s) => document.getElementById(s.id)).filter(
      (element): element is HTMLElement => element !== null,
    );
    if (typeof IntersectionObserver === "undefined" || elements.length === 0) return;
    const visible = new Map<string, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) visible.set(entry.target.id, entry.intersectionRatio);
        // The section nearest the top that is still on screen wins.
        const first = SECTIONS.find((s) => (visible.get(s.id) ?? 0) > 0);
        if (first) setActive(first.id);
      },
      { rootMargin: "-80px 0px -55% 0px", threshold: [0, 0.01, 0.5, 1] },
    );
    elements.forEach((element) => observer.observe(element));
    return () => observer.disconnect();
  }, []);
  return active;
}

interface Indicator {
  x: number;
  width: number;
  /** False for the first placement and after a resize: those snap, section changes slide. */
  slide: boolean;
}

/**
 * P4: one underline that slides (and stretches) from the old section's link to the new one,
 * on a spring. Until the script has measured the links (and without JavaScript) each link draws
 * its own static underline instead, so the active section is always marked.
 */
function useIndicator(list: React.RefObject<HTMLUListElement | null>, active: string) {
  const [indicator, setIndicator] = useState<Indicator | null>(null);
  const shown = useRef<string | null>(null);
  useEffect(() => {
    const element = list.current;
    if (!element) return;
    const place = (slide: boolean) => {
      const link = element.querySelector<HTMLElement>(`a[href="#${active}"]`);
      if (!link || element.offsetWidth === 0) return;
      setIndicator({ x: link.offsetLeft, width: link.offsetWidth, slide });
    };
    place(shown.current !== null && shown.current !== active);
    shown.current = active;
    if (typeof ResizeObserver === "undefined") return;
    // A resize (fonts, breakpoints) re-measures without sliding; the observer's first call,
    // made as it starts observing, is not a resize.
    let first = true;
    const observer = new ResizeObserver(() => {
      if (!first) place(false);
      first = false;
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [list, active]);
  return indicator;
}

/** Sticky product nav: name on the left, section links and one pill CTA on the right. */
export function LocalNav() {
  const active = useActiveSection();
  const [open, setOpen] = useState(false);
  const list = useRef<HTMLUListElement>(null);
  const indicator = useIndicator(list, active);

  return (
    <div className="sticky top-0 z-30">
      <nav
        aria-label="AutoSWE sections"
        className="border-b border-hairline bg-[var(--nav-bg)] backdrop-blur-xl backdrop-saturate-[1.8]"
      >
        <div className="wrap flex h-[52px] items-center gap-4">
          <a href="#overview" className="text-[21px] font-semibold tracking-[0.011em] text-ink">
            AutoSWE
          </a>
          <ul ref={list} className="relative ml-auto hidden items-center gap-6 md:flex">
            {SECTIONS.map((section) => (
              <li key={section.id}>
                <a
                  href={`#${section.id}`}
                  aria-current={active === section.id ? "true" : undefined}
                  className={cx(
                    "relative inline-flex h-[52px] items-center text-[12px] tracking-[-0.01em] transition-colors duration-200",
                    active === section.id ? "text-ink" : "text-ink/75 hover:text-ink",
                  )}
                >
                  {section.label}
                  {!indicator && active === section.id && (
                    <span aria-hidden className="absolute inset-x-0 bottom-0 h-px bg-ink" />
                  )}
                </a>
              </li>
            ))}
            {indicator && (
              <li
                aria-hidden
                data-slide={indicator.slide ? "" : undefined}
                className="nav-indicator pointer-events-none absolute bottom-0 left-0 h-px w-px origin-left bg-ink"
                style={{ transform: `translateX(${indicator.x}px) scaleX(${indicator.width})` }}
              />
            )}
          </ul>
          <button
            type="button"
            className="ml-auto inline-grid size-11 place-items-center rounded-full text-ink-2 hover:text-ink md:hidden"
            aria-expanded={open}
            aria-controls="local-nav-menu"
            aria-label={open ? "Hide sections" : "Show sections"}
            onClick={() => setOpen((value) => !value)}
          >
            <ChevronDown
              className={cx("size-5 transition-transform duration-300 ease-apple", open && "rotate-180")}
              aria-hidden
            />
          </button>
          <Link
            to="/app"
            className="hit-44 inline-flex h-7 items-center rounded-full bg-accent px-3 text-[12px] tracking-[-0.01em] text-white transition-colors duration-200 hover:bg-accent-hover md:ml-2"
          >
            Open app
          </Link>
        </div>
      </nav>
      <div
        id="local-nav-menu"
        hidden={!open}
        className="border-b border-hairline bg-[var(--nav-bg)] backdrop-blur-xl md:hidden"
      >
        <ul className="wrap py-2">
          {SECTIONS.map((section) => (
            <li key={section.id}>
              <a
                href={`#${section.id}`}
                onClick={() => setOpen(false)}
                aria-current={active === section.id ? "true" : undefined}
                className={cx(
                  "flex min-h-11 items-center text-[17px] tracking-[-0.022em]",
                  active === section.id ? "font-semibold text-ink" : "text-ink-2",
                )}
              >
                {section.label}
              </a>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
