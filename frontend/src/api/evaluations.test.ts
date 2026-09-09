import { describe, expect, it, vi } from "vitest";
import { EvaluationClient } from "./evaluations";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("EvaluationClient", () => {
  it("reads suites and runs only through canonical Control Plane collections", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return jsonResponse({ items: [], next_cursor: null, total: 0, limit: 50 });
    });
    const client = new EvaluationClient({ fetchImpl });

    await client.listSuites({ limit: 25, cursor: "opaque-suite-cursor" });
    await client.listRuns({ limit: 50, cursor: "opaque-run-cursor" });

    expect(calls).toEqual([
      "/api/v1/evaluation-suites?limit=25&cursor=opaque-suite-cursor",
      "/api/v1/evaluation-runs?limit=50&cursor=opaque-run-cursor",
    ]);
  });

  it("preserves canonical versioned suite identity on detail reads", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/v1/evaluation-suites/reference.lifecycle%401.0");
      return jsonResponse({
        id: "reference.lifecycle@1.0",
        type: "evaluation-suite",
        suite_id: "reference.lifecycle",
        version: "1.0",
        name: "Reference lifecycle",
        description: "",
        tags: [],
        cases: [],
      });
    });
    const client = new EvaluationClient({ fetchImpl });

    await client.getSuite("reference.lifecycle@1.0");
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("runs a suite through evaluation.run with immutable snapshot input and idempotency", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/evaluation.run");
      expect(init?.method).toBe("POST");
      expect(init?.credentials).toBe("include");
      const headers = new Headers(init?.headers);
      expect(headers.get("idempotency-key")).toBe("eval-run-key");
      expect(headers.has("x-correlation-id")).toBe(true);
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "reference.lifecycle@1.0",
        snapshot: {
          platform_version: "0.1.0",
          platform_commit: "abc123",
          references: [
            { kind: "executor", ref_id: "reference", version: "1.0", revision: null },
          ],
          environment: [{ key: "mode", value: "test" }],
        },
        repetitions: 1,
        seed: 42,
        baseline_run_id: "baseline-run",
        regression_policy_ref: "reference.pr@1.0",
      });
      return jsonResponse({ id: "evaluation-run-1", type: "evaluation-run" });
    });
    const client = new EvaluationClient({ fetchImpl });

    await client.runSuite(
      "reference.lifecycle@1.0",
      {
        snapshot: {
          platform_version: "0.1.0",
          platform_commit: "abc123",
          references: [{ kind: "executor", ref_id: "reference", version: "1.0" }],
          environment: [{ key: "mode", value: "test" }],
        },
        repetitions: 1,
        seed: 42,
        baseline_run_id: "baseline-run",
        regression_policy_ref: "reference.pr@1.0",
      },
      "eval-run-key",
    );
  });

  it("forwards explicit repeat, seed and candidate comparison policies", async () => {
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "suite@1",
        snapshot: {
          platform_version: "1.0",
          platform_commit: null,
          references: [],
          environment: [],
        },
        repetitions: 3,
        aggregation_policy_ref: "aggregate@1",
        repeat_policy: {
          strategy: "paired_ab",
          repeat_count: 3,
          min_repeats: 1,
          stability_window: null,
          variance_threshold: null,
          version: "1.0",
        },
        seed_policy: {
          mode: "fixed_seed_supported",
          ordered_seeds: [11, 17, 23],
          provider_seed_control: true,
          limitations: [],
          version: "1.0",
        },
        candidate_reference_kinds: ["model"],
        performance_sensitive: true,
      });
      return jsonResponse({ id: "run", type: "evaluation-run" });
    });
    const client = new EvaluationClient({ fetchImpl });

    await client.runSuite("suite@1", {
      snapshot: { platform_version: "1.0" },
      repetitions: 3,
      aggregation_policy_ref: "aggregate@1",
      repeat_policy: {
        strategy: "paired_ab",
        repeat_count: 3,
        min_repeats: 1,
        stability_window: null,
        variance_threshold: null,
        version: "1.0",
      },
      seed_policy: {
        mode: "fixed_seed_supported",
        ordered_seeds: [11, 17, 23],
        provider_seed_control: true,
        limitations: [],
        version: "1.0",
      },
      candidate_reference_kinds: ["model"],
      performance_sensitive: true,
    });
  });

  it("compares runs only through evaluation.compare and forwards manifest controls", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/evaluation.compare");
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "current-run",
        baseline_run_id: "baseline-run",
        regression_policy_ref: "reference.pr@1.0",
        aggregation_policy_ref: "aggregate@1",
        candidate_reference_kinds: ["skill_bundle"],
        performance_sensitive: true,
      });
      return jsonResponse({
        id: "current-run",
        type: "evaluation-comparison",
        current_run_id: "current-run",
        baseline_run_id: "baseline-run",
        policy_id: "reference.pr",
        policy_version: "1.0",
        findings: [],
        regression_count: 0,
        improvement_count: 0,
        manifest_comparison: {
          status: "directly_comparable",
          differences: [],
        },
      });
    });
    const client = new EvaluationClient({ fetchImpl });

    await client.compareRuns(
      "current-run",
      "baseline-run",
      "reference.pr@1.0",
      "eval-compare-key",
      {
        aggregation_policy_ref: "aggregate@1",
        candidate_reference_kinds: ["skill_bundle"],
        performance_sensitive: true,
      },
    );
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("rejects invalid repetitions and ambiguous seed inputs before transport", () => {
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({}));
    const client = new EvaluationClient({ fetchImpl });

    expect(() =>
      client.runSuite("reference.lifecycle@1.0", {
        snapshot: { platform_version: "0.1.0" },
        repetitions: 0,
      }),
    ).toThrow("positive integer");

    expect(() =>
      client.runSuite("reference.lifecycle@1.0", {
        snapshot: { platform_version: "0.1.0" },
        seed: 42,
        seed_policy: {
          mode: "deterministic_no_randomness",
          ordered_seeds: [],
          provider_seed_control: null,
          limitations: [],
          version: "1.0",
        },
      }),
    ).toThrow("alternative inputs");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("requires an aggregation policy for repeated baseline comparisons", () => {
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({}));
    const client = new EvaluationClient({ fetchImpl });

    expect(() =>
      client.runSuite("reference.lifecycle@1.0", {
        snapshot: { platform_version: "0.1.0" },
        baseline_run_id: "baseline-run",
      }),
    ).toThrow("must both be set or both be omitted");

    expect(() =>
      client.runSuite("reference.lifecycle@1.0", {
        snapshot: { platform_version: "0.1.0" },
        repetitions: 2,
        baseline_run_id: "baseline-run",
        regression_policy_ref: "reference.pr@1.0",
      }),
    ).toThrow("aggregation policy");

    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
