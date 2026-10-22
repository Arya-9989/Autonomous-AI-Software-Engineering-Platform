import { buildTree, type TreeNode } from "../lib/fileTree";
import { AGENTS } from "../lib/stages";
import type { FileEntry } from "../lib/types";
import { sample } from "./sample";

/**
 * Timing and data for the pinned "How it works" story (Story.tsx). Scroll progress through the
 * story runs from 0 to 1; each agent owns one equal band of it, after a short lead-in and before
 * a short hold on the finished project. Every element that moves gets its start and end as
 * fractions of that progress, which the CSS scroll timeline (or the Motion fallback) reads from
 * custom properties. Everything shown is from the real offline run in sample.json.
 */

export const LEAD = 0.04;
export const BAND = 0.18;
/** Band k of agent k: [start, end). The last band ends at 0.94, leaving a hold on the result. */
export const bands = AGENTS.map((_, k) => ({
  start: round(LEAD + k * BAND),
  end: round(LEAD + (k + 1) * BAND),
}));
/** Length of a cross-fade or a tree shift, as a share of the whole story. */
export const FADE = 0.025;
/** Tree rows slide to make room during this window at the start of a band (see sx-tree in index.css). */
export const SHIFT = 0.02;

export function round(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

export interface Range {
  a: number;
  b: number;
}

/** Spread `count` items evenly over [from, to]; each one animates over `length`. */
export function stagger(count: number, from: number, to: number, length: number): Range[] {
  const step = count > 1 ? (to - from - length) / (count - 1) : 0;
  return Array.from({ length: count }, (_, i) => ({
    a: round(from + i * step),
    b: round(from + i * step + length),
  }));
}

const STAGE_INDEX: Record<string, number> = Object.fromEntries(AGENTS.map((agent, i) => [agent.name, i]));

// --- The file tree -------------------------------------------------------------------------------

export interface TreeRow {
  path: string;
  name: string;
  depth: number;
  file: boolean;
  /** The agent whose band first shows this row (a folder appears with its first file). */
  stage: number;
  /** Offset from the row's final position, in rows, while the tree is partial: [architect, coder]. */
  offsets: [number, number];
  /** When the row fades in. */
  enter: Range;
  /** When the reviewer's check mark appears beside a file (null for folders). */
  check: Range | null;
}

function flatten(node: TreeNode, depth: number, out: { node: TreeNode; depth: number }[]) {
  for (const child of node.children) {
    out.push({ node: child, depth });
    if (!child.file) flatten(child, depth + 1, out);
  }
}

function firstStage(node: TreeNode): number {
  if (node.file) return STAGE_INDEX[node.file.stage] ?? 0;
  return Math.min(...node.children.map(firstStage));
}

// --- What the reviewer runs (src/autoswe/pipeline/validation.py, in its order) ------------------

const endpoints = sample.architecture.endpoints.length;
const count = (predicate: (path: string) => boolean) => sample.files.filter((f) => predicate(f.path)).length;
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Parser checks, one per file type; then the checks over the whole project. */
export const PARSE_CHECKS: { tool: string; match: (path: string) => boolean; noun: string }[] = [
  { tool: "ast", match: (p) => p.endsWith(".py"), noun: "Python file" },
  { tool: "json", match: (p) => p.endsWith(".json"), noun: "JSON file" },
  { tool: "tomllib", match: (p) => p.endsWith(".toml"), noun: "TOML file" },
  { tool: "PyYAML", match: (p) => /\.ya?ml$/.test(p), noun: "YAML file" },
  { tool: "python-hcl2", match: (p) => p.endsWith(".tf"), noun: "Terraform file" },
  { tool: "Dockerfile rules", match: (p) => p.split("/").at(-1) === "Dockerfile", noun: "Dockerfile" },
];

export interface ReviewLine {
  tool: string;
  detail: string;
  at: Range;
}

const reviewBand = bands[4]!;
const lineSlots = stagger(PARSE_CHECKS.length + 3, reviewBand.start + 0.012, reviewBand.end - 0.045, 0.012);

export const REVIEW_LINES: ReviewLine[] = [
  ...PARSE_CHECKS.map((check, i) => ({
    tool: check.tool,
    detail: plural(count(check.match), check.noun),
    at: lineSlots[i]!,
  })),
  { tool: "secret scan", detail: `all ${sample.files.length} files`, at: lineSlots[PARSE_CHECKS.length]! },
  {
    tool: "structure",
    detail: "README, manifest, Dockerfile, tests, CI, K8s, Terraform",
    at: lineSlots[PARSE_CHECKS.length + 1]!,
  },
  {
    tool: "traceability",
    detail: `${endpoints} of ${endpoints} designed endpoints in the code`,
    at: lineSlots[PARSE_CHECKS.length + 2]!,
  },
];

/** The verdict lands at the end of the reviewer's band. */
export const VERDICT: Range = { a: round(reviewBand.end - 0.035), b: round(reviewBand.end - 0.01) };

/** A file is ticked when the check that parses it runs; files no parser reads, by the secret scan. */
function checkFor(path: string): Range {
  const index = PARSE_CHECKS.findIndex((check) => check.match(path));
  return REVIEW_LINES[index === -1 ? PARSE_CHECKS.length : index]!.at;
}

export function treeRows(files: Pick<FileEntry, "path" | "stage" | "size">[] = sample.files): TreeRow[] {
  const flat: { node: TreeNode; depth: number }[] = [];
  flatten(buildTree(files.map((f) => ({ ...f, description: "", language: "" }))), 0, flat);
  const stages = flat.map(({ node }) => firstStage(node));
  // Where each row sits while only the rows of stages <= s exist.
  const position = (index: number, s: number) => stages.slice(0, index).filter((stage) => stage <= s).length;

  // Rows of one band fade in one after another, in tree order, just after the tree makes room.
  const entering = new Map<number, Range[]>();
  for (const s of new Set(stages)) {
    const n = stages.filter((stage) => stage === s).length;
    const band = bands[s]!;
    entering.set(
      s,
      stagger(n, band.start + SHIFT, band.start + SHIFT + Math.min(0.11, 0.008 * n + 0.02), 0.012),
    );
  }
  const seen = new Map<number, number>();

  return flat.map(({ node, depth }, index) => {
    const stage = stages[index]!;
    const nth = seen.get(stage) ?? 0;
    seen.set(stage, nth + 1);
    // Rows that have not appeared yet sit at their position for the band they appear in, so
    // they never travel while invisible.
    const at = (s: number) => position(index, Math.max(s, stage)) - index;
    return {
      path: node.path,
      name: node.name,
      depth,
      file: Boolean(node.file),
      stage,
      offsets: [at(1), at(2)],
      enter: entering.get(stage)![nth]!,
      check: node.file ? checkFor(node.path) : null,
    };
  });
}

// --- Code the agents write, revealed line by line ------------------------------------------------

export const CODE_FILES = [
  { stage: 2, path: "app/models.py" },
  { stage: 3, path: "Dockerfile" },
] as const;

/** Lines visible in the code pane; it equals the tree's row count so both panes share a height. */
export function codeLines(path: string): { text: string; at: Range }[] {
  const file = sample.showcase.find((f) => f.path === path);
  if (!file) throw new Error(`story: ${path} is not in the sample showcase`);
  const stage = CODE_FILES.find((f) => f.path === path)!.stage;
  const band = bands[stage]!;
  const lines = file.content.replace(/\n$/, "").split("\n");
  const slots = stagger(lines.length, band.start + 0.02, band.end - FADE - 0.012, 0.01);
  return lines.map((text, i) => ({ text, at: slots[i]! }));
}

/** When the code pane scrolls to keep the newest lines in view (the second half of the reveal). */
export function codeFollow(path: string, visible: number): { rows: number; at: Range } | null {
  const lines = codeLines(path);
  const overflow = lines.length - visible;
  if (overflow <= 0) return null;
  // Starts as the line that fills the last visible row appears; the newest line then stays at the bottom.
  return { rows: overflow, at: { a: lines[visible - 1]!.at.a, b: lines.at(-1)!.at.b } };
}
