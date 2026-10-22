import { describe, expect, it } from "vitest";

import { parseSSE, toJobEvent } from "./sse";

describe("parseSSE", () => {
  it("parses complete events and keeps the partial tail", () => {
    const { events, rest } = parseSSE(
      'retry: 2000\n\nid: 7\nevent: stage.started\ndata: {"stage":"analyze"}\n\nid: 8\nevent: stage.log\ndata: {"mess',
    );
    expect(events).toEqual([{ id: "7", event: "stage.started", data: '{"stage":"analyze"}' }]);
    expect(rest).toBe('id: 8\nevent: stage.log\ndata: {"mess');
  });

  it("ignores comments, joins multi-line data and handles CRLF", () => {
    const { events } = parseSSE(": keep-alive\r\n\r\nevent: x\r\ndata: a\r\ndata: b\r\n\r\n");
    expect(events).toEqual([{ id: null, event: "x", data: "a\nb" }]);
  });

  it("defaults the event name to message", () => {
    expect(parseSSE("data: {}\n\n").events[0]?.event).toBe("message");
  });
});

describe("toJobEvent", () => {
  it("merges the JSON payload with id and event name", () => {
    expect(toJobEvent({ id: "3", event: "job.failed", data: '{"error":"boom"}' })).toEqual({
      id: 3,
      event: "job.failed",
      error: "boom",
    });
  });

  it("returns null for malformed data", () => {
    expect(toJobEvent({ id: null, event: "x", data: "{" })).toBeNull();
  });
});
