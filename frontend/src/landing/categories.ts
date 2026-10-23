import { sample } from "./sample";

/** Groups of generated files for "What's in the box"; categories.test.ts checks they cover every file once. */
const GROUPS: { name: string; detail: string; match: (path: string) => boolean }[] = [
  {
    name: "Service",
    detail: "FastAPI app, SQLAlchemy models, Alembic migrations, a router per entity",
    match: (p) => p.startsWith("app/") || p.startsWith("migrations/") || p === "alembic.ini",
  },
  {
    name: "Tests",
    detail: "pytest suite on a migrated SQLite database, plus a migration check",
    match: (p) => p.startsWith("tests/"),
  },
  {
    name: "Container",
    detail: "Multi-stage, non-root Dockerfile with a health check",
    match: (p) => p === "Dockerfile" || p === ".dockerignore",
  },
  {
    name: "CI",
    detail: "GitHub Actions: lint, test, build the image",
    match: (p) => p.startsWith(".github/"),
  },
  {
    name: "Kubernetes",
    detail: "Deployment, Service and Kustomization",
    match: (p) => p.startsWith("deploy/k8s/"),
  },
  {
    name: "Terraform",
    detail: "ECR and ECS Fargate on AWS",
    match: (p) => p.startsWith("deploy/terraform/"),
  },
  {
    name: "Docs",
    detail: "README, ARCHITECTURE.md with ADRs, machine-readable JSON",
    match: (p) => p.endsWith(".md") || p.startsWith("docs/"),
  },
  {
    name: "Packaging",
    detail: "pyproject.toml with dependencies, ruff and pytest settings",
    match: (p) => p === "pyproject.toml",
  },
];

export const CATEGORIES = GROUPS.map((group) => ({
  ...group,
  files: sample.files.filter((file) => group.match(file.path)).map((file) => file.path),
}));
