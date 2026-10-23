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
    await expect(page.getByText("37 files", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Dockerfile" }).click();
    await expect(page.getByRole("region", { name: "Contents of Dockerfile" })).toContainText("HEALTHCHECK");

    // Review and requirements tabs.
    await page.getByRole("tab", { name: /Review/ }).click();
    await expect(page.getByRole("img", { name: "Review score 100 out of 100" })).toBeVisible();
    // A clicked tab shows no keyboard focus ring; moving with the arrow keys does.
    const outline = (name: RegExp) =>
      page.getByRole("tab", { name }).evaluate((el) => getComputedStyle(el).outlineStyle);
    expect(await outline(/Review/)).toBe("none");
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("tab", { name: "Requirements" })).toBeFocused();
    expect(await outline(/Requirements/)).toBe("solid");
    await page.getByRole("tab", { name: "Requirements" }).click();
    await expect(page.getByText("Original specification")).toBeVisible();

    // Audit the finished page, not the "Job queued" toast halfway through fading out (its text
    // is mid-opacity then, which axe reads as low contrast).
    await expect(page.locator("[data-sonner-toast]")).toHaveCount(0, { timeout: 10_000 });
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

test.describe("studio details", () => {
  test("an unknown job costs one 404, not a second one for its event stream", async ({ page }) => {
    const notFound: string[] = [];
    page.on("response", (response) => {
      if (response.status() === 404) notFound.push(new URL(response.url()).pathname);
    });
    await page.goto("/app/jobs/does-not-exist");
    await expect(page.getByRole("heading", { level: 1, name: "Job not found" })).toBeVisible();
    await page.waitForTimeout(500);
    expect(notFound).toEqual(["/api/v1/jobs/does-not-exist"]);
  });

  test("focus moves to the new page after creating a job", async ({ page }) => {
    await startJob(page);
    await expect(page.locator("#main")).toBeFocused();
  });

  test("the job page fits a 320px phone, tabs included", async ({ page }) => {
    await startJob(page);
    await expect(page.getByText("Succeeded").first()).toBeVisible({ timeout: 30_000 });
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 800 });
      await expectNoHorizontalOverflow(page);
      const tabs = page.getByRole("tablist", { name: "Job output" });
      // The output section renders a frame after the job loads.
      await expect(tabs.getByRole("tab")).toHaveCount(4);
      const box = await tabs.boundingBox();
      expect(box && box.x + box.width).toBeLessThanOrEqual(width);
      for (const tab of await tabs.getByRole("tab").all()) {
        // Each label fits its segment: no ellipsis, nothing clipped.
        const label = tab.locator(".truncate");
        expect(
          await label.evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
          await tab.innerText(),
        ).toBe(true);
      }
    }
  });

  test("blocked storage still follows the OS appearance", async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(window, "localStorage", {
        get() {
          throw new DOMException("blocked", "SecurityError");
        },
      });
    });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/app");
    await expect(page.locator("html")).toHaveClass(/dark/);
  });

  test("the browser chrome colour follows a manual appearance switch", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/app");
    const colours = () =>
      page.evaluate(() =>
        [...document.querySelectorAll('meta[name="theme-color"]')].map((m) => m.getAttribute("content")),
      );
    expect(new Set(await colours())).toEqual(new Set(["#000000"]));
    await page.getByRole("button", { name: "Switch to light appearance" }).click();
    expect(new Set(await colours())).toEqual(new Set(["#ffffff"]));
  });
});

test.describe("studio motion", () => {
  /** Counts View Transitions the page starts, and records the names in play when it captures. */
  async function watchTransitions(page: Page): Promise<void> {
    await page.addInitScript(() => {
      const w = window as unknown as { __vt: { count: number; names: string[] } };
      w.__vt = { count: 0, names: [] };
      const original = document.startViewTransition?.bind(document);
      if (!original) return;
      document.startViewTransition = ((update: () => void) => {
        w.__vt.count += 1;
        w.__vt.names = [...document.querySelectorAll<HTMLElement>("[style*='view-transition-name']")].map(
          (el) => el.style.viewTransitionName,
        );
        return original(update);
      }) as typeof document.startViewTransition;
    });
  }
  const transitions = (page: Page) =>
    page.evaluate(() => (window as unknown as { __vt: { count: number; names: string[] } }).__vt);

  async function finishedJob(page: Page): Promise<string> {
    // Earlier tests may still hold the three active jobs a key may run (HTTP 429): wait for a slot.
    let id = "";
    await expect
      .poll(
        async () => {
          const response = await page.request.post("/api/v1/jobs", { data: { spec: LIBRARY_SPEC } });
          if (response.status() === 202) id = ((await response.json()) as { id: string }).id;
          return response.status();
        },
        { timeout: 30_000, intervals: [500] },
      )
      .toBe(202);
    await expect
      .poll(
        async () =>
          ((await (await page.request.get(`/api/v1/jobs/${id}`)).json()) as { status: string }).status,
        { timeout: 30_000 },
      )
      .toBe("succeeded");
    return id;
  }

  test("a job card morphs into its page (shared-element view transition)", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const id = await finishedJob(page);
    await watchTransitions(page);
    await page.goto("/app");
    const card = page.getByRole("navigation", { name: "Recent jobs" }).locator(`a[href="/app/jobs/${id}"]`);
    await card.hover();
    await card.click();
    await expect(page).toHaveURL(new RegExp(`/app/jobs/${id}$`));
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const seen = await transitions(page);
    expect(seen.count).toBe(1);
    // The card's status and name were the shared elements leaving the list.
    expect(seen.names.sort()).toEqual(["job-name", "job-status"]);
    // Names exist only during the transition: none is left behind to clash with the next one.
    await expect
      .poll(() => page.evaluate(() => document.querySelectorAll("[style*='view-transition-name:']").length))
      .toBe(0);
  });

  test("with reduced motion a job card opens its page instantly", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    const id = await finishedJob(page);
    await watchTransitions(page);
    await page.goto("/app");
    await page.getByRole("navigation", { name: "Recent jobs" }).locator(`a[href="/app/jobs/${id}"]`).click();
    await expect(page).toHaveURL(new RegExp(`/app/jobs/${id}$`));
    expect((await transitions(page)).count).toBe(0);
  });

  test("the working stage springs forward and settles when it finishes", async ({ page }) => {
    await startJob(page);
    const timeline = page.getByRole("list", { name: "Pipeline progress" });
    const working = timeline.locator("li", { hasText: ": working" }).locator(".rail-node");
    await expect(working).toHaveCount(1);
    // Scaled up towards the spring target (1.08) while that agent works...
    await expect
      .poll(() => working.evaluate((el) => parseFloat(getComputedStyle(el).scale) || 1))
      .toBeGreaterThan(1.04);
    // ...and every node back at rest (the spring settled) once the job is done.
    await expect(timeline.getByText("Reviewer: done")).toBeAttached();
    await expect
      .poll(() =>
        timeline
          .locator(".rail-node")
          .evaluateAll((nodes) => nodes.map((el) => parseFloat(getComputedStyle(el).scale) || 1)),
      )
      .toEqual([1, 1, 1, 1, 1]);
  });
});
