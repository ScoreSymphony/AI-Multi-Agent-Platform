import { ControlPlaneError } from "./client";
import type { APIErrorBody, ListQuery, Page } from "./types";

export interface ControlPlaneCollectionClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

const COLLECTION_PATTERN = /^[a-z0-9][a-z0-9-]*$/;

/**
 * Read-only client for canonical Control Plane extension collections.
 *
 * This preserves the same `/api/v1` boundary as `ControlPlaneClient` while allowing
 * newly composed ResourceService collections to gain typed frontend projections
 * without creating provider-specific clients or backend fallbacks.
 */
export class ControlPlaneCollectionClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ControlPlaneCollectionClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  list<T>(collection: string, query: ListQuery = {}): Promise<Page<T>> {
    requireCollection(collection);
    return this.request<Page<T>>(`/${collection}${toQuery(query)}`);
  }

  get<T>(collection: string, resourceId: string): Promise<T> {
    requireCollection(collection);
    return this.request<T>(`/${collection}/${encodeURIComponent(resourceId)}`);
  }

  private async request<T>(path: string): Promise<T> {
    const headers = new Headers({
      Accept: "application/json",
      "X-Correlation-ID": crypto.randomUUID(),
    });
    const response = await this.fetchImpl(`${this.baseUrl}/api/v1${path}`, {
      method: "GET",
      headers,
      credentials: "include",
    });
    const text = await response.text();
    const payload: unknown = text ? safeJson(text) : null;
    if (!response.ok) {
      throw new ControlPlaneError(response.status, normalizeError(response, payload));
    }
    return payload as T;
  }
}

function requireCollection(collection: string): void {
  if (!COLLECTION_PATTERN.test(collection)) {
    throw new Error(`invalid canonical collection: ${collection}`);
  }
}

function toQuery(query: ListQuery): string {
  const params = new URLSearchParams();
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  if (query.cursor) params.set("cursor", query.cursor);
  if (query.sort) params.set("sort", query.sort);
  if (query.direction) params.set("direction", query.direction);
  if (query.q) params.set("q", query.q);
  for (const [field, value] of Object.entries(query.filters ?? {})) {
    params.set(`filter[${field}]`, value);
  }
  if (query.fields?.length) params.set("fields", query.fields.join(","));
  const text = params.toString();
  return text ? `?${text}` : "";
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function normalizeError(response: Response, payload: unknown): APIErrorBody {
  if (isErrorBody(payload)) return payload;
  const requestId = response.headers.get("x-request-id") ?? "unknown";
  return {
    code: "invalid_response",
    category: "contract",
    message: `Control Plane returned HTTP ${response.status} without a canonical error envelope`,
    request_id: requestId,
    correlation_id: response.headers.get("x-correlation-id") ?? requestId,
    retryable: false,
  };
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
