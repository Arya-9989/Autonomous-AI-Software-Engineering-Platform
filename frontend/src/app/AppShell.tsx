import { useQuery } from "@tanstack/react-query";
import { BookOpen, KeyRound, Menu, Moon, Plus, Search, Sun, X } from "lucide-react";
import { LazyMotion, MotionConfig } from "motion/react";
import { Dialog } from "radix-ui";
import { lazy, Suspense, useEffect, useState, useSyncExternalStore } from "react";
import { Link, Route, Routes, useLocation } from "react-router";

import { Logo } from "../components/Logo";
import { Badge, IconButton } from "../components/ui";
import { api, getApiKey } from "../lib/api";
import { cx } from "../lib/cx";
import { whenIdle } from "../lib/idle";
import { useTheme } from "../lib/theme";
import { ApiKeyDialog } from "./ApiKeyDialog";
import { JobList } from "./JobList";
import { JobPage } from "./JobPage";
import { NewJobPage } from "./NewJobPage";
import { NotFound } from "./NotFound";

// The shortcut label depends on the platform; the prerendered HTML says Ctrl K and hydration
// switches it on Apple devices without a mismatch.
const noSubscription = () => () => {};
const detectMac = () => /Mac|iPhone|iPad/.test(navigator.platform);

// The command menu (cmdk) is code nobody needs for the first paint: fetched when the page is
// idle, rendered from the first time it opens.
const loadPalette = () => import("./CommandPalette");
const CommandPalette = lazy(() => loadPalette().then((module) => ({ default: module.CommandPalette })));

const loadMotionFeatures = () => import("../lib/motionFeatures").then((module) => module.default);

function Sidebar() {
  return (
    <div className="flex h-full flex-col">
      <div className="p-4">
        <Link
          to="/app"
          className="flex h-10 w-full items-center justify-center gap-2 rounded-full bg-accent text-[14px] tracking-[-0.016em] text-white transition-colors duration-200 hover:bg-accent-hover pointer-coarse:h-11"
        >
          <Plus className="size-4" aria-hidden /> New project
        </Link>
      </div>
      <h2 className="px-5 pt-1 pb-2 text-[12px] leading-4 font-semibold tracking-[-0.01em] text-ink-2">
        Recent jobs
      </h2>
      <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        <JobList />
      </div>
    </div>
  );
}

export default function AppShell({ notFound = false }: { notFound?: boolean }) {
  const [theme, toggleTheme] = useTheme();
  const [drawerPath, setDrawerPath] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Stays mounted after the first open, so closing animates like any dialog.
  const [paletteUsed, setPaletteUsed] = useState(false);
  const openPalette = () => {
    setPaletteUsed(true);
    setPaletteOpen(true);
  };
  useEffect(() => whenIdle(() => void loadPalette()), []);
  const [keyDialog, setKeyDialog] = useState<"open" | "closed" | "auto">("auto");
  const location = useLocation();
  const info = useQuery({ queryKey: ["info"], queryFn: api.info, staleTime: 60_000 });
  const defaultProvider = info.data?.providers.find((p) => p.default);

  // The drawer belongs to the page it was opened on, so navigating closes it.
  const drawer = drawerPath === location.pathname;
  const setDrawer = (open: boolean) => setDrawerPath(open ? location.pathname : null);
  // Prompt for a key once when the server needs one and none is stored.
  const needsKey = Boolean(info.data?.auth_required && !getApiKey());
  const keyDialogOpen = keyDialog === "open" || (keyDialog === "auto" && needsKey);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteUsed(true);
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const isMac = useSyncExternalStore(noSubscription, detectMac, () => false);

  return (
    <LazyMotion features={loadMotionFeatures} strict>
      <MotionConfig reducedMotion="user">
        <div className="flex min-h-dvh flex-col bg-surface-2 dark:bg-canvas">
          <a
            href="#main"
            className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-full focus:bg-accent focus:px-4 focus:py-2 focus:text-white"
          >
            Skip to content
          </a>
          <header className="sticky top-0 z-30 border-b border-hairline bg-[var(--nav-bg)] backdrop-blur-xl backdrop-saturate-[1.8]">
            <div className="flex h-12 items-center gap-2 px-3 sm:px-4">
              <IconButton label="Show jobs" tip="start" className="lg:hidden" onClick={() => setDrawer(true)}>
                <Menu className="size-5" />
              </IconButton>
              <Logo to="/" className="px-1" />
              <span className="hidden text-[12px] text-ink-2 sm:inline" aria-hidden>
                /
              </span>
              <Link
                to="/app"
                className="hidden min-h-11 items-center px-1 text-[14px] tracking-[-0.016em] text-ink-2 hover:text-ink sm:inline-flex"
              >
                Studio
              </Link>

              <button
                type="button"
                onClick={openPalette}
                className="ml-auto flex h-9 min-w-11 items-center gap-2 rounded-full bg-surface px-3 text-[14px] tracking-[-0.016em] text-ink-2 ring-1 ring-hairline transition-colors hover:text-ink hover:ring-hairline-strong pointer-coarse:h-11 md:ml-4 md:w-72"
                aria-keyshortcuts={isMac ? "Meta+K" : "Control+K"}
              >
                <Search className="size-4 shrink-0" aria-hidden />
                <span className="sr-only md:not-sr-only">Search jobs and commands</span>
                <kbd
                  aria-hidden
                  className="ml-auto hidden rounded-md bg-canvas px-1.5 py-0.5 font-mono text-[11px] text-ink-2 shadow-[0_0_0_1px_var(--hairline)] md:inline"
                >
                  {isMac ? "⌘K" : "Ctrl K"}
                </kbd>
              </button>

              <div className="ml-auto flex items-center gap-1 md:ml-auto">
                {defaultProvider && (
                  <span className="mr-1 hidden xl:block">
                    <Badge tone="brand">
                      {defaultProvider.name === "offline"
                        ? "Offline provider"
                        : `Claude · ${defaultProvider.model}`}
                    </Badge>
                  </span>
                )}
                {info.data?.auth_required && (
                  <IconButton label="Set API key" tip="end" onClick={() => setKeyDialog("open")}>
                    <KeyRound className="size-4" />
                  </IconButton>
                )}
                <IconButton
                  tip="end"
                  label={theme === "dark" ? "Switch to light appearance" : "Switch to dark appearance"}
                  onClick={toggleTheme}
                >
                  {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
                </IconButton>
                <a
                  href="/api/docs"
                  className="hidden min-h-11 items-center gap-1.5 rounded-full px-3 text-[14px] tracking-[-0.016em] text-ink-2 hover:text-ink sm:inline-flex"
                >
                  <BookOpen className="size-4" aria-hidden /> API
                </a>
              </div>
            </div>
          </header>

          <div className="flex min-h-0 flex-1">
            <aside
              aria-label="Jobs"
              className="hidden w-72 shrink-0 border-r border-hairline bg-surface-3 lg:block"
            >
              <div className="sticky top-12 h-[calc(100dvh-3rem)]">
                <Sidebar />
              </div>
            </aside>

            <Dialog.Root open={drawer} onOpenChange={setDrawer}>
              <Dialog.Portal>
                <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40 backdrop-blur-[2px] data-[state=open]:animate-[fade-in_200ms_ease-out] lg:hidden" />
                <Dialog.Content
                  className="fixed inset-y-0 left-0 z-50 w-80 max-w-[85vw] bg-canvas shadow-2xl outline-none data-[state=open]:animate-[slide-in_280ms_cubic-bezier(0.25,0.1,0.25,1)] lg:hidden"
                  aria-describedby={undefined}
                >
                  <div className="flex h-12 items-center justify-between border-b border-hairline px-3">
                    <Dialog.Title className="sr-only">Jobs</Dialog.Title>
                    <Logo to="/" className="px-1" />
                    {/* No tooltip here: the dialog focuses this button on open. */}
                    <Dialog.Close
                      aria-label="Close jobs"
                      className="inline-grid size-11 place-items-center rounded-full text-ink-2 hover:bg-ink/[0.07] hover:text-ink"
                    >
                      <X className="size-5" aria-hidden />
                    </Dialog.Close>
                  </div>
                  <div className="h-[calc(100%-3rem)]">
                    <Sidebar />
                  </div>
                </Dialog.Content>
              </Dialog.Portal>
            </Dialog.Root>

            <main id="main" tabIndex={-1} className={cx("min-w-0 flex-1 outline-none")}>
              {notFound ? (
                <NotFound />
              ) : (
                <Routes>
                  <Route index element={<NewJobPage />} />
                  <Route path="jobs/:jobId" element={<JobPage />} />
                  <Route path="*" element={<NotFound />} />
                </Routes>
              )}
            </main>
          </div>

          <ApiKeyDialog open={keyDialogOpen} onClose={() => setKeyDialog("closed")} />
          {(paletteOpen || paletteUsed) && (
            <Suspense fallback={null}>
              <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
            </Suspense>
          )}
        </div>
      </MotionConfig>
    </LazyMotion>
  );
}
