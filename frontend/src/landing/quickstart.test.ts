import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { QUICKSTART } from "./quickstart";

const README = readFileSync(fileURLToPath(new URL("../../../README.md", import.meta.url)), "utf8");

describe("product page quickstart", () => {
  it("shows commands that appear verbatim in the README", () => {
    for (const way of QUICKSTART) {
      for (const line of way.command.split("\n")) {
        expect(README, `${way.title}: ${line}`).toContain(line);
      }
    }
  });
});
