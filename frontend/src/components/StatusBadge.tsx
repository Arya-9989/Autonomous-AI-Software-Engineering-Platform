import { Ban, CheckCircle2, CircleDashed, Loader2, XCircle } from "lucide-react";

import type { JobStatus } from "../lib/types";
import { Badge, type Tone } from "./ui";

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
      <Icon className={`size-3 ${status === "running" ? "animate-spin" : ""}`} aria-hidden />
      {label}
    </Badge>
  );
}

const DOT: Record<JobStatus, string> = {
  queued: "bg-zinc-400",
  running: "bg-brand-500 animate-pulse",
  succeeded: "bg-emerald-500",
  failed: "bg-red-500",
  cancelled: "bg-amber-500",
};

export function StatusDot({ status }: { status: JobStatus }) {
  return <span className={`inline-block size-2 shrink-0 rounded-full ${DOT[status]}`} aria-hidden />;
}
