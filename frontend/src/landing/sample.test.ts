import { describe, expect, it } from "vitest";

import { AGENTS } from "../lib/stages";
import { CATEGORIES } from "./categories";
import { filesByStage, sample } from "./sample";

// The snapshot itself is drift-checked against the pipeline by tests/test_landing_sample.py.
// These tests keep what the landing page derives from it honest.
describe("landing sample", () => {
  it("files every generated path under exactly one 'What's in the box' category", () => {
    for (const file of sample.files) {
      const owners = CATEGORIES.filter((category) => category.files.includes(file.path));
      expect(
        owners.map((o) => o.name),
        file.path,
      ).toHaveLength(1);
    }
    const total = CATEGORIES.reduce((sum, category) => sum + category.files.length, 0);
    expect(total).toBe(sample.files.length);
  });

  it("agrees with the reviewer's own numbers", () => {
    expect(sample.validation.files_checked).toBe(sample.files.length);
    expect(sample.stages.at(-1)?.summary).toContain(`${sample.review.score}/100`);
  });

  it("matches the stage summaries file counts", () => {
    expect(sample.stages[2]?.summary).toBe(
      `Generated ${filesByStage("implement").length} source and test files`,
    );
    expect(sample.stages[3]?.summary).toContain(`Generated ${filesByStage("devops").length} delivery files`);
  });

  it("uses the backend's agent labels in pipeline order", () => {
    expect(sample.stages.map((s) => s.name)).toEqual(AGENTS.map((a) => a.name));
    expect(sample.stages.map((s) => s.label)).toEqual(AGENTS.map((a) => a.label));
  });

  it("shows only files that the run really produced", () => {
    const paths = new Set(sample.files.map((f) => f.path));
    for (const file of sample.showcase) expect(paths.has(file.path), file.path).toBe(true);
  });
});
