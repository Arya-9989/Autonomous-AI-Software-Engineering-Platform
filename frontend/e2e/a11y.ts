import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

/** Fail on any serious or critical WCAG 2.2 A/AA violation (the project's bar). */
/** Let finite CSS/Web animations (entrances, theme cross-fades) finish so axe sees final colours. */
export async function settle(page: Page): Promise<void> {
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((animation) => animation.effect?.getComputedTiming().iterations !== Infinity)
        .map((animation) => animation.finished.catch(() => undefined)),
    ),
  );
}

export async function expectNoSeriousA11yViolations(page: Page, context: string): Promise<void> {
  await settle(page);
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const blocking = results.violations
    .filter((violation) => violation.impact === "serious" || violation.impact === "critical")
    .map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      targets: violation.nodes.slice(0, 5).map((node) => node.target.join(" ")),
    }));
  expect(blocking, `axe violations on ${context}`).toEqual([]);
}

export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}
