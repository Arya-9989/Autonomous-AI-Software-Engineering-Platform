import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { Card, SectionTitle, Skeleton } from "./ui";

function List({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <Card className="p-5">
      <SectionTitle>{title}</SectionTitle>
      <ul className="space-y-1.5 text-sm text-zinc-700 dark:text-zinc-300">
        {items.map((item, index) => (
          <li key={index} className="flex gap-2">
            <span className="mt-2 size-1 shrink-0 rounded-full bg-brand-500" aria-hidden />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function SpecView({ jobId, spec }: { jobId: string; spec: string }) {
  const requirements = useQuery({
    queryKey: ["job", jobId, "requirements"],
    queryFn: () => api.requirements(jobId),
    retry: false,
  });
  return (
    <div className="space-y-5">
      <Card className="p-5">
        <SectionTitle>Original specification</SectionTitle>
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">{spec}</p>
      </Card>
      {requirements.isPending ? (
        <Skeleton className="h-48" />
      ) : requirements.data ? (
        <div className="grid gap-5 lg:grid-cols-2">
          <List title="User stories" items={requirements.data.user_stories} />
          <List title="Functional requirements" items={requirements.data.functional_requirements} />
          <List title="Non-functional requirements" items={requirements.data.non_functional_requirements} />
          <List title="Assumptions" items={requirements.data.assumptions} />
          <List title="Out of scope" items={requirements.data.out_of_scope} />
        </div>
      ) : (
        <p className="text-sm text-zinc-500">The requirements analyst has not finished yet.</p>
      )}
    </div>
  );
}
