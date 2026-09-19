import { describe, expect, it, vi } from "vitest";

import { matchPath } from "../app/router";

import canonicalApiError from "./__fixtures__/canonical-api-error.json";
import canonicalCoreLifecycleRoutes from "./__fixtures__/canonical-core-lifecycle-routes.json";
import canonicalErrorCases from "./__fixtures__/canonical-error-cases.json";
import canonicalMarketplace from "./__fixtures__/canonical-marketplace.json";
import canonicalResult from "./__fixtures__/canonical-result.json";
import canonicalRetryableApiError from "./__fixtures__/canonical-retryable-api-error.json";
import canonicalRun from "./__fixtures__/canonical-run.json";
import canonicalTask from "./__fixtures__/canonical-task.json";
import canonicalTaskPage from "./__fixtures__/canonical-task-page.json";
import workflowProgress from "./__fixtures__/workflow-progress.json";
import { ControlPlaneClient } from "./client";
import { RegistryClient } from "./registry";
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

  it("preserves shared pagination, filter and sort semantics for Task lists", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(canonicalTaskPage), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const observed = await client.listTasks({
      limit: 1,
      cursor: "cursor_client_parity",
      sort: "updated_at",
      direction: "desc",
      q: "Shared canonical",
      filters: { status: "succeeded" },
      fields: ["id", "status"],
    });

    expect(observed).toEqual(canonicalTaskPage);
    expect(observed.items).toEqual([canonicalTask]);
    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const parsed = new URL(url, "http://control-plane.invalid");
    expect(parsed.pathname).toBe("/api/v1/tasks");
    expect(parsed.searchParams.get("limit")).toBe("1");
    expect(parsed.searchParams.get("cursor")).toBe("cursor_client_parity");
    expect(parsed.searchParams.get("sort")).toBe("updated_at");
    expect(parsed.searchParams.get("direction")).toBe("desc");
    expect(parsed.searchParams.get("q")).toBe("Shared canonical");
    expect(parsed.searchParams.get("filter[status]")).toBe("succeeded");
    expect(parsed.searchParams.get("fields")).toBe("id,status");
    expect(init.method).toBe("GET");
  });

  it("preserves canonical authorization error semantics instead of inventing Web errors", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(canonicalApiError), {
        status: 403,
        headers: {
          "content-type": "application/json",
          "x-request-id": canonicalApiError.request_id,
          "x-correlation-id": canonicalApiError.correlation_id,
        },
      }),
    );
    const client = new ControlPlaneClient({
      fetchImpl: fetchSpy as unknown as typeof fetch,
      maxReadRetries: 0,
    });

    await expect(client.getTask(canonicalTask.id)).rejects.toMatchObject({
      status: 403,
      body: canonicalApiError,
    });
    expect(fetchSpy).toHaveBeenCalledOnce();
  });


  it("uses the shared public routes for core Task and Run lifecycle mutations", async () => {
    const fetchSpy = vi.fn().mockImplementation(async () => (
      new Response("{}", {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    ));
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.queueTask(canonicalTask.id);
    await client.startTask(canonicalTask.id);
    await client.cancelTask(canonicalTask.id);
    await client.retryTask(canonicalTask.id);
    await client.cancelRun(canonicalTask.id, canonicalRun.id);

    const observedRoutes = fetchSpy.mock.calls.map(([url, init]) => ({
      method: (init as RequestInit).method,
      path: url as string,
    }));
    expect(observedRoutes).toEqual(Object.values(canonicalCoreLifecycleRoutes));
  });


  it("resolves a canonical Task deep link through the public API", async () => {
    const deepLink = `/tasks/${canonicalTask.id}`;
    const match = matchPath("/tasks/:taskId", deepLink);
    expect(match).not.toBeNull();
    expect(match?.taskId).toBe(canonicalTask.id);

    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(canonicalTask), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const observed = await client.getTask(match!.taskId);

    expect(observed.id).toBe(canonicalTask.id);
    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(fetchSpy.mock.calls[0]?.[0]).toBe(`/api/v1/tasks/${canonicalTask.id}`);
  });

  it("reloads canonical Task state instead of retaining a Web-owned lifecycle snapshot", async () => {
    const refreshedTask = {
      ...canonicalTask,
      revision: canonicalTask.revision + 1,
      status: "failed",
      updated_at: "2026-09-03T17:02:00+00:00",
    };
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(canonicalTask), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(refreshedTask), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const initial = await client.getTask(canonicalTask.id);
    const refreshed = await client.getTask(canonicalTask.id);

    expect(initial.revision).toBe(canonicalTask.revision);
    expect(refreshed.revision).toBe(canonicalTask.revision + 1);
    expect(refreshed.status).toBe("failed");
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(fetchSpy.mock.calls.map(([url]) => url)).toEqual([
      `/api/v1/tasks/${canonicalTask.id}`,
      `/api/v1/tasks/${canonicalTask.id}`,
    ]);
  });


  it("sends one idempotent mutation attempt even when the canonical error is retryable", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(canonicalRetryableApiError), {
        status: 503,
        headers: {
          "content-type": "application/json",
          "x-request-id": canonicalRetryableApiError.request_id,
          "x-correlation-id": canonicalRetryableApiError.correlation_id,
        },
      }),
    );
    const client = new ControlPlaneClient({
      fetchImpl: fetchSpy as unknown as typeof fetch,
      maxReadRetries: 5,
      retryDelayMs: 0,
    });

    await expect(client.queueTask(canonicalTask.id)).rejects.toMatchObject({
      status: 503,
      body: canonicalRetryableApiError,
    });

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api/v1/tasks/${canonicalTask.id}:queue`);
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("Idempotency-Key")).toBeTruthy();
  });


  it("preserves canonical not-found semantics", async () => {
    const fixture = canonicalErrorCases.not_found;
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(fixture.body), {
        status: fixture.status,
        headers: {
          "content-type": "application/json",
          "x-request-id": fixture.body.request_id,
          "x-correlation-id": fixture.body.correlation_id,
        },
      }),
    );
    const client = new ControlPlaneClient({
      fetchImpl: fetchSpy as unknown as typeof fetch,
      maxReadRetries: 0,
    });

    await expect(client.getTask(canonicalTask.id)).rejects.toMatchObject({
      status: fixture.status,
      body: fixture.body,
    });
    expect(fetchSpy).toHaveBeenCalledOnce();
  });

  it("preserves canonical conflict semantics for lifecycle mutations", async () => {
    const fixture = canonicalErrorCases.conflict;
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(fixture.body), {
        status: fixture.status,
        headers: {
          "content-type": "application/json",
          "x-request-id": fixture.body.request_id,
          "x-correlation-id": fixture.body.correlation_id,
        },
      }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await expect(client.queueTask(canonicalTask.id)).rejects.toMatchObject({
      status: fixture.status,
      body: fixture.body,
    });
    expect(fetchSpy).toHaveBeenCalledOnce();
  });

  it("preserves shared Marketplace pagination, filtering, sorting and canonical IDs", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(canonicalMarketplace.page), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const client = new RegistryClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const observed = await client.list({
      limit: 1,
      cursor: "marketplace-parity-cursor",
      sort: "name",
      direction: "asc",
      q: "Research",
      filters: {
        item_type: "agent",
        deprecated: "false",
        source: "official",
      },
    });

    expect(observed).toEqual(canonicalMarketplace.page);
    expect(observed.items[0]?.item_id).toBe(canonicalMarketplace.item.item_id);
    expect(observed.items[0]?.qualified_id).toBe(canonicalMarketplace.item.qualified_id);
    expect(fetchSpy).toHaveBeenCalledOnce();

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const parsed = new URL(url, "http://control-plane.invalid");
    expect(parsed.pathname).toBe("/api/v1/registry-items");
    expect(parsed.searchParams.get("limit")).toBe("1");
    expect(parsed.searchParams.get("cursor")).toBe("marketplace-parity-cursor");
    expect(parsed.searchParams.get("sort")).toBe("name");
    expect(parsed.searchParams.get("direction")).toBe("asc");
    expect(parsed.searchParams.get("q")).toBe("Research");
    expect(parsed.searchParams.get("filter[item_type]")).toBe("agent");
    expect(parsed.searchParams.get("filter[deprecated]")).toBe("false");
    expect(parsed.searchParams.get("filter[source]")).toBe("official");
    expect(init.method).toBe("GET");
  });

  it("installs through the canonical Marketplace command then rereads owner-backed state", async () => {
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(canonicalMarketplace.mutation), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(canonicalMarketplace.installed_item), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    const client = new RegistryClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const mutation = await client.install(
      canonicalMarketplace.item.item_id,
      canonicalMarketplace.item.version,
      "marketplace-parity-idempotency",
      canonicalMarketplace.item.source_registry,
    );
    const installed = await client.get(
      canonicalMarketplace.item.item_id,
      canonicalMarketplace.item.version,
      canonicalMarketplace.item.source_registry,
    );

    expect(mutation).toEqual(canonicalMarketplace.mutation);
    expect(installed).toEqual(canonicalMarketplace.installed_item);
    expect(installed.item_id).toBe(canonicalMarketplace.item.item_id);
    expect(installed.item_type).toBe(canonicalMarketplace.item.item_type);
    expect(installed.version).toBe(canonicalMarketplace.item.version);
    expect(installed.owner_extension?.status).toEqual(
      canonicalMarketplace.installed_item.owner_extension.status,
    );

    const [mutationUrl, mutationInit] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(mutationUrl).toBe("/api/v1/commands/marketplace.install");
    expect(mutationInit.method).toBe("POST");
    expect(new Headers(mutationInit.headers).get("Idempotency-Key")).toBe(
      "marketplace-parity-idempotency",
    );
    expect(JSON.parse(String(mutationInit.body))).toEqual({
      resource_ref: canonicalMarketplace.item.item_id,
      version: canonicalMarketplace.item.version,
      source_registry: canonicalMarketplace.item.source_registry,
    });

    const [readUrl, readInit] = fetchSpy.mock.calls[1] as [string, RequestInit];
    expect(readUrl).toBe(
      `/api/v1/registry-items/${encodeURIComponent(canonicalMarketplace.item.qualified_id)}`,
    );
    expect(readInit.method).toBe("GET");
  });

  it("preserves canonical Marketplace deprecated and unsupported state", async () => {
    const deprecated = canonicalMarketplace.deprecated_item;
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(deprecated), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const client = new RegistryClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const observed = await client.get(
      deprecated.item_id,
      deprecated.version,
      deprecated.source_registry,
    );

    expect(observed).toEqual(deprecated);
    expect(observed.deprecated).toBe(true);
    expect(observed.operation_state).toBe("unsupported");
    expect(observed.route_available).toBe(false);
    expect(observed.owner_extension?.supported_operations).toEqual([]);
    expect(fetchSpy).toHaveBeenCalledOnce();
  });

  it("preserves canonical Marketplace failure categories without Web reinterpretation", async () => {
    const cases = [
      canonicalMarketplace.errors.authorization,
      canonicalMarketplace.errors.validation,
      canonicalMarketplace.errors.unsupported,
      canonicalMarketplace.errors.unavailable,
    ];

    for (const fixture of cases) {
      const fetchSpy = vi.fn().mockResolvedValue(
        new Response(JSON.stringify(fixture.body), {
          status: fixture.status,
          headers: {
            "content-type": "application/json",
            "x-request-id": fixture.body.request_id,
            "x-correlation-id": fixture.body.correlation_id,
          },
        }),
      );
      const client = new RegistryClient({
        fetchImpl: fetchSpy as unknown as typeof fetch,
        maxReadRetries: 5,
        retryDelayMs: 0,
      });

      await expect(
        client.install(
          canonicalMarketplace.item.item_id,
          canonicalMarketplace.item.version,
          "marketplace-failure-idempotency",
          canonicalMarketplace.item.source_registry,
        ),
      ).rejects.toMatchObject({
        status: fixture.status,
        body: fixture.body,
      });
      expect(fetchSpy).toHaveBeenCalledOnce();
    }

    for (const fixture of [
      canonicalMarketplace.errors.not_found,
      canonicalMarketplace.errors.conflict,
    ]) {
      const fetchSpy = vi.fn().mockResolvedValue(
        new Response(JSON.stringify(fixture.body), {
          status: fixture.status,
          headers: { "content-type": "application/json" },
        }),
      );
      const client = new RegistryClient({
        fetchImpl: fetchSpy as unknown as typeof fetch,
        maxReadRetries: 0,
      });

      await expect(
        client.get(
          canonicalMarketplace.item.item_id,
          canonicalMarketplace.item.version,
          canonicalMarketplace.item.source_registry,
        ),
      ).rejects.toMatchObject({
        status: fixture.status,
        body: fixture.body,
      });
      expect(fetchSpy).toHaveBeenCalledOnce();
    }
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
