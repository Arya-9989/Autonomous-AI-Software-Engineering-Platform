import { useQuery } from "@tanstack/react-query";
import { KeyRound, Menu, Moon, Plus, Sun, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router";

import { api, getApiKey } from "../lib/api";
import { useTheme } from "../lib/theme";
import { ApiKeyDialog } from "./ApiKeyDialog";
import { JobList } from "./JobList";
import { Badge, Button } from "./ui";
import { cx } from "../lib/cx";

export function Logo() {
  return (
    <Link to="/" className="group flex items-center gap-2.5" aria-label="AutoSWE home">
      <span className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-brand-500 to-violet-500 text-white shadow-sm shadow-brand-500/30">
        <svg viewBox="0 0 32 32" className="size-5" aria-hidden>
          <path
            d="M9 21.5 14 10.5h4l5 11h-3.6l-1-2.4h-4.8l-1 2.4Zm5.6-5.2h2.8L16 12.9Z"
            fill="currentColor"
          />
        </svg>
      </span>
      <span className="leading-tight">
        <span className="block text-sm font-semibold tracking-tight text-zinc-900 dark:text-white">
          AutoSWE
        </span>
        <span className="block text-[11px] text-zinc-500 dark:text-zinc-400">
          Autonomous software engineering
        </span>
      </span>
    </Link>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const [theme, toggleTheme] = useTheme();
  const [drawerPath, setDrawerPath] = useState<string | null>(null);
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

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="p-3">
        <Link
          to="/"
          className="flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-brand-600 text-sm font-medium text-white shadow-sm shadow-brand-600/20 transition-colors hover:bg-brand-500"
        >
          <Plus className="size-4" aria-hidden /> New project
        </Link>
      </div>
      <div className="px-4 pb-2 pt-1 text-[11px] font-semibold uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
        Recent jobs
      </div>
      <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        <JobList />
      </div>
    </div>
  );

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-30 border-b border-zinc-200/80 bg-white/80 backdrop-blur-md dark:border-zinc-800/80 dark:bg-zinc-950/80">
        <div className="flex h-14 items-center gap-3 px-4">
          <button
            type="button"
            className="-ml-1 rounded-md p-1.5 text-zinc-600 hover:bg-zinc-100 lg:hidden dark:text-zinc-300 dark:hover:bg-zinc-800"
            onClick={() => setDrawer(true)}
            aria-label="Open job list"
          >
            <Menu className="size-5" />
          </button>
          <Logo />
          <div className="ml-auto flex items-center gap-1.5">
            {defaultProvider && (
              <span className="hidden sm:block">
                <Badge tone="brand">
                  {defaultProvider.name === "offline"
                    ? "Offline provider"
                    : `Claude · ${defaultProvider.model}`}
                </Badge>
              </span>
            )}
            {info.data?.auth_required && (
              <Button
                variant="ghost"
                size="sm"
                icon={<KeyRound className="size-4" />}
                onClick={() => setKeyDialog("open")}
                aria-label="Set API key"
              >
                <span className="hidden sm:inline">API key</span>
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={toggleTheme}
              aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              icon={theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            />
            <a
              href="/api/docs"
              className="hidden rounded-lg px-3 py-1.5 text-xs font-medium text-zinc-600 hover:bg-zinc-100 sm:inline-block dark:text-zinc-400 dark:hover:bg-zinc-800"
            >
              API docs
            </a>
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-72 shrink-0 border-r border-zinc-200/80 bg-white/50 lg:block dark:border-zinc-800/80 dark:bg-zinc-950/50">
          <div className="sticky top-14 h-[calc(100dvh-3.5rem)]">{sidebar}</div>
        </aside>

        <div
          className={cx("fixed inset-0 z-40 lg:hidden", drawer ? "" : "pointer-events-none")}
          aria-hidden={!drawer}
        >
          <div
            className={cx(
              "absolute inset-0 bg-zinc-950/40 transition-opacity",
              drawer ? "opacity-100" : "opacity-0",
            )}
            onClick={() => setDrawer(false)}
          />
          <div
            className={cx(
              "absolute inset-y-0 left-0 w-80 max-w-[85vw] bg-white shadow-xl transition-transform dark:bg-zinc-950",
              drawer ? "translate-x-0" : "-translate-x-full",
            )}
          >
            <div className="flex h-14 items-center justify-between border-b border-zinc-200 px-4 dark:border-zinc-800">
              <Logo />
              <button
                type="button"
                className="rounded-md p-1.5 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                onClick={() => setDrawer(false)}
                aria-label="Close job list"
              >
                <X className="size-5" />
              </button>
            </div>
            <div className="h-[calc(100%-3.5rem)]">{sidebar}</div>
          </div>
        </div>

        <main className="min-w-0 flex-1">{children}</main>
      </div>

      <ApiKeyDialog open={keyDialogOpen} onClose={() => setKeyDialog("closed")} />
    </div>
  );
}
