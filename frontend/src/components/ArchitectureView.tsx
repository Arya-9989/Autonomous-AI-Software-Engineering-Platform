import { useQuery } from "@tanstack/react-query";
import { Boxes } from "lucide-react";
import { useMemo } from "react";

import { api, ApiError } from "../lib/api";
import { diagramSource } from "../lib/diagram";
import { Mermaid } from "./Mermaid";
import { Badge, Card, EmptyState, ErrorState, SectionTitle, Skeleton } from "./ui";
import { cx } from "../lib/cx";

const METHOD_STYLE: Record<string, string> = {
  GET: "bg-accent-soft text-accent-ink",
  POST: "bg-ok-soft text-ok",
  PUT: "bg-warn-soft text-warn",
  PATCH: "bg-warn-soft text-warn",
  DELETE: "bg-bad-soft text-bad",
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
        <p className="text-sm leading-relaxed text-ink">{arch.overview}</p>
      </Card>

      <Card className="p-5">
        <SectionTitle>Components</SectionTitle>
        <Mermaid source={source} label="Component dependency diagram" />
        <div className="mt-5 grid gap-3 sm:grid-cols-2">
          {arch.components.map((component) => (
            <div key={component.name} className="rounded-lg border border-hairline p-3">
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-medium">{component.name}</p>
                <span className="text-right text-[11px] text-ink-2">{component.technology}</span>
              </div>
              <p className="mt-1 text-xs text-ink-2">{component.responsibility}</p>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-5 xl:grid-cols-5">
        <Card className="overflow-hidden xl:col-span-3">
          <div className="px-5 pt-5">
            <SectionTitle action={<span className="text-xs text-ink-2">{arch.endpoints.length} routes</span>}>
              API endpoints
            </SectionTitle>
          </div>
          <div className="scrollbar-thin max-h-[26rem] overflow-y-auto">
            <table className="w-full text-left text-sm">
              <tbody className="divide-y divide-hairline">
                {arch.endpoints.map((endpoint) => (
                  <tr key={`${endpoint.method} ${endpoint.path}`}>
                    <td className="w-24 py-2 pr-3 pl-5">
                      <span
                        className={cx(
                          "inline-block w-16 rounded px-1.5 py-0.5 text-center font-mono text-[11px] font-semibold",
                          METHOD_STYLE[endpoint.method] ?? METHOD_STYLE.GET,
                        )}
                      >
                        {endpoint.method}
                      </span>
                    </td>
                    <td className="py-2 font-mono text-xs break-all text-ink">{endpoint.path}</td>
                    <td className="hidden py-2 pr-5 text-xs text-ink-2 sm:table-cell">{endpoint.summary}</td>
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
              <div key={entity.name} className="rounded-lg border border-hairline">
                <p className="border-b border-hairline px-3 py-2 text-sm font-medium">{entity.name}</p>
                <ul className="px-3 py-2">
                  {entity.fields.map((field) => (
                    <li
                      key={field.name}
                      className="flex items-center justify-between py-0.5 font-mono text-xs"
                    >
                      <span className="text-ink">
                        {field.name}
                        {!field.required && <span className="text-ink-2">?</span>}
                      </span>
                      <span className="text-accent-ink">{field.type}</span>
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
                  <span className="text-ink-2">{tech.layer}</span>
                  <span className="text-right font-medium">{tech.choice}</span>
                </dt>
                <dd className="mt-0.5 text-xs text-ink-2">{tech.rationale}</dd>
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
                  <span className="mr-2 font-mono text-xs text-ink-2">
                    ADR-{String(index + 1).padStart(3, "0")}
                  </span>
                  {decision.title}
                </p>
                <p className="mt-1 text-xs text-ink">{decision.decision}</p>
                <p className="mt-1 text-xs text-ink-2">{decision.rationale}</p>
                {decision.alternatives.length > 0 && (
                  <p className="mt-1 text-xs text-ink-2">Rejected: {decision.alternatives.join(", ")}</p>
                )}
              </li>
            ))}
          </ol>
        </Card>
      </div>
    </div>
  );
}
