import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUp } from "lucide-react";
import { useEffect, useId, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { toast } from "../lib/toast";

import { PipelineRail } from "../components/PipelineRail";
import { Button, ErrorState } from "../components/ui";
import { api } from "../lib/api";
import { cx } from "../lib/cx";
import { AGENTS } from "../lib/stages";

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
    label: "Coffee inventory",
    spec: "Inventory service for a coffee roaster: track products with name, price and stock, and record shipments with carrier and shipped_at.",
  },
];

export function NewJobPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const specId = useId();
  const hintId = useId();
  const [spec, setSpec] = useState("");
  const [provider, setProvider] = useState<string | null>(null);
  const info = useQuery({ queryKey: ["info"], queryFn: api.info, staleTime: 60_000 });

  useEffect(() => {
    document.title = "New project · AutoSWE";
  }, []);

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
      toast.success("Job queued", { description: "Five agents are on it." });
      void navigate(`/app/jobs/${job.id}`);
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (valid && !create.isPending) create.mutate();
  };

  return (
    <div className="mx-auto max-w-[880px] px-4 pt-12 pb-20 sm:px-6 md:pt-20">
      <div className="text-center">
        <h1 className="t-headline text-ink">What should we build?</h1>
        <p className="t-body-lg mx-auto mt-4 max-w-[600px] text-ink-2">
          Describe the service in plain English. Name entities and fields if you like, for example{" "}
          <span className="font-mono text-[0.85em] text-ink">Book(title, year:int)</span>.
        </p>
      </div>

      <form
        onSubmit={submit}
        className="mt-10 overflow-hidden rounded-[22px] bg-surface shadow-[var(--shadow-tile)] ring-1 ring-hairline transition-shadow duration-200 focus-within:ring-2 focus-within:ring-accent-ink"
      >
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
          rows={6}
          maxLength={max}
          aria-describedby={hintId}
          placeholder="A REST API for a small library: members borrow books, librarians track loans and due dates…"
          className="block w-full resize-y bg-transparent px-6 pt-5 pb-3 text-[17px] leading-[25px] tracking-[-0.022em] text-ink outline-none placeholder:text-ink-2"
        />
        <div className="flex flex-wrap items-center gap-2 px-5 pb-4">
          <span className="mr-1 text-[12px] leading-4 text-ink-2">Try</span>
          {EXAMPLES.map((example) => (
            <button
              key={example.label}
              type="button"
              onClick={() => setSpec(example.spec)}
              className="hit-44 rounded-full bg-ink/[0.05] px-3 py-1 text-[12px] leading-4 tracking-[-0.01em] text-ink transition-colors duration-200 hover:bg-accent-soft hover:text-accent-ink"
            >
              {example.label}
            </button>
          ))}
        </div>
        <div className="flex flex-col gap-3 border-t border-hairline bg-surface-3 px-5 py-3.5 sm:flex-row sm:items-center">
          {providers.length > 1 ? (
            <div
              role="radiogroup"
              aria-label="Provider"
              className="inline-flex rounded-full bg-ink/[0.06] p-0.5"
            >
              {providers.map((p, index) => (
                <button
                  key={p.name}
                  type="button"
                  role="radio"
                  aria-checked={selected === p.name}
                  // One tab stop for the group; arrow keys move and select (WAI-ARIA radio group).
                  tabIndex={selected === p.name ? 0 : -1}
                  onClick={() => setProvider(p.name)}
                  onKeyDown={(event) => {
                    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
                    if (!step) return;
                    event.preventDefault();
                    const next = (index + step + providers.length) % providers.length;
                    setProvider(providers[next]!.name);
                    const radios =
                      event.currentTarget.parentElement?.querySelectorAll<HTMLElement>('[role="radio"]');
                    radios?.[next]?.focus();
                  }}
                  className={cx(
                    "rounded-full px-3.5 py-1.5 text-[12px] leading-4 font-medium transition-colors duration-200 pointer-coarse:py-3",
                    selected === p.name ? "bg-surface text-ink shadow-sm" : "text-ink-2 hover:text-ink",
                  )}
                >
                  {p.name === "anthropic" ? `Claude · ${p.model}` : "Offline"}
                </button>
              ))}
            </div>
          ) : (
            <p className="text-[12px] leading-4 text-ink-2">
              {selected === "offline"
                ? "Offline provider: deterministic templates, no API key needed."
                : selected
                  ? `Provider: ${selected}`
                  : " "}
            </p>
          )}
          <div className="flex items-center gap-3 sm:ml-auto">
            <span
              id={hintId}
              className={cx(
                "text-[12px] leading-4 whitespace-nowrap tabular-nums",
                length > 0 && !valid ? "text-warn" : "text-ink-2",
              )}
              aria-live="polite"
            >
              {length > 0 && length < min
                ? `${min - length} more characters`
                : `${length.toLocaleString("en")} / ${max.toLocaleString("en")}`}
            </span>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              disabled={!valid}
              loading={create.isPending}
              className="flex-1 sm:flex-none"
              icon={<ArrowUp className="size-4" aria-hidden />}
            >
              Generate project
            </Button>
          </div>
        </div>
      </form>
      {create.isError && (
        <div className="mt-4">
          <ErrorState error={create.error} />
        </div>
      )}

      <section aria-labelledby="pipeline-title" className="mt-20">
        <h2
          id="pipeline-title"
          className="text-center text-[21px] leading-[25px] font-semibold tracking-[0.011em] text-ink"
        >
          Then five agents take over.
        </h2>
        <PipelineRail
          className="mt-8"
          states={["pending", "pending", "pending", "pending", "pending"]}
          labels="full"
          caption="The pipeline your spec runs through"
        />
        <ul className="mt-6 hidden grid-cols-5 gap-3 text-center md:grid">
          {AGENTS.map((agent) => (
            <li key={agent.name} className="px-1 text-[12px] leading-4 text-ink-2">
              {agent.produces}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
