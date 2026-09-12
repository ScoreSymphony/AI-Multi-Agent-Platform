import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { ListQuery, Page } from "./types";

export interface ControlPlaneCollectionClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
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
  private readonly transport: ApiTransport;

  constructor(options: ControlPlaneCollectionClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.baseUrl = this.transport.baseUrl;
  }

  list<T>(collection: string, query: ListQuery = {}): Promise<Page<T>> {
    requireCollection(collection);
    return this.transport.request<Page<T>>(`/${collection}${toQuery(query)}`);
  }

  get<T>(collection: string, resourceId: string): Promise<T> {
    requireCollection(collection);
    return this.transport.request<T>(`/${collection}/${encodeURIComponent(resourceId)}`);
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
