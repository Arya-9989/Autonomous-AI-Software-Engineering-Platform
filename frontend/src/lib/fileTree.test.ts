import { describe, expect, it } from "vitest";

import { ancestors, buildTree, defaultFile } from "./fileTree";
import type { FileEntry } from "./types";

const entry = (path: string): FileEntry => ({
  path,
  size: 1,
  stage: "implement",
  description: "",
  language: "text",
});

describe("buildTree", () => {
  it("nests paths and sorts folders before files", () => {
    const tree = buildTree(["README.md", "app/main.py", "app/routers/books.py", "Dockerfile"].map(entry));
    expect(tree.children.map((c) => c.name)).toEqual(["app", "Dockerfile", "README.md"]);
    const app = tree.children[0]!;
    expect(app.children.map((c) => c.name)).toEqual(["routers", "main.py"]);
    expect(app.children[0]!.children[0]!.file?.path).toBe("app/routers/books.py");
  });
});

describe("helpers", () => {
  it("lists ancestors", () => {
    expect(ancestors("a/b/c.py")).toEqual(["a", "a/b"]);
    expect(ancestors("top.txt")).toEqual([]);
  });

  it("prefers the README as the default file", () => {
    expect(defaultFile(["app/main.py", "README.md"].map(entry))).toBe("README.md");
    expect(defaultFile(["z.txt"].map(entry))).toBe("z.txt");
    expect(defaultFile([])).toBeNull();
  });
});
