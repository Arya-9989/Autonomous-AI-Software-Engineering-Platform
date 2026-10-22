import { describe, expect, it } from "vitest";

import { archiveName } from "./api";

describe("archive file name", () => {
  it("uses the name the server put in Content-Disposition", () => {
    expect(archiveName('attachment; filename="caf-orders-api.zip"')).toBe("caf-orders-api.zip");
  });

  it("falls back when the header is missing or has no filename", () => {
    expect(archiveName(null)).toBe("project.zip");
    expect(archiveName("attachment")).toBe("project.zip");
  });
});
