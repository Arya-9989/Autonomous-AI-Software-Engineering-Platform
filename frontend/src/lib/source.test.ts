import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

// Built from its code point so this file does not contain the character it looks for.
const REPLACEMENT = String.fromCharCode(0xfffd);
const SRC = fileURLToPath(new URL("..", import.meta.url));

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return files(path);
    return /\.(tsx?|json|css)$/.test(name) ? [path] : [];
  });
}

describe("UI source text", () => {
  it("contains no U+FFFD replacement characters (a lost encoding shows up on screen)", () => {
    const damaged = files(SRC).filter((path) => readFileSync(path, "utf8").includes(REPLACEMENT));
    expect(damaged).toEqual([]);
  });
});
