import { describe as suite, expect, it } from "vitest";

import type { JobEvent } from "../lib/types";
import { describe } from "./activity";

const event = (fields: Partial<JobEvent> & { event: string }): JobEvent =>
  ({ id: 1, type: fields.event, ...fields }) as JobEvent;

suite("activity log lines", () => {
  it("does not repeat a stage failure on the job's failure line", () => {
    const stage = event({ event: "stage.failed", error: "DevOps engineer: Rate limited" });
    const job = event({ event: "job.failed", error: "DevOps engineer: Rate limited" });
    expect(describe(stage).text).toBe("DevOps engineer: Rate limited");
    expect(describe(job, stage).text).toBe("Job failed");
  });

  it("keeps a job failure that no stage reported (lost heartbeat)", () => {
    const log = event({ event: "stage.log", message: "Writing application code and tests" });
    const job = event({ event: "job.failed", error: "Worker heartbeat lost; job abandoned." });
    expect(describe(job, log).text).toBe("Worker heartbeat lost; job abandoned.");
  });
});
