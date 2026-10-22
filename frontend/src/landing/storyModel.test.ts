import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { countFrame } from "./countFrame";
import { AGENTS } from "../lib/stages";
import { CHECK_COUNT, GROUPS } from "./checks";
import { COPY_COUNT } from "./StoryCopy";
import { filesByStage, sample } from "./sample";
import {
  bands,
  CODE_FILES,
  codeFollow,
  codeLines,
  PARSE_CHECKS,
  REVIEW_LINES,
  SHIFT,
  treeRows,
  VERDICT,
} from "./storyModel";

const css = readFileSync(new URL("./story.css", import.meta.url), "utf8");
const rows = treeRows();

describe("pinned story timing", () => {
  it("has one copy and one band per agent", () => {
    expect(COPY_COUNT).toBe(AGENTS.length);
  });

  it("gives the five agents equal, ordered bands inside the story", () => {
    expect(bands).toHaveLength(5);
    bands.forEach((band, k) => {
      expect(band.end).toBeGreaterThan(band.start);
      if (k > 0) expect(band.start).toBe(bands[k - 1]!.end);
    });
    expect(bands[0]!.start).toBeGreaterThan(0);
    expect(bands.at(-1)!.end).toBeLessThan(1);
  });

  it("matches the tree-shift keyframes in story.css", () => {
    // sx-tree moves rows at the coder's and the DevOps engineer's band starts.
    for (const k of [2, 3]) {
      const start = Math.round(bands[k]!.start * 100);
      const end = Math.round((bands[k]!.start + SHIFT) * 100);
      expect(css).toMatch(new RegExp(`\\b${start}% \\{\\n\\s+transform`));
      expect(css).toContain(`${end}%,`);
    }
  });
});

describe("pinned story file tree", () => {
  it("shows every generated file once, in its final tree order", () => {
    const files = rows.filter((row) => row.file).map((row) => row.path);
    expect(files.sort()).toEqual(sample.files.map((f) => f.path).sort());
  });

  it("adds each file in the band of the agent that wrote it", () => {
    for (const [k, name] of ["architect", "implement", "devops"].entries()) {
      const stage = k + 1;
      const shown = rows.filter((row) => row.file && row.stage === stage).map((row) => row.path);
      expect(shown.sort()).toEqual(
        filesByStage(name)
          .map((f) => f.path)
          .sort(),
      );
    }
    expect(rows.some((row) => row.stage === 0 || row.stage === 4)).toBe(false);
  });

  it("moves rows by whole lines, and only upwards of their final place", () => {
    // From the DevOps band on (the last that adds rows) every row sits at its final place.
    for (const row of rows) {
      for (const offset of row.offsets) {
        expect(Number.isInteger(offset)).toBe(true);
        expect(offset).toBeLessThanOrEqual(0);
      }
    }
  });

  it("never stacks two visible rows on one line while the tree grows", () => {
    for (const [phase, stage] of [
      [0, 1],
      [1, 2],
    ] as const) {
      const visible = rows
        .map((row, index) => ({ row, line: index + row.offsets[phase] }))
        .filter(({ row }) => row.stage <= stage);
      const lines = visible.map((v) => v.line);
      expect(new Set(lines).size).toBe(lines.length);
      // Packed from the top: the partial tree has no gaps once its rows are in.
      expect(Math.max(...lines)).toBe(visible.length - 1);
    }
  });

  it("brings each row in during its own band, after the tree has made room", () => {
    for (const row of rows) {
      const band = bands[row.stage]!;
      expect(row.enter.a).toBeGreaterThanOrEqual(band.start + SHIFT - 1e-9);
      expect(row.enter.b).toBeLessThanOrEqual(band.end);
    }
  });

  it("ticks every file during the reviewer's band, and never a folder", () => {
    for (const row of rows) {
      if (!row.file) expect(row.check).toBeNull();
      else {
        expect(row.check!.a).toBeGreaterThanOrEqual(bands[4]!.start);
        expect(row.check!.b).toBeLessThanOrEqual(bands[4]!.end);
      }
    }
  });
});

describe("pinned story code and review", () => {
  it("reveals the real files, every line, inside the writing agent's band", () => {
    for (const { path, stage } of CODE_FILES) {
      const file = sample.showcase.find((f) => f.path === path)!;
      const lines = codeLines(path);
      expect(lines.map((l) => l.text).join("\n")).toBe(file.content.replace(/\n$/, ""));
      expect(lines[0]!.at.a).toBeGreaterThan(bands[stage]!.start);
      expect(lines.at(-1)!.at.b).toBeLessThan(bands[stage]!.end);
      for (let i = 1; i < lines.length; i++) expect(lines[i]!.at.a).toBeGreaterThan(lines[i - 1]!.at.a);
    }
  });

  it("scrolls a long file just far enough to show its last line", () => {
    const router = codeLines(CODE_FILES[0].path);
    expect(router.length).toBeGreaterThan(rows.length);
    expect(codeFollow(CODE_FILES[0].path, rows.length)?.rows).toBe(router.length - rows.length);
    expect(codeFollow("Dockerfile", rows.length)).toBeNull();
  });

  it("counts each file type with the parser the reviewer really uses", () => {
    const parsed = PARSE_CHECKS.reduce(
      (sum, check) => sum + sample.files.filter((f) => check.match(f.path)).length,
      0,
    );
    expect(parsed).toBeLessThanOrEqual(sample.files.length);
    expect(REVIEW_LINES.find((l) => l.tool === "secret scan")?.detail).toBe(
      `all ${sample.files.length} files`,
    );
    expect(REVIEW_LINES.find((l) => l.tool === "traceability")?.detail).toContain(
      `${sample.architecture.endpoints.length} of ${sample.architecture.endpoints.length}`,
    );
    expect(VERDICT.a).toBeGreaterThan(REVIEW_LINES.at(-1)!.at.a);
  });
});

describe("count-up", () => {
  it("starts at zero and lands exactly on the static value", () => {
    for (const value of [sample.validation.files_checked, CHECK_COUNT, 1, 100]) {
      expect(countFrame(value, 0)).toBe(0);
      expect(countFrame(value, 1)).toBe(value);
      expect(countFrame(value, 1.4)).toBe(value);
    }
  });

  it("never overshoots or runs backwards", () => {
    let previous = 0;
    for (let t = 0; t <= 1.0001; t += 0.01) {
      const shown = countFrame(32, t);
      expect(shown).toBeGreaterThanOrEqual(previous);
      expect(shown).toBeLessThanOrEqual(32);
      previous = shown;
    }
  });

  it("counts the checks the page lists", () => {
    expect(CHECK_COUNT).toBe(GROUPS.flatMap((group) => group.items).length);
  });
});
