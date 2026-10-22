import { useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import { BookOpen, Home, Moon, Plus, Search, Sun } from "lucide-react";
import { Dialog } from "radix-ui";
import type { ReactNode } from "react";
import { useNavigate } from "react-router";

import { api } from "../lib/api";
import { formatRelative } from "../lib/format";
import { useTheme } from "../lib/theme";
import { StatusDot } from "./StatusBadge";

function Item({
  children,
  onSelect,
  value,
  icon,
  hint,
}: {
  children: ReactNode;
  onSelect: () => void;
  value: string;
  icon: ReactNode;
  hint?: string;
}) {
  return (
    <Command.Item
      value={value}
      onSelect={onSelect}
      className="flex min-h-11 cursor-pointer items-center gap-3 rounded-xl px-3 text-[14px] leading-5 tracking-[-0.016em] text-ink data-[selected=true]:bg-accent-soft"
    >
      <span className="grid size-5 shrink-0 place-items-center text-ink-2 [&_svg]:size-4">{icon}</span>
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {hint && <span className="shrink-0 text-[12px] text-ink-2">{hint}</span>}
    </Command.Item>
  );
}

const GROUP =
  "px-1 [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:pb-1.5 [&_[cmdk-group-heading]]:text-[12px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:text-ink-2";

/** Command menu (Cmd/Ctrl+K): new job, open a recent job, switch appearance, jump to docs. */
export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const navigate = useNavigate();
  const [theme, toggleTheme] = useTheme();
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: () => api.listJobs(), enabled: open });

  const run = (action: () => void) => {
    onOpenChange(false);
    action();
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/35 backdrop-blur-[2px] data-[state=open]:animate-[fade-in_160ms_ease-out]" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed top-[12vh] left-1/2 z-50 w-[min(36rem,calc(100vw-2rem))] -translate-x-1/2 overflow-hidden rounded-[20px] bg-raised text-ink shadow-[0_24px_80px_rgb(0_0_0/0.35)] ring-1 ring-hairline outline-none data-[state=open]:animate-[palette-in_200ms_cubic-bezier(0.25,0.1,0.25,1)]"
        >
          <Dialog.Title className="sr-only">Command menu</Dialog.Title>
          <Command label="Command menu" loop>
            <div className="flex items-center gap-3 border-b border-hairline px-4">
              <Search className="size-4 shrink-0 text-ink-2" aria-hidden />
              <Command.Input
                placeholder="Search jobs and commands"
                className="h-14 w-full bg-transparent text-[17px] tracking-[-0.022em] text-ink outline-none placeholder:text-ink-2"
              />
            </div>
            <Command.List className="scrollbar-thin max-h-[min(60vh,420px)] overflow-y-auto pb-2">
              <Command.Empty className="px-4 py-8 text-center text-[14px] text-ink-2">
                No matches.
              </Command.Empty>
              <Command.Group heading="Actions" className={GROUP}>
                <Item
                  value="new project job"
                  icon={<Plus />}
                  onSelect={() => run(() => void navigate("/app"))}
                >
                  New project
                </Item>
                <Item
                  value="toggle theme appearance dark light"
                  icon={theme === "dark" ? <Sun /> : <Moon />}
                  onSelect={() => run(toggleTheme)}
                >
                  {theme === "dark" ? "Switch to light appearance" : "Switch to dark appearance"}
                </Item>
                <Item
                  value="overview landing home"
                  icon={<Home />}
                  onSelect={() => run(() => void navigate("/"))}
                >
                  Product overview
                </Item>
                <Item
                  value="api docs openapi"
                  icon={<BookOpen />}
                  onSelect={() => run(() => window.location.assign("/api/docs"))}
                >
                  API docs
                </Item>
              </Command.Group>
              {jobs.data && jobs.data.items.length > 0 && (
                <Command.Group heading="Recent jobs" className={GROUP}>
                  {jobs.data.items.slice(0, 12).map((job) => (
                    <Item
                      key={job.id}
                      value={`${job.project_name ?? ""} ${job.title} ${job.id}`}
                      icon={<StatusDot status={job.status} />}
                      hint={formatRelative(job.created_at)}
                      onSelect={() => run(() => void navigate(`/app/jobs/${job.id}`))}
                    >
                      {job.project_name ?? job.title}
                    </Item>
                  ))}
                </Command.Group>
              )}
            </Command.List>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
