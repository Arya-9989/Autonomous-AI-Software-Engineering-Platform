import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileSearch, Square, Trash2, XCircle } from "lucide-react";
import { m } from "motion/react";
import { AlertDialog, Tabs } from "radix-ui";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { toast } from "sonner";

import { ArchitectureView } from "../components/ArchitectureView";
import { FileExplorer } from "../components/FileExplorer";
import { ReviewView } from "../components/ReviewView";
import { SpecView } from "../components/SpecView";
import { Button, Card, EmptyState, ErrorState, Skeleton } from "../components/ui";
import { pill } from "../lib/pill";
import { useJobEvents } from "../hooks/useJobEvents";
import { api, ApiError } from "../lib/api";
import { cx } from "../lib/cx";
import { formatDuration, formatRelative, formatTokens } from "../lib/format";
import { SPRING } from "../lib/motion";
import { TERMINAL } from "../lib/types";
import { ActivityLog } from "./ActivityLog";
import { StageTimeline } from "./StageTimeline";
import { StatusBadge } from "./StatusBadge";

type Tab = "files" | "architecture" | "review" | "spec";

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[12px] leading-4 text-ink-2">{label}</dt>
      <dd className="mt-0.5 truncate text-[14px] leading-5 font-medium text-ink tabular-nums">{value}</dd>
    </div>
  );
}

function JobSkeleton() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:py-10" aria-busy="true" aria-label="Loading job">
      <Skeleton className="h-5 w-24 rounded-full" />
      <Skeleton className="mt-3 h-9 w-2/3 max-w-xl" />
      <div className="mt-8 grid gap-5 lg:grid-cols-3">
        <Card className="p-6 lg:col-span-2">
          <div className="grid grid-cols-5 gap-4">
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} className="flex flex-col items-center gap-3">
                <Skeleton className="size-12 rounded-full md:size-14" />
                <Skeleton className="h-3 w-12" />
              </div>
            ))}
          </div>
          <Skeleton className="mt-8 h-20" />
        </Card>
        <Card className="p-5">
          <Skeleton className="h-4 w-20" />
          <Skeleton className="mt-5 h-3.5" />
          <Skeleton className="mt-2 h-3.5 w-3/4" />
          <Skeleton className="mt-5 h-3.5" />
        </Card>
      </div>
    </div>
  );
}

const TABS: { id: Tab; label: string }[] = [
  { id: "files", label: "Files" },
  { id: "architecture", label: "Architecture" },
  { id: "review", label: "Review" },
  { id: "spec", label: "Requirements" },
];

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

  const title = job.data?.project_name ?? job.data?.title;
  useEffect(() => {
    document.title = `${title ?? "Job"} · AutoSWE`;
  }, [title]);

  const cancel = useMutation({
    mutationFn: () => api.cancelJob(jobId),
    onSuccess: (data) => {
      queryClient.setQueryData(["job", jobId], data);
      toast("Cancellation requested");
    },
    onError: (error) => toast.error("Could not cancel", { description: (error as Error).message }),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteJob(jobId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      toast.success("Job deleted");
      void navigate("/app");
    },
    onError: (error) => toast.error("Could not delete", { description: (error as Error).message }),
  });
  const download = useMutation({
    mutationFn: () => api.downloadArchive(jobId, `${job.data?.project_name ?? "project"}.zip`),
    onSuccess: () =>
      toast.success("Download started", { description: `${job.data?.project_name ?? "project"}.zip` }),
    onError: (error) => toast.error("Download failed", { description: (error as Error).message }),
  });

  if (job.isPending) return <JobSkeleton />;
  if (job.isError) {
    if (job.error instanceof ApiError && job.error.status === 404) {
      return (
        <EmptyState
          icon={<FileSearch />}
          title="Job not found"
          heading="h1"
          action={
            <Link to="/app" className={pill("primary", "sm")}>
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
    <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:py-10">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={data.status} />
            {data.project_name && (
              <span className="font-mono text-[12px] text-ink-2">{data.project_name}</span>
            )}
          </div>
          <h1 className="mt-2.5 max-w-3xl text-[28px] leading-8 font-semibold tracking-[0.007em] text-ink md:text-[32px] md:leading-9 md:tracking-[-0.003em]">
            {data.title}
          </h1>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {running ? (
            <Button
              variant="danger"
              size="sm"
              icon={<Square className="size-3.5" aria-hidden />}
              loading={cancel.isPending}
              disabled={data.cancel_requested}
              onClick={() => cancel.mutate()}
            >
              {data.cancel_requested ? "Cancelling…" : "Cancel"}
            </Button>
          ) : (
            <AlertDialog.Root>
              <AlertDialog.Trigger asChild>
                <Button
                  variant="plain"
                  size="sm"
                  icon={<Trash2 className="size-4" aria-hidden />}
                  loading={remove.isPending}
                >
                  Delete
                </Button>
              </AlertDialog.Trigger>
              <AlertDialog.Portal>
                <AlertDialog.Overlay className="fixed inset-0 z-40 bg-black/45 backdrop-blur-sm data-[state=open]:animate-[fade-in_200ms_ease-out]" />
                <AlertDialog.Content className="fixed top-1/2 left-1/2 z-50 w-[min(26rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-[22px] bg-raised p-7 text-ink shadow-[0_24px_80px_rgb(0_0_0/0.35)] outline-none data-[state=open]:animate-[pop-in_240ms_cubic-bezier(0.25,0.1,0.25,1)]">
                  <AlertDialog.Title className="text-[21px] leading-[25px] font-semibold tracking-[0.011em]">
                    Delete this job?
                  </AlertDialog.Title>
                  <AlertDialog.Description className="mt-2 text-[14px] leading-5 text-ink-2">
                    Its generated files, artifacts and activity log are removed. This cannot be undone.
                  </AlertDialog.Description>
                  <div className="mt-7 flex justify-end gap-2">
                    <AlertDialog.Cancel asChild>
                      <Button variant="plain" size="sm">
                        Keep it
                      </Button>
                    </AlertDialog.Cancel>
                    <AlertDialog.Action asChild>
                      <Button
                        size="sm"
                        className="bg-bad text-white hover:opacity-90"
                        onClick={() => remove.mutate()}
                      >
                        Delete job
                      </Button>
                    </AlertDialog.Action>
                  </div>
                </AlertDialog.Content>
              </AlertDialog.Portal>
            </AlertDialog.Root>
          )}
          <Button
            variant="primary"
            size="sm"
            icon={<Download className="size-4" aria-hidden />}
            disabled={data.status !== "succeeded"}
            loading={download.isPending}
            onClick={() => download.mutate()}
          >
            Download .zip
          </Button>
        </div>
      </div>

      {data.status === "failed" && data.error && (
        <div
          role="alert"
          className="mt-5 flex gap-3 rounded-[14px] bg-bad-soft p-4 text-[14px] leading-5 text-bad"
        >
          <XCircle className="size-5 shrink-0" aria-hidden />
          <p>{data.error}</p>
        </div>
      )}

      <div className="mt-8 grid gap-5 lg:grid-cols-3">
        <Card className="p-5 md:p-7 lg:col-span-2">
          <StageTimeline stages={data.stages} />
          <dl className="mt-7 grid grid-cols-2 gap-4 border-t border-hairline pt-5 sm:grid-cols-4">
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
                  : "None (offline)"
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

      <Tabs.Root value={tab} onValueChange={(value) => setTab(value as Tab)} className="mt-12">
        <Tabs.List
          aria-label="Job output"
          className="no-scrollbar inline-flex max-w-full gap-0.5 overflow-x-auto rounded-full bg-ink/[0.06] p-1"
        >
          {TABS.map((item) => (
            <Tabs.Trigger
              key={item.id}
              value={item.id}
              className={cx(
                "relative shrink-0 rounded-full px-4 py-1.5 text-[14px] leading-5 tracking-[-0.016em] transition-colors duration-200 pointer-coarse:py-3",
                tab === item.id ? "text-ink" : "text-ink-2 hover:text-ink",
              )}
            >
              {tab === item.id && (
                <m.span
                  layoutId="job-tab"
                  transition={SPRING}
                  className="absolute inset-0 rounded-full bg-surface shadow-sm ring-1 ring-hairline"
                  aria-hidden
                />
              )}
              <span className="relative">
                {item.label}
                {item.id === "review" && data.review_score !== null && (
                  <span className="ml-1.5 text-[12px] text-ink-2 tabular-nums">{data.review_score}</span>
                )}
              </span>
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="files" className="pt-6 outline-none">
          <FileExplorer jobId={jobId} focus={focus} running={running} />
        </Tabs.Content>
        <Tabs.Content value="architecture" className="pt-6 outline-none">
          <ArchitectureView jobId={jobId} />
        </Tabs.Content>
        <Tabs.Content value="review" className="pt-6 outline-none">
          <ReviewView jobId={jobId} onOpenFile={openFile} />
        </Tabs.Content>
        <Tabs.Content value="spec" className="pt-6 outline-none">
          <SpecView jobId={jobId} spec={data.spec} />
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
}
