import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  ClipboardList,
  Download,
  FileSearch,
  FolderTree,
  ShieldCheck,
  Square,
  Trash2,
  XCircle,
} from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ActivityLog } from "../components/ActivityLog";
import { ArchitectureView } from "../components/ArchitectureView";
import { FileExplorer } from "../components/FileExplorer";
import { ReviewView } from "../components/ReviewView";
import { SpecView } from "../components/SpecView";
import { StageTracker } from "../components/StageTracker";
import { StatusBadge } from "../components/StatusBadge";
import { Button, Card, EmptyState, ErrorState, Skeleton, Tabs } from "../components/ui";
import { useJobEvents } from "../hooks/useJobEvents";
import { api, ApiError } from "../lib/api";
import { formatDuration, formatRelative, formatTokens } from "../lib/format";
import { TERMINAL } from "../lib/types";

type Tab = "files" | "architecture" | "review" | "spec";

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-zinc-500">{label}</dt>
      <dd className="truncate text-sm font-medium tabular-nums text-zinc-800 dark:text-zinc-200">{value}</dd>
    </div>
  );
}

export function JobPage() {
  const { jobId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("files");
  const [focus, setFocus] = useState<string | null>(null);

  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.getJob(jobId),
    // SSE drives updates; polling is only a safety net.
    refetchInterval: (query) => (query.state.data && !TERMINAL.has(query.state.data.status) ? 5000 : false),
  });
  const { events, status: streamStatus } = useJobEvents(jobId);

  const cancel = useMutation({
    mutationFn: () => api.cancelJob(jobId),
    onSuccess: (data) => queryClient.setQueryData(["job", jobId], data),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteJob(jobId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      void navigate("/");
    },
  });
  const download = useMutation({
    mutationFn: () => api.downloadArchive(jobId, `${job.data?.project_name ?? "project"}.zip`),
  });

  if (job.isPending) {
    return (
      <div className="mx-auto max-w-7xl space-y-4 px-4 py-6 sm:px-6">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-36" />
        <Skeleton className="h-96" />
      </div>
    );
  }
  if (job.isError) {
    if (job.error instanceof ApiError && job.error.status === 404) {
      return (
        <EmptyState
          icon={<FileSearch className="size-6" />}
          title="Job not found"
          action={
            <Link to="/" className="text-sm font-medium text-brand-600 hover:underline dark:text-brand-400">
              Start a new project
            </Link>
          }
        >
          It may have been deleted, or it belongs to a different API key.
        </EmptyState>
      );
    }
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <ErrorState error={job.error} onRetry={() => void job.refetch()} />
      </div>
    );
  }

  const data = job.data;
  const running = !TERMINAL.has(data.status);
  const totalDuration =
    data.started_at && data.finished_at
      ? (new Date(data.finished_at).getTime() - new Date(data.started_at).getTime()) / 1000
      : null;
  const openFile = (path: string) => {
    setFocus(path);
    setTab("files");
  };

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:py-8">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={data.status} />
            {data.project_name && (
              <span className="font-mono text-xs text-zinc-500 dark:text-zinc-400">{data.project_name}</span>
            )}
          </div>
          <h1 className="mt-2 text-xl font-semibold tracking-tight text-pretty text-zinc-900 sm:text-2xl dark:text-white">
            {data.title}
          </h1>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {running ? (
            <Button
              variant="danger"
              icon={<Square className="size-3.5" />}
              loading={cancel.isPending}
              disabled={data.cancel_requested}
              onClick={() => cancel.mutate()}
            >
              {data.cancel_requested ? "Cancelling…" : "Cancel"}
            </Button>
          ) : (
            <Button
              variant="ghost"
              icon={<Trash2 className="size-4" />}
              loading={remove.isPending}
              onClick={() => {
                if (window.confirm("Delete this job and its generated files?")) remove.mutate();
              }}
            >
              Delete
            </Button>
          )}
          <Button
            variant="primary"
            icon={<Download className="size-4" />}
            disabled={data.status !== "succeeded"}
            loading={download.isPending}
            onClick={() => download.mutate()}
          >
            Download .zip
          </Button>
        </div>
      </div>

      {(cancel.isError || remove.isError || download.isError) && (
        <div className="mt-4">
          <ErrorState error={cancel.error ?? remove.error ?? download.error} />
        </div>
      )}

      {data.status === "failed" && data.error && (
        <div
          role="alert"
          className="mt-4 flex gap-3 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300"
        >
          <XCircle className="size-5 shrink-0" aria-hidden />
          <p>{data.error}</p>
        </div>
      )}

      <div className="mt-6 grid gap-5 lg:grid-cols-3">
        <Card className="p-5 lg:col-span-2">
          <StageTracker stages={data.stages} />
          <dl className="mt-6 grid grid-cols-2 gap-4 border-t border-zinc-100 pt-4 sm:grid-cols-4 dark:border-zinc-800">
            <Meta
              label="Provider"
              value={
                data.provider === "offline" ? "Offline templates" : `${data.provider} · ${data.model ?? ""}`
              }
            />
            <Meta label="Submitted" value={formatRelative(data.created_at)} />
            <Meta
              label="Duration"
              value={totalDuration !== null ? formatDuration(totalDuration) : running ? "In progress" : "—"}
            />
            <Meta
              label="Tokens"
              value={
                data.input_tokens + data.output_tokens > 0
                  ? `${formatTokens(data.input_tokens)} in · ${formatTokens(data.output_tokens)} out`
                  : "none (offline)"
              }
            />
          </dl>
        </Card>
        {/* The log scrolls inside the height of the stage card instead of stretching the row. */}
        <div className="relative lg:min-h-80">
          <Card className="flex flex-col overflow-hidden lg:absolute lg:inset-0">
            <ActivityLog events={events} status={streamStatus} />
          </Card>
        </div>
      </div>

      <div className="mt-8">
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "files", label: "Files", icon: <FolderTree className="size-4" /> },
            { id: "architecture", label: "Architecture", icon: <Boxes className="size-4" /> },
            {
              id: "review",
              label: "Review",
              icon: <ShieldCheck className="size-4" />,
              count: data.review_score ?? undefined,
            },
            { id: "spec", label: "Requirements", icon: <ClipboardList className="size-4" /> },
          ]}
        />
        <div className="pt-5" role="tabpanel">
          {tab === "files" && <FileExplorer jobId={jobId} focus={focus} running={running} />}
          {tab === "architecture" && <ArchitectureView jobId={jobId} />}
          {tab === "review" && <ReviewView jobId={jobId} onOpenFile={openFile} />}
          {tab === "spec" && <SpecView jobId={jobId} spec={data.spec} />}
        </div>
      </div>
    </div>
  );
}
