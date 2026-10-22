import { useQuery } from "@tanstack/react-query";
import { Boxes } from "lucide-react";
import { useMemo } from "react";

import { api, ApiError } from "../lib/api";
import { diagramSource } from "../lib/diagram";
import { Mermaid } from "./Mermaid";
import { Badge, Card, EmptyState, ErrorState, SectionTitle, Skeleton } from "./ui";
import { cx } from "../lib/cx";

const METHOD_STYLE: Record<string, string> = {
  GET: "bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-950/40 dark:text-sky-300 dark:ring-sky-900",
  POST: "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:ring-emerald-900",
  PUT: "bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-900",
  PATCH:
    "bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-900",
  DELETE: "bg-red-50 text-red-700 ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-900",
};

export function ArchitectureView({ jobId }: { jobId: string }) {
  const query = useQuery({
    queryKey: ["job", jobId, "architecture"],
    queryFn: () => api.architecture(jobId),
  });
  const source = useMemo(() => (query.data ? diagramSource(query.data) : ""), [query.data]);

  if (query.isPending) return <Skeleton className="h-96" />;
  if (query.isError) {
    if (query.error instanceof ApiError && query.error.status === 404) {
      return (
        <EmptyState icon={<Boxes className="size-6" />} title="No architecture yet">
          The architect agent has not finished.
        </EmptyState>
      );
    }
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  }
  const arch = query.data;

  return (
    <div className="space-y-5">
      <Card className="p-5">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Badge tone="brand">{arch.style}</Badge>
          <Badge>{arch.deployment.database}</Badge>
          <Badge>{arch.deployment.cloud_target}</Badge>
          <Badge>
            {arch.deployment.replicas} replicas · port {arch.deployment.container_port}
          </Badge>
        </div>
        <p className="text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">{arch.overview}</p>
      </Card>

      <Card className="p-5">
        <SectionTitle>Components</SectionTitle>
        <Mermaid source={source} label="Component dependency diagram" />
        <div className="mt-5 grid gap-3 sm:grid-cols-2">
          {arch.components.map((component) => (
            <div key={component.name} className="rounded-lg border border-zinc-100 p-3 dark:border-zinc-800">
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-medium">{component.name}</p>
                <span className="text-right text-[11px] text-zinc-500">{component.technology}</span>
              </div>
              <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{component.responsibility}</p>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-5 xl:grid-cols-5">
        <Card className="overflow-hidden xl:col-span-3">
          <div className="px-5 pt-5">
            <SectionTitle
              action={<span className="text-xs text-zinc-500">{arch.endpoints.length} routes</span>}
            >
              API endpoints
            </SectionTitle>
          </div>
          <div className="scrollbar-thin max-h-[26rem] overflow-y-auto">
            <table className="w-full text-left text-sm">
              <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                {arch.endpoints.map((endpoint) => (
                  <tr key={`${endpoint.method} ${endpoint.path}`}>
                    <td className="w-24 py-2 pr-3 pl-5">
                      <span
                        className={cx(
                          "inline-block w-16 rounded px-1.5 py-0.5 text-center font-mono text-[11px] font-semibold ring-1 ring-inset",
                          METHOD_STYLE[endpoint.method] ?? METHOD_STYLE.GET,
                        )}
                      >
                        {endpoint.method}
                      </span>
                    </td>
                    <td className="py-2 font-mono text-xs break-all text-zinc-800 dark:text-zinc-200">
                      {endpoint.path}
                    </td>
                    <td className="hidden py-2 pr-5 text-xs text-zinc-500 sm:table-cell">
                      {endpoint.summary}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card className="p-5 xl:col-span-2">
          <SectionTitle>Data model</SectionTitle>
          <div className="space-y-3">
            {arch.entities.map((entity) => (
              <div key={entity.name} className="rounded-lg border border-zinc-100 dark:border-zinc-800">
                <p className="border-b border-zinc-100 px-3 py-2 text-sm font-medium dark:border-zinc-800">
                  {entity.name}
                </p>
                <ul className="px-3 py-2">
                  {entity.fields.map((field) => (
                    <li
                      key={field.name}
                      className="flex items-center justify-between py-0.5 font-mono text-xs"
                    >
                      <span className="text-zinc-800 dark:text-zinc-200">
                        {field.name}
                        {!field.required && <span className="text-zinc-400">?</span>}
                      </span>
                      <span className="text-brand-600 dark:text-brand-400">{field.type}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card className="p-5">
          <SectionTitle>Technology choices</SectionTitle>
          <dl className="space-y-3">
            {arch.tech_stack.map((tech) => (
              <div key={tech.layer}>
                <dt className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="text-zinc-500">{tech.layer}</span>
                  <span className="text-right font-medium">{tech.choice}</span>
                </dt>
                <dd className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">{tech.rationale}</dd>
              </div>
            ))}
          </dl>
        </Card>
        <Card className="p-5">
          <SectionTitle>Decision records</SectionTitle>
          <ol className="space-y-4">
            {arch.decisions.map((decision, index) => (
              <li key={decision.title}>
                <p className="text-sm font-medium">
                  <span className="mr-2 font-mono text-xs text-zinc-400">
                    ADR-{String(index + 1).padStart(3, "0")}
                  </span>
                  {decision.title}
                </p>
                <p className="mt-1 text-xs text-zinc-700 dark:text-zinc-300">{decision.decision}</p>
                <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">{decision.rationale}</p>
                {decision.alternatives.length > 0 && (
                  <p className="mt-1 text-xs text-zinc-400">Rejected: {decision.alternatives.join(", ")}</p>
                )}
              </li>
            ))}
          </ol>
        </Card>
      </div>
    </div>
  );
}
