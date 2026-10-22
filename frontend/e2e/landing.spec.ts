import { expect, test } from "@playwright/test";

import sample from "../src/landing/sample.json" with { type: "json" };
import { expectNoHorizontalOverflow, expectNoSeriousA11yViolations } from "./a11y";

test.describe("landing page", () => {
  for (const colorScheme of ["light", "dark"] as const) {
    for (const viewport of [
      { name: "desktop", width: 1440, height: 900 },
      { name: "mobile", width: 390, height: 844 },
    ]) {
      test(`is accessible and fits the screen (${colorScheme}, ${viewport.name})`, async ({ page }) => {
        await page.emulateMedia({ colorScheme, reducedMotion: "reduce" });
        await page.setViewportSize(viewport);
        await page.goto("/");
        await expect(page.getByRole("heading", { level: 1 })).toHaveText(
          /Plain English in\.\s*Deployable project out\./,
        );
        await expectNoHorizontalOverflow(page);
        await expectNoSeriousA11yViolations(page, `landing ${colorScheme} ${viewport.name}`);
      });
    }
  }

  test("shows the real offline run, finished at once with reduced motion", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    const hero = page.locator("#overview");
    await expect(hero.getByText(sample.spec, { exact: false }).first()).toBeVisible();
    for (const stage of sample.stages) {
      await expect(hero.getByText(stage.summary)).toBeVisible();
    }
    await expect(hero.getByText(String(sample.files.length), { exact: true })).toBeVisible();
    await expect(hero.getByText("Succeeded")).toBeVisible();
  });

  test("highlights carousel has a working pager and pause control", async ({ page }) => {
    await page.goto("/");
    const pause = page.getByRole("button", { name: "Pause highlights" });
    await pause.click();
    await expect(page.getByRole("button", { name: "Play highlights" })).toBeVisible();
    const third = page.getByRole("button", { name: "Show highlight 3" });
    await third.click();
    await expect(third).toHaveAttribute("aria-current", "true");
  });

  test("highlights do not autoplay with reduced motion", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Play highlights" })).toBeVisible();
  });

  test("local nav marks the section in view", async ({ page }) => {
    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "AutoSWE sections" });
    await nav.getByRole("link", { name: "Output" }).click();
    await expect(nav.getByRole("link", { name: "Output" })).toHaveAttribute("aria-current", "true");
  });

  test("output viewer shows the generated files byte for byte", async ({ page }) => {
    await page.goto("/#output");
    const files = page.getByRole("tablist", { name: "Generated files" });
    await files.getByRole("tab", { name: /Dockerfile/ }).click();
    const panel = page.getByRole("region", { name: "Dockerfile, generated source" });
    await expect(panel).toContainText("HEALTHCHECK");
    await expect(panel).toContainText("USER 10001");
  });

  test("get started commands are copyable", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto("/#get-started");
    await page.getByRole("button", { name: "Copy the Docker command" }).click();
    await expect(page.getByText("Copied to clipboard")).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("docker compose up --build");
  });

  test("the primary call to action opens the studio", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Try it now" }).click();
    await expect(page).toHaveURL(/\/app$/);
    await expect(page.getByRole("heading", { name: "What should we build?" })).toBeVisible();
  });
});

test.describe("landing page without JavaScript", () => {
  test.use({ javaScriptEnabled: false });

  test("is prerendered: the headline and the real run are in the HTML", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      /Plain English in\.\s*Deployable project out\./,
    );
    await expect(page.getByText(sample.spec, { exact: false }).first()).toBeVisible();
  });
});

test("the landing page fits a 320px phone", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await page.goto("/");
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expectNoHorizontalOverflow(page);
});
