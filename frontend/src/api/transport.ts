import type { APIErrorBody } from "./types";

export interface AuthBoundary {
  getAccessToken?: () => Promise<string | null>;
}

export interface CsrfBoundary {
  getToken: () => string | null;
}

export interface ApiTransportOptions {
  baseUrl?: string;
  apiPrefix?: string;
  auth?: AuthBoundary;
  csrf?: CsrfBoundary;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
  maxReadRetries?: number;
  retryDelayMs?: number;
}

export interface ApiRequestOptions {
  method?: string;
  body?: unknown;
  headers?: HeadersInit;
  idempotencyKey?: string;
  signal?: AbortSignal;
  timeoutMs?: number;
  retry?: "default" | "never" | "safe";
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const RETRYABLE_READ_STATUSES = new Set([408, 425, 429, 500, 502, 503, 504]);
const DEFAULT_TIMEOUT_MS = 30_000;
const DEFAULT_MAX_READ_RETRIES = 1;
const DEFAULT_RETRY_DELAY_MS = 100;

export class ControlPlaneError extends Error {
  readonly status: number;
  readonly body: APIErrorBody;

  constructor(status: number, body: APIErrorBody) {
    super(body.message);
    this.name = "ControlPlaneError";
    this.status = status;
    this.body = body;
  }

  get requestId(): string {
    return this.body.request_id;
  }

  get correlationId(): string {
    return this.body.correlation_id;
  }
}

/**
 * Canonical browser HTTP transport for the versioned Control Plane.
 *
 * Domain clients own paths and typed domain models. This layer owns generic
 * HTTP construction, browser auth/CSRF behavior, JSON/error normalization,
 * timeout/cancellation and bounded retry semantics.
 */
export class ApiTransport {
  readonly baseUrl: string;
  readonly apiPrefix: string;

  private readonly auth?: AuthBoundary;
  private readonly csrf?: CsrfBoundary;
  private readonly fetchImpl: typeof fetch;
  private readonly timeoutMs: number;
  private readonly maxReadRetries: number;
  private readonly retryDelayMs: number;

  constructor(options: ApiTransportOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.apiPrefix = normalizeApiPrefix(options.apiPrefix ?? "/api/v1");
    this.auth = options.auth;
    this.csrf = options.csrf;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
    this.timeoutMs = requireNonNegativeFinite(options.timeoutMs ?? DEFAULT_TIMEOUT_MS, "timeoutMs");
    this.maxReadRetries = requireNonNegativeInteger(
      options.maxReadRetries ?? DEFAULT_MAX_READ_RETRIES,
      "maxReadRetries",
    );
    this.retryDelayMs = requireNonNegativeFinite(
      options.retryDelayMs ?? DEFAULT_RETRY_DELAY_MS,
      "retryDelayMs",
    );
  }

  async request<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
    const method = (options.method ?? "GET").toUpperCase();
    const headers = new Headers(options.headers);
    if (!headers.has("Accept")) headers.set("Accept", "application/json");

    const correlationId = headers.get("X-Correlation-ID") ?? crypto.randomUUID();
    headers.set("X-Correlation-ID", correlationId);

    if (options.body !== undefined && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (options.idempotencyKey && !headers.has("Idempotency-Key")) {
      headers.set("Idempotency-Key", options.idempotencyKey);
    }

    const accessToken = await this.auth?.getAccessToken?.();
    if (accessToken && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${accessToken}`);
    }

    const csrfToken = this.csrf?.getToken() ?? null;
    if (
      !SAFE_METHODS.has(method)
      && csrfToken
      && !headers.has("Authorization")
      && !headers.has("X-CSRF-Token")
    ) {
      headers.set("X-CSRF-Token", csrfToken);
    }

    const canRetry = this.canRetry(method, options.retry);
    const maxAttempts = canRetry ? this.maxReadRetries + 1 : 1;
    let attempt = 0;

    while (attempt < maxAttempts) {
      attempt += 1;
      try {
        const response = await this.fetchAttempt(
          `${this.baseUrl}${this.apiPrefix}${normalizePath(path)}`,
          {
            method,
            headers,
            credentials: "include",
            body: options.body === undefined ? undefined : JSON.stringify(options.body),
          },
          options.signal,
          options.timeoutMs ?? this.timeoutMs,
          correlationId,
        );

        const text = await response.text();
        const payload = text ? safeJson(text) : null;
        if (response.ok) return payload as T;

        const error = new ControlPlaneError(
          response.status,
          normalizeError(response, payload, correlationId),
        );
        if (attempt < maxAttempts && shouldRetryResponse(response.status)) {
          await this.retryDelay(attempt, options.signal);
          continue;
        }
        throw error;
      } catch (error) {
        const normalized = normalizeThrownError(error, correlationId);
        if (
          attempt < maxAttempts
          && normalized.body.retryable
          && !options.signal?.aborted
        ) {
          await this.retryDelay(attempt, options.signal);
          continue;
        }
        throw normalized;
      }
    }

    throw transportError(
      "network_failure",
      "transport",
      "Control Plane request failed after bounded retries",
      correlationId,
      true,
    );
  }

  private canRetry(method: string, retry: ApiRequestOptions["retry"]): boolean {
    if (retry === "never") return false;
    if (retry === "safe") return SAFE_METHODS.has(method);
    return method === "GET" || method === "HEAD";
  }

  private async fetchAttempt(
    input: RequestInfo | URL,
    init: RequestInit,
    externalSignal: AbortSignal | undefined,
    timeoutMs: number,
    correlationId: string,
  ): Promise<Response> {
    if (externalSignal?.aborted) {
      throw transportError(
        "request_aborted",
        "transport",
        "Control Plane request was cancelled",
        correlationId,
        false,
      );
    }

    const controller = new AbortController();
    let timedOut = false;
    const onExternalAbort = () => controller.abort();
    externalSignal?.addEventListener("abort", onExternalAbort, { once: true });
    const timer = timeoutMs > 0
      ? setTimeout(() => {
          timedOut = true;
          controller.abort();
        }, timeoutMs)
      : null;

    try {
      return await this.fetchImpl(input, { ...init, signal: controller.signal });
    } catch (error) {
      if (externalSignal?.aborted) {
        throw transportError(
          "request_aborted",
          "transport",
          "Control Plane request was cancelled",
          correlationId,
          false,
        );
      }
      if (timedOut) {
        throw transportError(
          "request_timeout",
          "transport",
          `Control Plane request exceeded ${timeoutMs}ms`,
          correlationId,
          true,
        );
      }
      if (error instanceof ControlPlaneError) throw error;
      throw transportError(
        "network_failure",
        "transport",
        error instanceof Error ? error.message : "Control Plane network request failed",
        correlationId,
        true,
      );
    } finally {
      if (timer !== null) clearTimeout(timer);
      externalSignal?.removeEventListener("abort", onExternalAbort);
    }
  }

  private async retryDelay(attempt: number, signal?: AbortSignal): Promise<void> {
    const delayMs = this.retryDelayMs * attempt;
    if (delayMs <= 0) return;
    if (signal?.aborted) {
      throw transportError(
        "request_aborted",
        "transport",
        "Control Plane request was cancelled",
        "unknown",
        false,
      );
    }
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(resolve, delayMs);
      if (!signal) return;
      const onAbort = () => {
        clearTimeout(timer);
        signal.removeEventListener("abort", onAbort);
        reject(
          transportError(
            "request_aborted",
            "transport",
            "Control Plane request was cancelled",
            "unknown",
            false,
          ),
        );
      };
      signal.addEventListener("abort", onAbort, { once: true });
    });
  }
}

function normalizeApiPrefix(value: string): string {
  const trimmed = value.trim();
  if (!trimmed || trimmed === "/") return "";
  const withLeadingSlash = trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
  return withLeadingSlash.replace(/\/$/, "");
}

function normalizePath(path: string): string {
  return path.startsWith("/") ? path : `/${path}`;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function normalizeError(
  response: Response,
  payload: unknown,
  fallbackCorrelationId: string,
): APIErrorBody {
  if (isErrorBody(payload)) return payload;
  const requestId = response.headers.get("x-request-id") ?? "unknown";
  return {
    code: "invalid_response",
    category: "contract",
    message: `Control Plane returned HTTP ${response.status} without a canonical error envelope`,
    request_id: requestId,
    correlation_id:
      response.headers.get("x-correlation-id") ??
      (requestId === "unknown" ? fallbackCorrelationId : requestId),
    retryable: false,
  };
}

function normalizeThrownError(error: unknown, correlationId: string): ControlPlaneError {
  if (error instanceof ControlPlaneError) return error;
  return transportError(
    "network_failure",
    "transport",
    error instanceof Error ? error.message : "Control Plane network request failed",
    correlationId,
    true,
  );
}

function transportError(
  code: string,
  category: string,
  message: string,
  correlationId: string,
  retryable: boolean,
): ControlPlaneError {
  return new ControlPlaneError(0, {
    code,
    category,
    message,
    request_id: "unknown",
    correlation_id: correlationId,
    retryable,
  });
}

function isErrorBody(value: unknown): value is APIErrorBody {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<APIErrorBody>;
  return (
    typeof candidate.code === "string"
    && typeof candidate.category === "string"
    && typeof candidate.message === "string"
    && typeof candidate.request_id === "string"
    && typeof candidate.correlation_id === "string"
    && typeof candidate.retryable === "boolean"
  );
}

function shouldRetryResponse(status: number): boolean {
  return RETRYABLE_READ_STATUSES.has(status);
}

function requireNonNegativeInteger(value: number, name: string): number {
  if (!Number.isInteger(value) || value < 0) {
    throw new TypeError(`${name} must be a non-negative integer`);
  }
  return value;
}

function requireNonNegativeFinite(value: number, name: string): number {
  if (!Number.isFinite(value) || value < 0) {
    throw new TypeError(`${name} must be a non-negative finite number`);
  }
  return value;
}

export function isControlPlaneError(value: unknown): value is ControlPlaneError {
  return value instanceof ControlPlaneError;
}
