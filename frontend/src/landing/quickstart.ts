/** Commands from the README's Quickstart, unchanged (quickstart.test.ts checks every line). */
export const QUICKSTART: { title: string; detail: string; command: string }[] = [
  {
    title: "Docker",
    detail: "PostgreSQL and the app. Opens on localhost:8000, offline unless you pass ANTHROPIC_API_KEY.",
    command: "docker compose up --build",
  },
  {
    title: "Local",
    detail: "Python 3.12+, uv and Node 24. The UI is served by FastAPI from frontend/dist.",
    command: [
      "uv sync",
      "(cd frontend && npm ci && npm run build)",
      "cp .env.example .env",
      "uv run autoswe serve",
    ].join("\n"),
  },
  {
    title: "Command line",
    detail: "Runs the whole pipeline once and writes the project to a folder.",
    command:
      'uv run autoswe generate "A REST API for a small library where members borrow books and librarians track loans with due dates." --out ./library-api',
  },
];
