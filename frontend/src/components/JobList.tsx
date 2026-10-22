import { useQuery } from "@tanstack/react-query";
import { FolderGit2 } from "lucide-react";
import { NavLink } from "react-router";

import { api, ApiError } from "../lib/api";
import { formatRelative } from "../lib/format";
import { TERMINAL } from "../lib/types";
import { StatusDot } from "./StatusBadge";
import { Skeleton } from "./ui";
import { cx } from "../lib/cx";

export function JobList() {
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.listJobs(),
    refetchInterval: (query) =>
      query.state.data?.items.some((job) => !TERMINAL.has(job.status)) ? 3000 : 15_000,
  });

  if (jobs.isPending) {
    return (
      <div className="space-y-2 px-2" aria-busy>
        {Array.from({ length: 5 }, (_, i) => (
          <Skeleton key={i} className="h-12" />
        ))}
      </div>
    );
  }
  if (jobs.isError) {
    const unauthorised = jobs.error instanceof ApiError && jobs.error.status === 401;
    return (
      <p className="px-3 py-2 text-xs text-zinc-500">
        {unauthorised ? "Add your API key to see your jobs." : "Could not load jobs."}
      </p>
    );
  }
  if (jobs.data.items.length === 0) {
    return (
      <div className="mx-2 rounded-lg border border-dashed border-zinc-300 px-4 py-6 text-center dark:border-zinc-700">
        <FolderGit2 className="mx-auto mb-2 size-5 text-zinc-400" aria-hidden />
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          No jobs yet. Describe a project to get started.
        </p>
      </div>
    );
  }

  return (
    <nav aria-label="Recent jobs">
      <ul className="space-y-0.5">
        {jobs.data.items.map((job) => (
          <li key={job.id}>
            <NavLink
              to={`/jobs/${job.id}`}
              className={({ isActive }) =>
                cx(
                  "group block rounded-lg px-3 py-2 transition-colors",
                  isActive
                    ? "bg-brand-50 ring-1 ring-brand-200 dark:bg-brand-950/40 dark:ring-brand-900"
                    : "hover:bg-zinc-100 dark:hover:bg-zinc-800/60",
                )
              }
            >
              <div className="flex items-center gap-2">
                <StatusDot status={job.status} />
                <span className="truncate text-sm font-medium text-zinc-800 dark:text-zinc-200">
                  {job.project_name ?? job.title}
                </span>
              </div>
              <div className="mt-0.5 flex items-center gap-1.5 pl-4 text-[11px] text-zinc-500 dark:text-zinc-500">
                <span>{formatRelative(job.created_at)}</span>
                <span aria-hidden>·</span>
                <span className="truncate">{job.provider}</span>
                {!TERMINAL.has(job.status) && (
                  <span className="ml-auto tabular-nums text-brand-600 dark:text-brand-400">
                    {Math.round(job.progress * 100)}%
                  </span>
                )}
              </div>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
