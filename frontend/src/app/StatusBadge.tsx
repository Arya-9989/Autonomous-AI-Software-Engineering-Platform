import { Ban, CheckCircle2, CircleDashed, Loader2, XCircle } from "lucide-react";

import { Badge, type Tone } from "../components/ui";
import { cx } from "../lib/cx";
import type { JobStatus } from "../lib/types";

const STYLE: Record<JobStatus, { tone: Tone; label: string; icon: typeof CheckCircle2 }> = {
  queued: { tone: "neutral", label: "Queued", icon: CircleDashed },
  running: { tone: "brand", label: "Running", icon: Loader2 },
  succeeded: { tone: "success", label: "Succeeded", icon: CheckCircle2 },
  failed: { tone: "danger", label: "Failed", icon: XCircle },
  cancelled: { tone: "warning", label: "Cancelled", icon: Ban },
};

export function StatusBadge({ status }: { status: JobStatus }) {
  const { tone, label, icon: Icon } = STYLE[status];
  return (
    <Badge tone={tone}>
      <Icon className={cx("size-3", status === "running" && "animate-spin")} aria-hidden />
      {label}
    </Badge>
  );
}

const DOT: Record<JobStatus, string> = {
  queued: "bg-ink-3",
  running: "bg-accent-ink animate-pulse-soft",
  succeeded: "bg-ok",
  failed: "bg-bad",
  cancelled: "bg-warn",
};

export function StatusDot({ status }: { status: JobStatus }) {
  return <span className={cx("inline-block size-2 shrink-0 rounded-full", DOT[status])} aria-hidden />;
}
