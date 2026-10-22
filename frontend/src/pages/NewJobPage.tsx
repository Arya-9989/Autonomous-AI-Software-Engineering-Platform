import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Boxes, ClipboardList, Code2, Container, ShieldCheck, Sparkles } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";

import { Button, Card, ErrorState } from "../components/ui";
import { cx } from "../lib/cx";
import { api } from "../lib/api";

const EXAMPLES = [
  {
    label: "Library loans",
    spec: "Build a REST API for a small library where members borrow books and librarians track loans with due dates.",
  },
  {
    label: "Clinic bookings",
    spec: "A booking service called ClinicBook for a dental clinic. Entities: Patient(name, email, date_of_birth:date), Appointment(starts_at:datetime, duration_minutes:int, notes?).",
  },
  {
    label: "Inventory",
    spec: "Inventory service for a coffee roaster: track products with name, price and stock, and record shipments with carrier and shipped_at.",
  },
];

const STAGES = [
  { icon: ClipboardList, title: "Requirements analyst", text: "Entities, user stories, assumptions" },
  { icon: Boxes, title: "Architect", text: "Components, endpoints, ADRs" },
  { icon: Code2, title: "Code generator", text: "FastAPI service and tests" },
  { icon: Container, title: "DevOps engineer", text: "Docker, CI, Kubernetes, Terraform" },
  { icon: ShieldCheck, title: "Reviewer", text: "Parsers, policy checks, repair, review" },
];

export function NewJobPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const specId = useId();
  const [spec, setSpec] = useState("");
  const [provider, setProvider] = useState<string | null>(null);
  const info = useQuery({ queryKey: ["info"], queryFn: api.info, staleTime: 60_000 });

  const providers = info.data?.providers ?? [];
  const selected = provider ?? providers.find((p) => p.default)?.name ?? null;
  const min = info.data?.min_spec_chars ?? 20;
  const max = info.data?.max_spec_chars ?? 20_000;
  const length = spec.trim().length;
  const valid = length >= min && length <= max;

  const create = useMutation({
    mutationFn: () => api.createJob({ spec: spec.trim(), provider: selected ?? undefined }),
    onSuccess: (job) => {
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.setQueryData(["job", job.id], job);
      void navigate(`/jobs/${job.id}`);
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (valid && !create.isPending) create.mutate();
  };

  return (
    <div className="relative isolate">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-80 bg-[radial-gradient(60%_100%_at_50%_0%,var(--color-brand-100),transparent)] dark:bg-[radial-gradient(60%_100%_at_50%_0%,color-mix(in_oklch,var(--color-brand-900)_60%,transparent),transparent)]"
      />
      <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6 sm:py-12 lg:py-16">
        <div className="mb-8 text-center sm:mb-10">
          <span className="mb-4 inline-flex items-center gap-1.5 rounded-full border border-brand-200 bg-brand-50 px-3 py-1 text-xs font-medium text-brand-700 dark:border-brand-900 dark:bg-brand-950/50 dark:text-brand-300">
            <Sparkles className="size-3.5" aria-hidden /> Five agents, one validated repository
          </span>
          <h1 className="text-3xl font-semibold tracking-tight text-balance text-zinc-900 sm:text-4xl dark:text-white">
            Describe the software you need
          </h1>
          <p className="mx-auto mt-3 max-w-2xl text-base text-pretty text-zinc-600 dark:text-zinc-400">
            The pipeline analyses your requirements, designs an architecture, writes the code and tests, adds
            Docker, CI, Kubernetes and Terraform, then validates and reviews the result.
          </p>
        </div>

        <Card className="overflow-hidden">
          <form onSubmit={submit}>
            <label htmlFor={specId} className="sr-only">
              Specification
            </label>
            <textarea
              id={specId}
              value={spec}
              onChange={(e) => setSpec(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(e);
              }}
              rows={7}
              maxLength={max}
              placeholder="e.g. A REST API for a small library: members borrow books, librarians track loans and due dates…"
              className="block w-full resize-y border-0 bg-transparent px-5 py-4 text-[15px] leading-relaxed text-zinc-900 outline-none placeholder:text-zinc-400 dark:text-zinc-100 dark:placeholder:text-zinc-600"
            />
            <div className="flex flex-wrap items-center gap-2 border-t border-zinc-100 px-4 py-3 dark:border-zinc-800">
              <span className="mr-1 text-xs text-zinc-500">Try:</span>
              {EXAMPLES.map((example) => (
                <button
                  key={example.label}
                  type="button"
                  onClick={() => setSpec(example.spec)}
                  className="rounded-full border border-zinc-200 px-2.5 py-1 text-xs text-zinc-600 transition-colors hover:border-brand-300 hover:bg-brand-50 hover:text-brand-700 dark:border-zinc-700 dark:text-zinc-400 dark:hover:border-brand-800 dark:hover:bg-brand-950/40 dark:hover:text-brand-300"
                >
                  {example.label}
                </button>
              ))}
            </div>
            <div className="flex flex-col gap-3 border-t border-zinc-100 bg-zinc-50/70 px-4 py-3 sm:flex-row sm:items-center dark:border-zinc-800 dark:bg-zinc-900/40">
              {providers.length > 1 ? (
                <div
                  role="radiogroup"
                  aria-label="Provider"
                  className="inline-flex rounded-lg bg-zinc-200/60 p-0.5 dark:bg-zinc-800"
                >
                  {providers.map((p) => (
                    <button
                      key={p.name}
                      type="button"
                      role="radio"
                      aria-checked={selected === p.name}
                      onClick={() => setProvider(p.name)}
                      className={cx(
                        "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                        selected === p.name
                          ? "bg-white text-zinc-900 shadow-xs dark:bg-zinc-950 dark:text-white"
                          : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-200",
                      )}
                    >
                      {p.name === "anthropic" ? `Claude (${p.model})` : "Offline"}
                    </button>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  {selected === "offline"
                    ? "Offline provider: deterministic templates, no API key needed."
                    : selected
                      ? `Provider: ${selected}`
                      : " "}
                </p>
              )}
              <div className="flex items-center gap-3 sm:ml-auto">
                <span
                  className={cx(
                    "text-xs whitespace-nowrap tabular-nums",
                    length > 0 && !valid ? "text-amber-600 dark:text-amber-400" : "text-zinc-400",
                  )}
                  aria-live="polite"
                >
                  {length > 0 && length < min
                    ? `${min - length} more characters`
                    : `${length.toLocaleString()} / ${max.toLocaleString()}`}
                </span>
                <Button
                  type="submit"
                  variant="primary"
                  disabled={!valid}
                  loading={create.isPending}
                  className="w-full sm:w-auto"
                >
                  Generate project <ArrowRight className="size-4" aria-hidden />
                </Button>
              </div>
            </div>
          </form>
        </Card>
        {create.isError && (
          <div className="mt-4">
            <ErrorState error={create.error} />
          </div>
        )}

        <ol className="mt-10 grid grid-cols-1 gap-3 sm:grid-cols-5">
          {STAGES.map(({ icon: Icon, title, text }, index) => (
            <li
              key={title}
              className="relative rounded-xl border border-zinc-200/80 bg-white/60 p-4 dark:border-zinc-800 dark:bg-zinc-900/40"
            >
              <div className="mb-3 flex items-center justify-between">
                <span className="grid size-8 place-items-center rounded-lg bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                  <Icon className="size-4" aria-hidden />
                </span>
                <span className="font-mono text-[11px] text-zinc-400">0{index + 1}</span>
              </div>
              <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{title}</p>
              <p className="mt-0.5 text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">{text}</p>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
