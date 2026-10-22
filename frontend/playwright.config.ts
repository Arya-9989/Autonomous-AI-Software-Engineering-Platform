import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against the real stack: FastAPI serving the production build (frontend/dist),
 * the in-process worker and the offline provider, with a small OFFLINE_DELAY_S so the live
 * progress UI can be observed. Build the UI first: `npm run build && npm run e2e`.
 */
const port = Number(process.env.E2E_PORT ?? 5150);
const baseURL = `http://127.0.0.1:${port}`;
const database = process.env.E2E_DATABASE_URL ?? "sqlite+aiosqlite:///./data/e2e.db";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // E2E_SERVER_COMMAND lets machines without uv on PATH use e.g. "python -m uv run ...".
    command: process.env.E2E_SERVER_COMMAND ?? "uv run --locked autoswe serve",
    cwd: "..",
    url: `${baseURL}/readyz`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      HOST: "127.0.0.1",
      PORT: String(port),
      AUTH_DISABLED: "true",
      LLM_DEFAULT_PROVIDER: "offline",
      OFFLINE_DELAY_S: "0.4",
      LOG_FORMAT: "console",
      LOG_LEVEL: "WARNING",
      DATABASE_URL: database,
    },
  },
});
