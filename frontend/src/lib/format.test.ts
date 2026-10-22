import { describe, expect, it } from "vitest";

import { formatBytes, formatDuration, formatRelative, formatTokens } from "./format";

describe("format", () => {
  it("formats bytes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
  });

  it("formats durations", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(0.0004)).toBe("1 ms");
    expect(formatDuration(0.25)).toBe("250 ms");
    expect(formatDuration(12.34)).toBe("12.3 s");
    expect(formatDuration(125)).toBe("2m 5s");
  });

  it("formats relative times", () => {
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(formatRelative("2026-01-01T11:59:58Z", now)).toBe("just now");
    expect(formatRelative("2026-01-01T11:55:00Z", now)).toBe("5 minutes ago");
    expect(formatRelative("2025-12-31T12:00:00Z", now)).toBe("yesterday");
  });

  it("formats token counts", () => {
    expect(formatTokens(950)).toBe("950");
    expect(formatTokens(12_345)).toBe("12.3k");
  });
});
