import { describe, expect, it } from "vitest";

import type { JobEvent } from "../lib/types";
import { staleQueries } from "./useJobEvents";

const event = (name: string): JobEvent => ({ id: 1, event: name });

describe("queries an event makes stale", () => {
  it("ignores log lines", () => {
    expect(staleQueries(event("stage.log"), "j1")).toEqual([]);
  });

  it("refreshes the job on stage transitions, and its outputs when a stage completes", () => {
    expect(staleQueries(event("stage.started"), "j1")).toEqual([["job", "j1"]]);
    expect(staleQueries(event("stage.completed"), "j1")).toEqual([
      ["job", "j1"],
      ["job", "j1", "files"],
      ["job", "j1", "requirements"],
      ["job", "j1", "architecture"],
      ["job", "j1", "review"],
    ]);
  });

  it("refreshes the job list when the job itself changes state", () => {
    expect(staleQueries(event("job.succeeded"), "j1")).toEqual([["job", "j1"], ["jobs"]]);
  });
});
