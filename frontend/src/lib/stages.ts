import { Boxes, ClipboardList, Code2, Container, ShieldCheck, type LucideIcon } from "lucide-react";

import type { StageName } from "./types";

/** The five agents, in pipeline order. Labels match the backend's STAGE_LABELS. */
export const AGENTS: {
  name: StageName;
  label: string;
  short: string;
  icon: LucideIcon;
  produces: string;
}[] = [
  {
    name: "analyze",
    label: "Requirements analyst",
    short: "Analyst",
    icon: ClipboardList,
    produces: "Entities, user stories, assumptions",
  },
  {
    name: "architect",
    label: "Architect",
    short: "Architect",
    icon: Boxes,
    produces: "Components, endpoints, decision records",
  },
  {
    name: "implement",
    label: "Code generator",
    short: "Coder",
    icon: Code2,
    produces: "FastAPI service and tests",
  },
  {
    name: "devops",
    label: "DevOps engineer",
    short: "DevOps",
    icon: Container,
    produces: "Docker, CI, Kubernetes, Terraform",
  },
  {
    name: "review",
    label: "Reviewer",
    short: "Reviewer",
    icon: ShieldCheck,
    produces: "Parsers, policy checks, repair, review",
  },
];
