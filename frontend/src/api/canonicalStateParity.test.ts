import { describe, expect, it, vi } from "vitest";

import canonicalResult from "./__fixtures__/canonical-result.json";
import canonicalRun from "./__fixtures__/canonical-run.json";
import canonicalTask from "./__fixtures__/canonical-task.json";
import workflowProgress from "./__fixtures__/workflow-progress.json";
import { ControlPlaneClient } from "./client";
import { getPlanCoordination } from "./workflowProgress";

describe("canonical CLI/Web resource parity", () => {
  it("reads the shared canonical Task snapshot from the same versioned resource route", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(canonicalTask), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const observed = await client.getTask(canonicalTask.id);

    expect(observed).toEqual(canonicalTask);
    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api/v1/tasks/${canonicalTask.id}`);
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("include");
  });

  it("reads the shared canonical Run snapshot from the same versioned resource route", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(canonicalRun), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const observed = await client.getRun(canonicalRun.id);

    expect(observed).toEqual(canonicalRun);
    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api/v1/runs/${canonicalRun.id}`);
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("include");
  });

  it("reads the shared canonical Result snapshot from the same versioned resource route", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(canonicalResult), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const observed = await client.getReference("results", canonicalResult.id);

    expect(observed).toEqual(canonicalResult);
    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api/v1/results/${canonicalResult.id}`);
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("include");
  });

  it("keeps the shared Task Run Result snapshot internally coherent", () => {
    expect(canonicalRun.task_id).toBe(canonicalTask.id);
    expect(canonicalResult.task_id).toBe(canonicalTask.id);
    expect(canonicalTask.run_ids).toEqual([canonicalRun.id]);
    expect(canonicalTask.result_ids).toEqual([canonicalResult.id]);
    expect(canonicalTask.status).toBe(canonicalRun.status);
    expect(canonicalTask.correlation_id).toBe(canonicalRun.correlation_id);
  });

  it("reads the shared #560 workflow snapshot with explicit wait and retry terminal semantics", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(workflowProgress), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const observed = await getPlanCoordination(client, workflowProgress.id);

    expect(observed).toEqual(workflowProgress);
    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api/v1/plan-coordination/${workflowProgress.id}`);
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("include");

    const byId = new Map(observed.steps.map((step) => [step.id, step]));
    expect(byId.get("step_560_external")?.wait_state).toBe("active");
    expect(byId.get("step_560_external")?.wait_external_job_ref).toBe("adapter-job-560-parity");
    expect(byId.get("step_560_expired")?.wait_state).toBe("expired");
    expect(byId.get("step_560_expired")?.wait_approval_id).toBe("approval_560_parity");
    expect(byId.get("step_560_exhausted")?.retry_state).toBe("exhausted");
    expect(byId.get("step_560_not_retryable")?.retry_state).toBe("not_retryable");
  });
});
