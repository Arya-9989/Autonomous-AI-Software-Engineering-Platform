import { expect, test, type Page } from "@playwright/test";

import sample from "../src/landing/sample.json" with { type: "json" };
import { expectNoHorizontalOverflow, expectNoSeriousA11yViolations } from "./a11y";

const STAGE_TITLES = [
  "Reads the spec. Names the things.",
  "Designs before it writes.",
  "Writes the service. And its tests.",
  "Ready to ship, not just to run.",
  "Checks everything. Repairs what fails.",
];
// The Validation section lists 20 checks (src/landing/checks.ts); the count-up must land on it.
const CHECKS = 20;

/** Scrolls so the pinned story is `progress` (0..1) of the way through, and lets it render. */
async function scrollStory(page: Page, progress: number): Promise<void> {
  await page.evaluate((p) => {
    const track = document.querySelector<HTMLElement>(".story-track")!;
    const top = track.getBoundingClientRect().top + window.scrollY - 52;
    window.scrollTo(0, top + p * (track.offsetHeight - (window.innerHeight - 52)));
  }, progress);
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );
}

/** Rendered opacity of a tree row in the pinned story, including its ancestors'. */
function rowOpacity(page: Page, name: string): Promise<number> {
  return page.evaluate((file) => {
    const rows = [...document.querySelectorAll<HTMLElement>(".story-pinned li.sx-tree")];
    const row = rows.find((li) => li.textContent?.trim() === file);
    let element: HTMLElement | null = row?.querySelector<HTMLElement>(".sx-in") ?? null;
    let opacity = 1;
    while (element) {
      opacity *= Number(getComputedStyle(element).opacity);
      element = element.parentElement;
    }
    return opacity;
  }, name);
}

/** The Validation section's stat for `label` ("Files checked", "Checks"). */
function stat(page: Page, label: string) {
  return page
    .getByTestId("validation-stats")
    .locator("div", { has: page.getByText(label, { exact: true }) })
    .locator("dd");
}

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

  test("local nav marks the section in view, and its underline slides there", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "AutoSWE sections" });
    const output = nav.getByRole("link", { name: "Output" });
    const indicator = nav.locator(".nav-indicator");
    await expect(indicator).toBeAttached();
    await output.click();
    await expect(output).toHaveAttribute("aria-current", "true");
    await expect(indicator).toHaveAttribute("data-slide", "");
    // One underline, moved by transform (not layout), ends exactly under the active link.
    await expect
      .poll(async () => {
        const [line, link] = await Promise.all([indicator.boundingBox(), output.boundingBox()]);
        return line && link ? Math.abs(line.x - link.x) + Math.abs(line.width - link.width) : 99;
      })
      .toBeLessThanOrEqual(1);
  });

  test("the pinned story is scrubbed by scroll and plays backwards", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.locator(".story-pinned")).toBeVisible();
    await expect(page.locator(".story-static")).toHaveCount(0);

    // During the analyst's band no files exist yet.
    await scrollStory(page, 0.12);
    expect(await rowOpacity(page, "models.py")).toBeLessThan(0.05);
    expect(await rowOpacity(page, "Dockerfile")).toBeLessThan(0.05);
    // Near the end of the coder's band its files are in; the DevOps engineer's are not yet.
    await scrollStory(page, 0.56);
    expect(await rowOpacity(page, "models.py")).toBeGreaterThan(0.95);
    expect(await rowOpacity(page, "Dockerfile")).toBeLessThan(0.05);
    // Finished: the whole tree.
    await scrollStory(page, 0.98);
    expect(await rowOpacity(page, "Dockerfile")).toBeGreaterThan(0.95);
    // And back up: scrubbed, not triggered, so it reverses.
    await scrollStory(page, 0.12);
    expect(await rowOpacity(page, "models.py")).toBeLessThan(0.05);
  });

  test("without CSS scroll timelines the fallback scrubs the same story", async ({ page }) => {
    // Emulate a browser without scroll-driven animations (Firefox): the feature test fails and
    // no animation may use a scroll timeline, so only the script's seeking can move anything.
    await page.addInitScript(() => {
      const supports = CSS.supports.bind(CSS);
      CSS.supports = ((...args: [string, string?]) =>
        String(args[0]).includes("animation-timeline")
          ? false
          : supports(...(args as [string]))) as typeof CSS.supports;
      document.addEventListener("DOMContentLoaded", () => {
        const style = document.createElement("style");
        style.textContent = ".story-pinned * { animation-timeline: auto !important; }";
        document.head.append(style);
      });
    });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.locator(".story-pinned[data-scrub]")).toBeVisible();
    await scrollStory(page, 0.12);
    await expect.poll(() => rowOpacity(page, "models.py")).toBeLessThan(0.05);
    await scrollStory(page, 0.56);
    await expect.poll(() => rowOpacity(page, "models.py")).toBeGreaterThan(0.95);
    expect(await rowOpacity(page, "Dockerfile")).toBeLessThan(0.05);
    await scrollStory(page, 0.98);
    await expect.poll(() => rowOpacity(page, "Dockerfile")).toBeGreaterThan(0.95);
  });

  test("swapping in the pinned story moves nothing below it", async ({ page, browser }) => {
    const outputTop = (p: Page) =>
      p.evaluate(() => document.getElementById("output")!.getBoundingClientRect().top + window.scrollY);
    const noScript = await browser.newContext({
      javaScriptEnabled: false,
      viewport: { width: 1440, height: 900 },
    });
    const prerendered = await noScript.newPage();
    await prerendered.goto("/");
    const before = await outputTop(prerendered);
    await noScript.close();
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.locator(".story-pinned")).toBeVisible();
    expect(Math.abs((await outputTop(page)) - before)).toBeLessThanOrEqual(1);
  });

  test("with reduced motion the story is static, complete and nothing is faded", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const story = page.locator("#how-it-works");
    await expect(page.locator(".story-pinned")).toHaveCount(0);
    for (const title of STAGE_TITLES) await expect(story.getByRole("heading", { name: title })).toBeVisible();
    for (const stage of sample.stages) await expect(story.getByText(stage.summary)).toBeVisible();
    await expect(story.getByRole("region", { name: /app\/routers\/books\.py/ })).toContainText(
      "@router.post",
    );
    const faded = await story.evaluate((root) =>
      [...root.querySelectorAll<HTMLElement>("h3, p, li, pre")]
        .filter((el) => el.offsetHeight > 0 && Number(getComputedStyle(el).opacity) < 0.1)
        .map((el) => el.textContent?.slice(0, 40)),
    );
    expect(faded).toEqual([]);
  });

  test("validation numbers count up once and land on the exact values", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const files = stat(page, "Files checked");
    // Below the fold on load: armed at zero, waiting to count.
    await expect(files).toHaveText("0");
    await page.getByTestId("validation-stats").scrollIntoViewIfNeeded();
    await expect(files).toHaveText(String(sample.validation.files_checked));
    await expect(stat(page, "Checks")).toHaveText(String(CHECKS));
    await expect(page.getByTestId("validation-stats").locator("[data-counting]")).toHaveCount(0);
  });

  test("with reduced motion the numbers are final at once", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    await expect(stat(page, "Files checked")).toHaveText(String(sample.validation.files_checked));
    await expect(stat(page, "Checks")).toHaveText(String(CHECKS));
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

  test("the whole story and the final numbers are there without JavaScript", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const story = page.locator("#how-it-works");
    for (const title of STAGE_TITLES) await expect(story.getByRole("heading", { name: title })).toBeVisible();
    for (const stage of sample.stages) await expect(story.getByText(stage.summary)).toBeVisible();
    await expect(story.getByRole("region", { name: /app\/routers\/books\.py/ })).toContainText(
      "@router.post",
    );
    await expect(stat(page, "Files checked")).toHaveText(String(sample.validation.files_checked));
    await expect(stat(page, "Checks")).toHaveText(String(CHECKS));
    // The section in view is marked even though no script measured the links.
    const nav = page.getByRole("navigation", { name: "AutoSWE sections" });
    await expect(nav.getByRole("link", { name: "Overview" }).locator("span")).toHaveCount(1);
  });
});

test("the landing page fits a 320px phone", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await page.goto("/");
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expectNoHorizontalOverflow(page);
});
