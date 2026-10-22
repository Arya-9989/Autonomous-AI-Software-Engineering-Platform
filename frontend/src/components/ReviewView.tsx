import { useQuery } from "@tanstack/react-query";
import { AlertCircle, AlertTriangle, CheckCircle2, Info, ShieldCheck } from "lucide-react";

import { api, ApiError } from "../lib/api";
import type { Severity } from "../lib/types";
import { Card, EmptyState, ErrorState, SectionTitle, Skeleton } from "./ui";
import { cx } from "../lib/cx";

const SEVERITY: Record<Severity, { icon: typeof Info; className: string; label: string }> = {
  error: { icon: AlertCircle, className: "text-bad", label: "Error" },
  warning: { icon: AlertTriangle, className: "text-warn", label: "Warning" },
  info: { icon: Info, className: "text-accent-ink", label: "Info" },
};

function ScoreRing({ score }: { score: number }) {
  const radius = 34;
  const circumference = 2 * Math.PI * radius;
  const tone = score >= 85 ? "text-ok" : score >= 60 ? "text-warn" : "text-bad";
  return (
    <div className="relative size-24 shrink-0" role="img" aria-label={`Review score ${score} out of 100`}>
      <svg viewBox="0 0 80 80" className="size-24 -rotate-90">
        <circle
          cx="40"
          cy="40"
          r={radius}
          className="fill-none stroke-[var(--hairline-strong)]"
          strokeWidth="7"
        />
        <circle
          cx="40"
          cy="40"
          r={radius}
          className={cx("fill-none stroke-current transition-[stroke-dashoffset] duration-700", tone)}
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - score / 100)}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center">
        <span className="text-2xl font-semibold tabular-nums">{score}</span>
      </div>
    </div>
  );
}

export function ReviewView({ jobId, onOpenFile }: { jobId: string; onOpenFile: (path: string) => void }) {
  const query = useQuery({ queryKey: ["job", jobId, "review"], queryFn: () => api.review(jobId) });

  if (query.isPending) return <Skeleton className="h-80" />;
  if (query.isError) {
    if (query.error instanceof ApiError && query.error.status === 404) {
      return (
        <EmptyState icon={<ShieldCheck className="size-6" />} title="Review pending">
          Validation and review run after the DevOps stage.
        </EmptyState>
      );
    }
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  }

  const review = query.data;
  const issues = review.validation.issues;
  const count = (severity: Severity) => issues.filter((i) => i.severity === severity).length;
  const order: Severity[] = ["error", "warning", "info"];
  const findings = [...review.findings].sort((a, b) => order.indexOf(a.severity) - order.indexOf(b.severity));

  return (
    <div className="space-y-5">
      <Card className="flex flex-col gap-5 p-5 sm:flex-row sm:items-center">
        <ScoreRing score={review.score} />
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-relaxed text-ink">{review.summary}</p>
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              { label: "Files checked", value: review.validation.files_checked },
              { label: "Errors", value: count("error") },
              { label: "Warnings", value: count("warning") },
              { label: "Notes", value: count("info") },
            ].map((stat) => (
              <div key={stat.label} className="rounded-lg bg-surface-2 px-3 py-2">
                <p className="text-[11px] text-ink-2">{stat.label}</p>
                <p className="text-lg font-semibold tabular-nums">{stat.value}</p>
              </div>
            ))}
          </div>
        </div>
      </Card>

      <Card className="p-5">
        <SectionTitle>Automated validation</SectionTitle>
        {issues.length === 0 ? (
          <p className="flex items-center gap-2 text-sm text-ok">
            <CheckCircle2 className="size-4" aria-hidden />
            Every file parsed cleanly; no policy, structure or secret-scan issues.
          </p>
        ) : (
          <ul className="divide-y divide-hairline">
            {issues.map((issue, index) => {
              const { icon: Icon, className } = SEVERITY[issue.severity];
              return (
                <li key={index} className="flex gap-3 py-2 text-sm">
                  <Icon className={cx("mt-0.5 size-4 shrink-0", className)} aria-hidden />
                  <div className="min-w-0">
                    <p>{issue.message}</p>
                    <p className="font-mono text-[11px] text-ink-2">
                      {issue.rule}
                      {issue.file && (
                        <>
                          {" · "}
                          <button
                            type="button"
                            className="underline-offset-2 hover:underline"
                            onClick={() => onOpenFile(issue.file!)}
                          >
                            {issue.file}
                            {issue.line ? `:${issue.line}` : ""}
                          </button>
                        </>
                      )}
                    </p>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card className="p-5">
        <SectionTitle>Reviewer findings</SectionTitle>
        {findings.length === 0 ? (
          <p className="text-sm text-ink-2">No findings.</p>
        ) : (
          <ul className="space-y-3">
            {findings.map((finding, index) => {
              const { icon: Icon, className, label } = SEVERITY[finding.severity];
              return (
                <li key={index} className="flex gap-3 rounded-lg border border-hairline p-3">
                  <Icon className={cx("mt-0.5 size-4 shrink-0", className)} aria-label={label} />
                  <div className="min-w-0 text-sm">
                    <p className="text-ink">{finding.message}</p>
                    {finding.suggestion && <p className="mt-1 text-xs text-ink-2">{finding.suggestion}</p>}
                    {finding.file && (
                      <button
                        type="button"
                        onClick={() => onOpenFile(finding.file!)}
                        className="mt-1.5 font-mono text-[11px] text-accent-ink hover:underline"
                      >
                        {finding.file}
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
