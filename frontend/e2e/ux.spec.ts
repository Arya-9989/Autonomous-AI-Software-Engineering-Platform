import { expect, test } from "@playwright/test";

import { expectNoHorizontalOverflow, expectNoSeriousA11yViolations } from "./a11y";

test.use({ hasTouch: true });

test("settings loading and failure keep generation unavailable until retry succeeds", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => (release = resolve));
  let failed = true;
  await page.route("**/api/v1/info", async (route) => {
    await pending;
    if (failed) await route.fulfill({ status: 400, json: { detail: "Settings unavailable" } });
    else await route.continue();
  });
  await page.goto("/app");
  await page.getByRole("button", { name: "Library loans" }).click();
  const generate = page.getByRole("button", { name: "Generate project" });
  await expect(generate).toBeDisabled();
  await expect(page.getByRole("status")).toContainText("Loading project settings");
  release();
  await expect(page.getByRole("alert")).toContainText("Settings unavailable");
  await expect(generate).toBeDisabled();
  failed = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(generate).toBeEnabled();
});

test("specification guidance and pending submission preserve the submitted values", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/v1/jobs", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await pending;
    await route.fulfill({ status: 400, json: { detail: "Try this specification again" } });
  });
  await page.goto("/app");
  const spec = page.getByLabel("Specification");
  await expect(spec).toHaveAccessibleDescription(/At least 20 characters/);
  await spec.fill("Short");
  await expect(spec).toHaveAttribute("aria-invalid", "true");
  await expect(spec).toHaveAccessibleDescription("15 more characters");
  await page.getByRole("button", { name: "Library loans" }).click();
  const submitted = await spec.inputValue();
  await page.getByRole("button", { name: "Generate project" }).click();
  await expect(spec).toBeDisabled();
  await expect(page.getByRole("button", { name: "Clinic bookings" })).toBeDisabled();
  release();
  await expect(page.getByRole("alert")).toContainText("Try this specification again");
  await expect(spec).toBeEnabled();
  await expect(spec).toHaveValue(submitted);
});

test("long server errors wrap on small and landscape screens in both themes", async ({ page }) => {
  await page.route("**/api/v1/info", (route) =>
    route.fulfill({ status: 400, json: { detail: "Unavailable_".repeat(60) } }),
  );
  await page.goto("/app");
  await expect(page.getByRole("alert")).toBeVisible();
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme, reducedMotion: "reduce" });
    for (const viewport of [
      { width: 375, height: 812 },
      { width: 812, height: 375 },
    ]) {
      await page.setViewportSize(viewport);
      await expectNoHorizontalOverflow(page);
      await expectNoSeriousA11yViolations(page, `settings error ${colorScheme} ${viewport.width}`);
    }
  }
});

test("activity stays where the reader scrolled and resumes following at the bottom", async ({ page }) => {
  const response = await page.request.post("/api/v1/jobs", {
    data: { spec: "A library REST API with books and members tracking loans with due dates." },
  });
  expect(response.status()).toBe(202);
  const { id } = (await response.json()) as { id: string };
  await expect
    .poll(
      async () =>
        ((await (await page.request.get(`/api/v1/jobs/${id}`)).json()) as { status: string }).status,
    )
    .toBe("succeeded");
  await page.route(`**/api/v1/jobs/${id}/review`, async (route) => {
    const response = await route.fetch();
    const review = await response.json();
    review.validation.issues = [{ severity: "warning", message: "Check deployment", rule: "deployment" }];
    await route.fulfill({ response, json: review });
  });
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = (input, init) => {
      if (typeof input === "string" && input.endsWith("/events")) {
        const stream = new ReadableStream({
          start(controller) {
            let id = 0;
            const append = () => {
              id += 1;
              controller.enqueue(
                new TextEncoder().encode(
                  `id: ${id}\nevent: stage.log\ndata: ${JSON.stringify({ message: `Log entry ${id}` })}\n\n`,
                ),
              );
            };
            Object.assign(window, { appendActivity: append });
            for (let i = 0; i < 40; i += 1) append();
          },
        });
        return Promise.resolve(new Response(stream, { headers: { "Content-Type": "text/event-stream" } }));
      }
      return original(input, init);
    };
  });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(`/app/jobs/${id}`);
  const log = page.getByRole("log", { name: "Job activity" });
  await expect(log.getByRole("listitem")).toHaveCount(40);
  await log.evaluate((el) => {
    el.scrollTop = 0;
    el.dispatchEvent(new Event("scroll"));
  });
  const append = () =>
    page.evaluate(() => (window as unknown as { appendActivity: () => void }).appendActivity());
  await append();
  await expect(log.getByRole("listitem")).toHaveCount(41);
  expect(await log.evaluate((el) => el.scrollTop)).toBe(0);
  await log.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
    el.dispatchEvent(new Event("scroll"));
  });
  await append();
  await expect(log.getByRole("listitem")).toHaveCount(42);
  await expect
    .poll(() => log.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight))
    .toBeLessThanOrEqual(1);

  const files = page.getByRole("navigation", { name: "Generated files" }).getByRole("button");
  await expect(files.first()).toBeVisible();
  for (const button of await files.all()) {
    expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  }

  await page.getByRole("tab", { name: "Architecture" }).click();
  const endpoints = page.getByRole("region", { name: "API endpoints" });
  await expect(endpoints.getByRole("columnheader", { name: "Method" })).toBeAttached();
  await expect(endpoints.getByRole("columnheader", { name: "Path" })).toBeAttached();
  await endpoints.focus();
  await expect(endpoints).toBeFocused();
  await page.getByRole("tab", { name: "Review" }).click();
  await expect(page.getByText("Warning:", { exact: true })).toBeAttached();
});
