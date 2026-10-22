import { expect, test, type Page } from "@playwright/test";

import { expectNoHorizontalOverflow, expectNoSeriousA11yViolations } from "./a11y";

const LIBRARY_SPEC =
  "Build a REST API for a small library where members borrow books and librarians track loans with due dates.";

async function startJob(page: Page): Promise<void> {
  await page.goto("/app");
  await page.getByRole("button", { name: "Library loans" }).click();
  await expect(page.getByLabel("Specification")).toHaveValue(LIBRARY_SPEC);
  await page.getByRole("button", { name: "Generate project" }).click();
  await expect(page).toHaveURL(/\/app\/jobs\/[0-9a-f-]+$/);
}

test.describe("studio", () => {
  test("new-project page is accessible in both themes and at 390px", async ({ page }) => {
    for (const colorScheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme });
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto("/app");
      await expect(page.getByRole("heading", { level: 1, name: "What should we build?" })).toBeVisible();
      await expectNoSeriousA11yViolations(page, `new job ${colorScheme}`);
      await page.setViewportSize({ width: 390, height: 844 });
      await expectNoHorizontalOverflow(page);
    }
  });

  test("a job runs live through the five agents and produces the project", async ({ page }) => {
    await startJob(page);
    await expect(page.getByText("Job queued")).toBeVisible();

    // With OFFLINE_DELAY_S the stages are observable while they run.
    const timeline = page.getByRole("list", { name: "Pipeline progress" });
    await expect(timeline.getByText(/: working$/)).toHaveCount(1);
    await expect(page.getByRole("log", { name: "Job activity" })).toContainText(
      "Requirements analyst started",
    );

    await expect(page.getByText("Succeeded").first()).toBeVisible({ timeout: 30_000 });
    for (const label of [
      "Requirements analyst",
      "Architect",
      "Code generator",
      "DevOps engineer",
      "Reviewer",
    ]) {
      await expect(timeline.getByText(`${label}: done`)).toBeAttached();
    }
    await expect(page.getByRole("log", { name: "Job activity" })).toContainText("Project ready");
    await expect(page.getByText("Validation passed; review score 100/100").first()).toBeVisible();

    // Files: the tree and a highlighted file.
    await expect(page.getByText("32 files", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Dockerfile" }).click();
    await expect(page.getByRole("region", { name: "Contents of Dockerfile" })).toContainText("HEALTHCHECK");

    // Review and requirements tabs.
    await page.getByRole("tab", { name: /Review/ }).click();
    await expect(page.getByRole("img", { name: "Review score 100 out of 100" })).toBeVisible();
    await page.getByRole("tab", { name: "Requirements" }).click();
    await expect(page.getByText("Original specification")).toBeVisible();

    for (const colorScheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme });
      await page.getByRole("tab", { name: "Files" }).click();
      await expectNoSeriousA11yViolations(page, `finished job ${colorScheme}`);
    }
  });

  test("command palette opens with the keyboard and switches the appearance", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto("/app");
    await expect(page.getByRole("heading", { name: "What should we build?" })).toBeVisible();
    await page.keyboard.press("Control+k");
    const palette = page.getByRole("dialog", { name: "Command menu" });
    await expect(palette).toBeVisible();
    await expectNoSeriousA11yViolations(page, "command palette");
    await page.keyboard.type("dark mode");
    await page.keyboard.press("Enter");
    await expect(palette).toBeHidden();
    await expect(page.locator("html")).toHaveClass(/dark/);
  });

  test("deleting a finished job asks first, then confirms with a toast", async ({ page }) => {
    await startJob(page);
    await expect(page.getByText("Succeeded").first()).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "Delete" }).click();
    const dialog = page.getByRole("alertdialog", { name: "Delete this job?" });
    await expect(dialog).toBeVisible();
    await expectNoSeriousA11yViolations(page, "delete dialog");
    await dialog.getByRole("button", { name: "Delete job" }).click();
    await expect(page).toHaveURL(/\/app$/);
    await expect(page.getByText("Job deleted")).toBeVisible();
  });

  test("unknown jobs and pages get designed not-found states", async ({ page }) => {
    await page.goto("/app/jobs/does-not-exist");
    await expect(page.getByRole("heading", { level: 1, name: "Job not found" })).toBeVisible();
    await page.goto("/no-such-page");
    await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
    await expectNoSeriousA11yViolations(page, "not found");
  });

  test("links from before the move to /app still work", async ({ page }) => {
    await page.goto("/jobs/does-not-exist");
    await expect(page).toHaveURL(/\/app\/jobs\/does-not-exist$/);
  });
});
