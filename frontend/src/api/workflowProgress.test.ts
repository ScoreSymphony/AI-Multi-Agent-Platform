import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "./client";
import {
  getPlanCoordination,
  isMissingPlanCoordinationError,
  type PlanCoordinationProjection,
} from "./workflowProgress";

const projection: PlanCoordinationProjection = {
  id: "plan_421",
  task_id: "task_421",
  plan_revision: 7,
  steps: [],
};

describe("workflow progress client", () => {
  it("reads the registered coordinator projection through /api/v1 only", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(projection), { status: 200 }),
    );
    const client = new ControlPlaneClient({
      baseUrl: "https://control.example.test",
      fetchImpl: fetchSpy as unknown as typeof fetch,
    });

    const result = await getPlanCoordination(client, "plan_421");

    expect(result).toEqual(projection);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, options] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://control.example.test/api/v1/plan-coordination/plan_421");
    expect(options.method).toBe("GET");
    expect(options.credentials).toBe("include");
  });

  it("classifies only canonical not-found responses as an absent coordination projection", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: "not_found",
          category: "resource",
          message: "plan coordination projection not found",
          retryable: false,
          request_id: "req_421",
          correlation_id: "corr_421",
        }),
        { status: 404 },
      ),
    );
    const client = new ControlPlaneClient({
      baseUrl: "https://control.example.test",
      fetchImpl: fetchSpy as unknown as typeof fetch,
    });

    let error: unknown;
    try {
      await getPlanCoordination(client, "plan_421");
    } catch (caught) {
      error = caught;
    }

    expect(isMissingPlanCoordinationError(error)).toBe(true);
    expect(isMissingPlanCoordinationError(new Error("not found"))).toBe(false);
  });
});
