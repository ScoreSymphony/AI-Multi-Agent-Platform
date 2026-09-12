import { describe, expect, it, vi } from "vitest";
import { ApiTransport } from "./transport";

function jsonResponse(body: unknown, status = 200, headers: HeadersInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...Object.fromEntries(new Headers(headers)) },
  });
}

function canonicalError(status: number, code: string): Response {
  return jsonResponse(
    {
      code,
      category: status === 401 ? "authentication" : "authorization",
      message: code,
      request_id: `request_${status}`,
      correlation_id: `correlation_${status}`,
      retryable: false,
    },
    status,
  );
}

describe("ApiTransport", () => {
  it("parses JSON success and accepts an empty success body", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ ok: true }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const transport = new ApiTransport({ fetchImpl: fetchImpl as unknown as typeof fetch });

    await expect(transport.request<{ ok: boolean }>("/health")).resolves.toEqual({ ok: true });
    await expect(transport.request<null>("/empty")).resolves.toBeNull();
  });

  it("preserves canonical error envelopes and 401/403 semantics", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(canonicalError(401, "unauthenticated"))
      .mockResolvedValueOnce(canonicalError(403, "forbidden"));
    const transport = new ApiTransport({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      maxReadRetries: 0,
    });

    await expect(transport.request("/auth/me")).rejects.toMatchObject({
      status: 401,
      body: { code: "unauthenticated", request_id: "request_401" },
    });
    await expect(transport.request("/protected")).rejects.toMatchObject({
      status: 403,
      body: { code: "forbidden", request_id: "request_403" },
    });
  });

  it("normalizes malformed error responses and extracts request diagnostics", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response("not-json", {
        status: 422,
        headers: {
          "x-request-id": "request_header",
          "x-correlation-id": "correlation_header",
        },
      }),
    );
    const transport = new ApiTransport({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      maxReadRetries: 0,
    });

    await expect(transport.request("/invalid")).rejects.toMatchObject({
      status: 422,
      requestId: "request_header",
      correlationId: "correlation_header",
      body: {
        code: "invalid_response",
        category: "contract",
        request_id: "request_header",
        correlation_id: "correlation_header",
      },
    });
  });

  it("adds auth, content negotiation and stable correlation/idempotency headers", async () => {
    const calls: RequestInit[] = [];
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      calls.push(init ?? {});
      return jsonResponse({ ok: true });
    });
    const transport = new ApiTransport({
      baseUrl: "https://platform.test/",
      auth: { getAccessToken: async () => "token_1" },
      fetchImpl,
    });

    await transport.request("tasks", {
      method: "POST",
      body: { title: "test" },
      idempotencyKey: "idem_1",
    });

    const headers = new Headers(calls[0]?.headers);
    expect(headers.get("accept")).toBe("application/json");
    expect(headers.get("content-type")).toBe("application/json");
    expect(headers.get("authorization")).toBe("Bearer token_1");
    expect(headers.get("idempotency-key")).toBe("idem_1");
    expect(headers.get("x-correlation-id")).toBeTruthy();
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("https://platform.test/api/v1/tasks");
  });

  it("injects the current CSRF token for cookie mutations and observes rotation", async () => {
    let csrfToken = "csrf_initial";
    const seen: Array<string | null> = [];
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      seen.push(new Headers(init?.headers).get("x-csrf-token"));
      return jsonResponse({ ok: true });
    });
    const transport = new ApiTransport({
      csrf: { getToken: () => csrfToken },
      fetchImpl,
    });

    await transport.request("/tasks/task_1:cancel", { method: "POST" });
    csrfToken = "csrf_rotated";
    await transport.request("/tasks/task_2:cancel", { method: "POST" });

    expect(seen).toEqual(["csrf_initial", "csrf_rotated"]);
  });

  it("does not attach CSRF to bearer-authenticated mutations", async () => {
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get("authorization")).toBe("Bearer token_1");
      expect(headers.has("x-csrf-token")).toBe(false);
      return jsonResponse({ ok: true });
    });
    const transport = new ApiTransport({
      auth: { getAccessToken: async () => "token_1" },
      csrf: { getToken: () => "csrf_should_not_leak" },
      fetchImpl,
    });

    await transport.request("/tasks", { method: "POST", body: {} });
  });

  it("times out consistently with a normalized transport error", async () => {
    const fetchImpl = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      }),
    );
    const transport = new ApiTransport({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      timeoutMs: 5,
      maxReadRetries: 0,
    });

    await expect(transport.request("/slow")).rejects.toMatchObject({
      status: 0,
      body: { code: "request_timeout", category: "transport", retryable: true },
    });
  });

  it("honors caller cancellation without retrying", async () => {
    const fetchImpl = vi.fn();
    const transport = new ApiTransport({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      maxReadRetries: 3,
      retryDelayMs: 0,
    });
    const controller = new AbortController();
    controller.abort();

    await expect(transport.request("/cancelled", { signal: controller.signal })).rejects.toMatchObject({
      status: 0,
      body: { code: "request_aborted", retryable: false },
    });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("retries bounded transient GET failures with the same correlation ID", async () => {
    const correlationIds: string[] = [];
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      correlationIds.push(new Headers(init?.headers).get("x-correlation-id") ?? "");
      if (correlationIds.length === 1) {
        return new Response("temporarily unavailable", { status: 503 });
      }
      return jsonResponse({ ok: true });
    });
    const transport = new ApiTransport({
      fetchImpl,
      maxReadRetries: 1,
      retryDelayMs: 0,
    });

    await expect(transport.request("/health")).resolves.toEqual({ ok: true });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(correlationIds[0]).toBeTruthy();
    expect(correlationIds[1]).toBe(correlationIds[0]);
  });

  it("never blindly retries mutations even on transient failures", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response("temporarily unavailable", { status: 503 }));
    const transport = new ApiTransport({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      maxReadRetries: 3,
      retryDelayMs: 0,
    });

    await expect(
      transport.request("/commands/task.cancel", {
        method: "POST",
        body: { resource_ref: "task_1" },
        idempotencyKey: "idem_1",
      }),
    ).rejects.toMatchObject({ status: 503 });
    expect(fetchImpl).toHaveBeenCalledOnce();
  });
});
