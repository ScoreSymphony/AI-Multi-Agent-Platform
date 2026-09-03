import type {
  APIErrorBody,
  APImanifest,
  CanonicalModel,
  CanonicalModelProvider,
  CanonicalProject,
  CanonicalRun,
  CanonicalTask,
  CanonicalUsageAggregate,
  CanonicalUsageBudget,
  CanonicalUsageRecord,
  CanonicalWorkspaceIdentity,
  CreateProjectInput,
  CreateTaskInput,
  CreateWorkspaceInput,
  HealthStatus,
  JsonValue,
  ListQuery,
  Page,
  TimelineItem,
} from "./types";

export interface AuthBoundary {
  getAccessToken?: () => Promise<string | null>;
}

export interface ControlPlaneClientOptions {
  baseUrl?: string;
  auth?: AuthBoundary;
  fetchImpl?: typeof fetch;
}

export class ControlPlaneError extends Error {
  readonly status: number;
  readonly body: APIErrorBody;

  constructor(status: number, body: APIErrorBody) {
    super(body.message);
    this.name = "ControlPlaneError";
    this.status = status;
    this.body = body;
  }
}

export class ControlPlaneClient {
  readonly baseUrl: string;
  private readonly auth?: AuthBoundary;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ControlPlaneClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.auth = options.auth;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  manifest(): Promise<APImanifest> {
    return this.request<APImanifest>("/");
  }

  health(): Promise<HealthStatus> {
    return this.request<HealthStatus>("/health");
  }

  readiness(): Promise<HealthStatus> {
    return this.request<HealthStatus>("/readiness");
  }

  listProjects(query: ListQuery = {}): Promise<Page<CanonicalProject>> {
    return this.request<Page<CanonicalProject>>(`/projects${toQuery(query)}`);
  }

  getProject(projectId: string): Promise<CanonicalProject> {
    return this.request<CanonicalProject>(`/projects/${encodeURIComponent(projectId)}`);
  }

  createProject(input: CreateProjectInput): Promise<CanonicalProject> {
    return this.command<CanonicalProject>("/projects", { method: "POST", body: input });
  }

  listWorkspaces(query: ListQuery = {}): Promise<Page<CanonicalWorkspaceIdentity>> {
    return this.request<Page<CanonicalWorkspaceIdentity>>(`/workspaces${toQuery(query)}`);
  }

  getWorkspace(workspaceId: string): Promise<CanonicalWorkspaceIdentity> {
    return this.request<CanonicalWorkspaceIdentity>(`/workspaces/${encodeURIComponent(workspaceId)}`);
  }

  createWorkspace(input: CreateWorkspaceInput): Promise<CanonicalWorkspaceIdentity> {
    return this.command<CanonicalWorkspaceIdentity>("/workspaces", { method: "POST", body: input });
  }

  listTasks(query: ListQuery = {}): Promise<Page<CanonicalTask>> {
    return this.request<Page<CanonicalTask>>(`/tasks${toQuery(query)}`);
  }

  getTask(taskId: string): Promise<CanonicalTask> {
    return this.request<CanonicalTask>(`/tasks/${encodeURIComponent(taskId)}`);
  }

  createTask(input: CreateTaskInput): Promise<CanonicalTask> {
    return this.command<CanonicalTask>("/tasks", { method: "POST", body: input });
  }

  queueTask(taskId: string): Promise<CanonicalTask> {
    return this.command<CanonicalTask>(`/tasks/${encodeURIComponent(taskId)}:queue`);
  }

  startTask(taskId: string): Promise<CanonicalRun> {
    return this.command<CanonicalRun>(`/tasks/${encodeURIComponent(taskId)}:start`);
  }

  cancelTask(taskId: string): Promise<CanonicalTask> {
    return this.command<CanonicalTask>(`/tasks/${encodeURIComponent(taskId)}:cancel`);
  }

  retryTask(taskId: string): Promise<CanonicalRun> {
    return this.command<CanonicalRun>(`/tasks/${encodeURIComponent(taskId)}:retry`);
  }

  listRuns(query: ListQuery = {}): Promise<Page<CanonicalRun>> {
    return this.request<Page<CanonicalRun>>(`/runs${toQuery(query)}`);
  }

  listTaskRuns(taskId: string, query: ListQuery = {}): Promise<Page<CanonicalRun>> {
    return this.request<Page<CanonicalRun>>(
      `/tasks/${encodeURIComponent(taskId)}/runs${toQuery(query)}`,
    );
  }

  getRun(runId: string): Promise<CanonicalRun> {
    return this.request<CanonicalRun>(`/runs/${encodeURIComponent(runId)}`);
  }

  cancelRun(taskId: string, runId: string): Promise<CanonicalRun> {
    return this.command<CanonicalRun>(
      `/tasks/${encodeURIComponent(taskId)}/runs/${encodeURIComponent(runId)}:cancel`,
    );
  }

  timeline(taskId: string, query: ListQuery = {}): Promise<Page<TimelineItem>> {
    return this.request<Page<TimelineItem>>(
      `/tasks/${encodeURIComponent(taskId)}/timeline${toQuery(query)}`,
    );
  }

  listModels(query: ListQuery = {}): Promise<Page<CanonicalModel>> {
    return this.request<Page<CanonicalModel>>(`/models${toQuery(query)}`);
  }

  getModel(modelIdOrAlias: string): Promise<CanonicalModel> {
    return this.request<CanonicalModel>(`/models/${encodeURIComponent(modelIdOrAlias)}`);
  }

  enableModel(modelIdOrAlias: string): Promise<CanonicalModel> {
    return this.command<CanonicalModel>(`/models/${encodeURIComponent(modelIdOrAlias)}:enable`);
  }

  disableModel(modelIdOrAlias: string): Promise<CanonicalModel> {
    return this.command<CanonicalModel>(`/models/${encodeURIComponent(modelIdOrAlias)}:disable`);
  }

  listModelProviders(query: ListQuery = {}): Promise<Page<CanonicalModelProvider>> {
    return this.request<Page<CanonicalModelProvider>>(`/model-providers${toQuery(query)}`);
  }

  getModelProvider(providerId: string): Promise<CanonicalModelProvider> {
    return this.request<CanonicalModelProvider>(
      `/model-providers/${encodeURIComponent(providerId)}`,
    );
  }

  enableModelProvider(providerId: string): Promise<CanonicalModelProvider> {
    return this.command<CanonicalModelProvider>(
      `/model-providers/${encodeURIComponent(providerId)}:enable`,
    );
  }

  disableModelProvider(providerId: string): Promise<CanonicalModelProvider> {
    return this.command<CanonicalModelProvider>(
      `/model-providers/${encodeURIComponent(providerId)}:disable`,
    );
  }

  refreshModelProviderHealth(providerId: string): Promise<CanonicalModelProvider> {
    return this.command<CanonicalModelProvider>(
      `/model-providers/${encodeURIComponent(providerId)}:refresh-health`,
    );
  }

  listUsageRecords(query: ListQuery = {}): Promise<Page<CanonicalUsageRecord>> {
    return this.request<Page<CanonicalUsageRecord>>(`/usage-records${toQuery(query)}`);
  }

  listUsageAggregates(query: ListQuery = {}): Promise<Page<CanonicalUsageAggregate>> {
    return this.request<Page<CanonicalUsageAggregate>>(`/usage-aggregates${toQuery(query)}`);
  }

  listUsageBudgets(query: ListQuery = {}): Promise<Page<CanonicalUsageBudget>> {
    return this.request<Page<CanonicalUsageBudget>>(`/usage-budgets${toQuery(query)}`);
  }

  private command<T>(
    path: string,
    options: { method?: string; body?: unknown } = {},
  ): Promise<T> {
    return this.request<T>(path, {
      method: options.method ?? "POST",
      body: options.body,
      idempotencyKey: crypto.randomUUID(),
    });
  }

  private async request<T>(
    path: string,
    options: {
      method?: string;
      body?: unknown;
      idempotencyKey?: string;
    } = {},
  ): Promise<T> {
    const headers = new Headers({
      Accept: "application/json",
      "X-Correlation-ID": crypto.randomUUID(),
    });
    if (options.body !== undefined) {
      headers.set("Content-Type", "application/json");
    }
    if (options.idempotencyKey) {
      headers.set("Idempotency-Key", options.idempotencyKey);
    }
    const accessToken = await this.auth?.getAccessToken?.();
    if (accessToken) {
      headers.set("Authorization", `Bearer ${accessToken}`);
    }

    const response = await this.fetchImpl(`${this.baseUrl}/api/v1${path}`, {
      method: options.method ?? "GET",
      headers,
      credentials: "include",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });

    const text = await response.text();
    const payload: unknown = text ? safeJson(text) : null;
    if (!response.ok) {
      throw new ControlPlaneError(response.status, normalizeError(response, payload));
    }
    return payload as T;
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
  if (isErrorBody(payload)) {
    return payload;
  }
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
    typeof candidate.code === "string" &&
    typeof candidate.category === "string" &&
    typeof candidate.message === "string" &&
    typeof candidate.request_id === "string" &&
    typeof candidate.correlation_id === "string" &&
    typeof candidate.retryable === "boolean"
  );
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

export function isControlPlaneError(value: unknown): value is ControlPlaneError {
  return value instanceof ControlPlaneError;
}

export function prettyJson(value: Record<string, JsonValue> | JsonValue): string {
  return JSON.stringify(value, null, 2);
}
