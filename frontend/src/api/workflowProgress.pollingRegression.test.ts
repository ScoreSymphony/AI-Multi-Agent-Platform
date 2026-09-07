import { afterEach, describe, expect, it, vi } from "vitest";

import { ControlPlaneClient } from "./client";
import {
  WorkflowProgressPoller,
  type PlanCoordinationProjection,
} from "./workflowProgress";

const projection: PlanCoordinationProjection = {
  id: "plan_polling_regression",
  task_id: "task_polling_regression",
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

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("workflow progress polling regressions", () => {
  it("does not start another automatic poll while the previous request is unresolved", async () => {
    vi.useFakeTimers();
    const first = deferred<Response>();
    const fetchSpy = vi
      .fn()
      .mockImplementationOnce(() => first.promise)
      .mockResolvedValue(
        new Response(JSON.stringify({ ...projection, plan_revision: 8 }), { status: 200 }),
      );
    let observedFirst!: () => void;
    const firstObserved = new Promise<void>((resolve) => {
      observedFirst = resolve;
    });
    const seen: number[] = [];
    const poller = new WorkflowProgressPoller({
      client: clientWith(fetchSpy as unknown as typeof fetch),
      taskId: projection.task_id,
      planId: projection.id,
      intervalMs: 5,
      onProjection: (value) => {
        seen.push(value.plan_revision);
        if (seen.length === 1) observedFirst();
      },
      onMissing: vi.fn(),
      onError: vi.fn(),
    });

    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(25);
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    first.resolve(new Response(JSON.stringify(projection), { status: 200 }));
    await firstObserved;
    expect(seen).toEqual([7]);

    await vi.advanceTimersByTimeAsync(5);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    poller.stop();
  });
});
