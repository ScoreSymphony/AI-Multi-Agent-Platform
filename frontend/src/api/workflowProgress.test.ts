import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "./client";
import {
  getPlanCoordination,
  isMissingPlanCoordinationError,
  WorkflowProgressPoller,
  type PlanCoordinationProjection,
} from "./workflowProgress";

const projection: PlanCoordinationProjection = {
  id: "plan_421",
  task_id: "task_421",
  plan_revision: 7,
  steps: [],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

function clientWith(fetchImpl: typeof fetch): ControlPlaneClient {
  return new ControlPlaneClient({
    baseUrl: "https://control.example.test",
    fetchImpl,
  });
}

describe("workflow progress client", () => {
  it("reads the registered coordinator projection through /api/v1 only", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(projection), { status: 200 }),
    );
    const client = clientWith(fetchSpy as unknown as typeof fetch);

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
    const client = clientWith(fetchSpy as unknown as typeof fetch);

    let error: unknown;
    try {
      await getPlanCoordination(client, "plan_421");
    } catch (caught) {
      error = caught;
    }

    expect(isMissingPlanCoordinationError(error)).toBe(true);
    expect(isMissingPlanCoordinationError(new Error("not found"))).toBe(false);
  });

  it("starts with an immediate canonical refresh rather than waiting for the first interval", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(projection), { status: 200 }),
    );
    const seen: PlanCoordinationProjection[] = [];
    const poller = new WorkflowProgressPoller({
      client: clientWith(fetchSpy as unknown as typeof fetch),
      taskId: "task_421",
      planId: "plan_421",
      intervalMs: 60_000,
      onProjection: (value) => seen.push(value),
      onMissing: vi.fn(),
      onError: vi.fn(),
    });

    poller.start();
    await vi.waitFor(() => expect(seen).toHaveLength(1));
    poller.stop();

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(seen[0]?.plan_revision).toBe(7);
  });

  it("discards an older overlapping response so polling cannot regress workflow state", async () => {
    const first = deferred<Response>();
    const second = deferred<Response>();
    const fetchSpy = vi
      .fn()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    const seen: number[] = [];
    const errors: unknown[] = [];
    const poller = new WorkflowProgressPoller({
      client: clientWith(fetchSpy as unknown as typeof fetch),
      taskId: "task_421",
      planId: "plan_421",
      onProjection: (value) => seen.push(value.plan_revision),
      onMissing: vi.fn(),
      onError: (error) => errors.push(error),
    });

    const older = poller.refresh();
    const newer = poller.refresh();
    second.resolve(
      new Response(JSON.stringify({ ...projection, plan_revision: 9 }), { status: 200 }),
    );
    await newer;
    first.resolve(
      new Response(JSON.stringify({ ...projection, plan_revision: 8 }), { status: 200 }),
    );
    await older;
    poller.stop();

    expect(seen).toEqual([9]);
    expect(errors).toEqual([]);
  });

  it("routes canonical 404 polling results to the missing-state callback", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: "not_found",
          category: "resource",
          message: "plan coordination projection not found",
          retryable: false,
        }),
        { status: 404 },
      ),
    );
    const onMissing = vi.fn();
    const onError = vi.fn();
    const poller = new WorkflowProgressPoller({
      client: clientWith(fetchSpy as unknown as typeof fetch),
      taskId: "task_421",
      planId: "plan_421",
      onProjection: vi.fn(),
      onMissing,
      onError,
    });

    await poller.refresh();
    poller.stop();

    expect(onMissing).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
  });

  it("keeps canonical authorization failures distinct from a missing projection", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: "forbidden",
          category: "authorization",
          message: "workflow scope is not authorized",
          retryable: false,
          request_id: "req_forbidden_560",
          correlation_id: "corr_forbidden_560",
        }),
        { status: 403 },
      ),
    );
    const onMissing = vi.fn();
    const onError = vi.fn();
    const poller = new WorkflowProgressPoller({
      client: clientWith(fetchSpy as unknown as typeof fetch),
      taskId: "task_421",
      planId: "plan_421",
      onProjection: vi.fn(),
      onMissing,
      onError,
    });

    await poller.refresh();
    poller.stop();

    expect(onMissing).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(onError.mock.calls)).not.toContain("approval_421");
    expect(JSON.stringify(onError.mock.calls)).not.toContain("adapter-job-421");
  });
});
