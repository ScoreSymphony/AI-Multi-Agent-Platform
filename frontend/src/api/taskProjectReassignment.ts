import { ControlPlaneError } from "./client";
import type { APIErrorBody, CanonicalTask } from "./types";

export interface TaskProjectReassignmentClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

export class TaskProjectReassignmentClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: TaskProjectReassignmentClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  move(taskId: string, destinationProjectId: string | null): Promise<CanonicalTask> {
    return this.command<CanonicalTask>("task.project.move", taskId, {
      destination_project_id: destinationProjectId,
    });
  }

  private async command<T>(
    command: string,
    resourceRef: string,
    payload: object,
  ): Promise<T> {
    const headers = new Headers({
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Correlation-ID": crypto.randomUUID(),
      "Idempotency-Key": crypto.randomUUID(),
    });
    const response = await this.fetchImpl(
      `${this.baseUrl}/api/v1/commands/${encodeURIComponent(command)}`,
      {
        method: "POST",
        headers,
        credentials: "include",
        body: JSON.stringify({ resource_ref: resourceRef, ...payload }),
      },
    );
    const text = await response.text();
    const body: unknown = text ? safeJson(text) : null;
    if (!response.ok) {
      throw new ControlPlaneError(response.status, normalizeError(response, body));
    }
    return body as T;
  }
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
