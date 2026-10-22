import { AGENTS } from "../lib/stages";
import { sample } from "./sample";

const COPY: { title: string; body: string }[] = [
  {
    title: "Reads the spec. Names the things.",
    body: "The requirements analyst turns your paragraph into a typed Requirements artifact: entities and their fields, user stories, functional and non-functional requirements, assumptions and what is out of scope.",
  },
  {
    title: "Designs before it writes.",
    body: "The architect chooses components and a tech stack, lists every endpoint, plans the deployment and records its decisions as ADRs. The code that follows is traced back to this design.",
  },
  {
    title: "Writes the service. And its tests.",
    body: "The code generator produces a FastAPI service with SQLAlchemy models, Pydantic schemas, one router per entity and a pytest suite that runs against an isolated database.",
  },
  {
    title: "Ready to ship, not just to run.",
    body: "The DevOps engineer adds a multi-stage, non-root Dockerfile with a health check, a GitHub Actions workflow, Kubernetes manifests and Terraform for ECR and ECS Fargate.",
  },
  {
    title: "Checks everything. Repairs what fails.",
    body: "The reviewer parses every file, applies security and operability rules, sends errors back to the code generator for one bounded repair round, then writes the review.",
  },
];

function StepLog({ index }: { index: number }) {
  const stage = sample.stages[index]!;
  return (
    <div className="mt-6 space-y-1 font-mono text-[12px] leading-[18px] md:text-[13px] md:leading-5">
      {stage.logs.map((line) => (
        <p key={line} className="text-ink-2">
          <span aria-hidden className="mr-2 text-ink-3">
            ›
          </span>
          {line}
        </p>
      ))}
      <p className="text-ink">
        <span aria-hidden className="mr-2 text-ok">
          ✓
        </span>
        {stage.summary}
      </p>
    </div>
  );
}

/** One agent's copy: number and role, headline, what it does, and its real log lines. */
export function StepCopy({ index }: { index: number }) {
  const agent = AGENTS[index]!;
  const copy = COPY[index]!;
  return (
    <>
      <p className="t-eyebrow text-ink-2">
        <span className="text-accent-ink">{String(index + 1).padStart(2, "0")}</span> · {agent.label}
      </p>
      <h3 className="t-tile mt-3 text-ink">{copy.title}</h3>
      <p className="t-body-lg mt-4 text-ink-2">{copy.body}</p>
      <StepLog index={index} />
    </>
  );
}

/** How many agents the story covers (one copy each). */
export const COPY_COUNT = 5;
