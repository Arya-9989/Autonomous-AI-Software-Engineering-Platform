import { useQuery } from "@tanstack/react-query";
import { FolderGit2, RotateCw } from "lucide-react";
import { NavLink } from "react-router";

import { Skeleton } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { cx } from "../lib/cx";
import { formatRelative } from "../lib/format";
import { TERMINAL } from "../lib/types";
import { StatusDot } from "./StatusBadge";

export function JobList() {
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.listJobs(),
    refetchInterval: (query) =>
      query.state.data?.items.some((job) => !TERMINAL.has(job.status)) ? 3000 : 15_000,
  });

  if (jobs.isPending) {
    return (
      <div className="space-y-1 px-1" aria-busy="true" aria-label="Loading jobs">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="px-3 py-2.5">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="mt-1.5 h-3 w-1/2" />
          </div>
        ))}
      </div>
    );
  }
  if (jobs.isError) {
    const unauthorised = jobs.error instanceof ApiError && jobs.error.status === 401;
    return (
      <div className="px-3 py-2 text-[12px] leading-4 text-ink-2" role="status">
        <p>
          {unauthorised ? "Add your API key (the key icon above) to see your jobs." : "Could not load jobs."}
        </p>
        {!unauthorised && (
          <button
            type="button"
            onClick={() => void jobs.refetch()}
            className="mt-1 inline-flex min-h-11 items-center gap-1.5 font-medium text-accent-ink hover:underline"
          >
            <RotateCw className="size-3.5" aria-hidden /> Try again
          </button>
        )}
      </div>
    );
  }
  if (jobs.data.items.length === 0) {
    return (
      <div className="mx-2 rounded-2xl bg-surface-2 px-4 py-6 text-center">
        <FolderGit2 className="mx-auto mb-2 size-5 text-ink-2" aria-hidden />
        <p className="text-[12px] leading-4 text-ink-2">No jobs yet. Describe a project to start one.</p>
      </div>
    );
  }

  return (
    <nav aria-label="Recent jobs">
      <ul className="space-y-0.5">
        {jobs.data.items.map((job) => (
          <li key={job.id}>
            <NavLink
              to={`/app/jobs/${job.id}`}
              className={({ isActive }) =>
                cx(
                  "group block rounded-xl px-3 py-2 transition-colors duration-200 ease-apple",
                  isActive ? "bg-accent-soft" : "hover:bg-ink/[0.05]",
                )
              }
            >
              <div className="flex items-center gap-2">
                <StatusDot status={job.status} />
                <span className="truncate text-[14px] leading-5 font-medium tracking-[-0.016em] text-ink">
                  {job.project_name ?? job.title}
                </span>
                <span className="sr-only">, {job.status}</span>
              </div>
              <div className="mt-0.5 flex items-center gap-1.5 pl-4 text-[12px] leading-4 text-ink-2">
                <span>{formatRelative(job.created_at)}</span>
                <span aria-hidden>·</span>
                <span className="truncate">{job.provider}</span>
                {!TERMINAL.has(job.status) && (
                  <span className="ml-auto text-accent-ink tabular-nums">
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
