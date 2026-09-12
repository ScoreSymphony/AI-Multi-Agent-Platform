import type {
  APIErrorBody,
  APImanifest,
  BulkTaskManagementResult,
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
  SearchPage,
  SearchRequest,
  TaskManagementChanges,
  TimelineItem,
} from "./types";
import type {
  CanonicalAgent,
  CanonicalAgentRun,
  CanonicalAgentTeam,
} from "./agents";
import type {
  CanonicalCapability,
  CanonicalCapabilityProvider,
} from "./capabilities";
import type { CanonicalReference, ReferenceCollection } from "./references";
import {
  buildTerminalStreamUrl,
  type CanonicalTerminalSession,
  type CreateTerminalSessionInput,
  type TerminalDimensions,
} from "./terminal";
import { ApiTransport } from "./transport";
import type { ApiRequestOptions, ApiTransportOptions } from "./transport";

export { ControlPlaneError, isControlPlaneError } from "./transport";
export type { AuthBoundary } from "./transport";

export interface ControlPlaneClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

export class ControlPlaneClient {
  readonly baseUrl: string;
  private readonly transport: ApiTransport;

  constructor(options: ControlPlaneClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.baseUrl = this.transport.baseUrl;
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

  search(query: SearchRequest = {}): Promise<SearchPage> {
    return this.request<SearchPage>(`/search${toSearchQuery(query)}`);
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
    return this.request<CanonicalWorkspaceIdentity>(
      `/workspaces/${encodeURIComponent(workspaceId)}`,
    );
  }

  createWorkspace(input: CreateWorkspaceInput): Promise<CanonicalWorkspaceIdentity> {
    return this.command<CanonicalWorkspaceIdentity>("/workspaces", {
      method: "POST",
      body: input,
    });
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

  updateTaskManagement(
    taskId: string,
    changes: TaskManagementChanges,
  ): Promise<CanonicalTask> {
    return this.command<CanonicalTask>("/commands/task-management.update", {
      body: { resource_ref: taskId, ...changes },
    });
  }

  bulkUpdateTaskManagement(
    updates: Array<{ task_id: string; changes: TaskManagementChanges }>,
  ): Promise<BulkTaskManagementResult> {
    return this.command<BulkTaskManagementResult>("/commands/task-management.bulk-update", {
      body: { resource_ref: "tasks", updates },
    });
  }

  moveTaskProject(
    taskId: string,
    destinationProjectId: string | null,
  ): Promise<CanonicalTask> {
    return this.command<CanonicalTask>("/commands/task.project.move", {
      body: {
        resource_ref: taskId,
        destination_project_id: destinationProjectId,
      },
    });
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

  listReferences(
    collection: ReferenceCollection,
    query: ListQuery = {},
  ): Promise<Page<CanonicalReference>> {
    return this.request<Page<CanonicalReference>>(`/${collection}${toQuery(query)}`);
  }

  getReference(
    collection: ReferenceCollection,
    resourceId: string,
  ): Promise<CanonicalReference> {
    return this.request<CanonicalReference>(
      `/${collection}/${encodeURIComponent(resourceId)}`,
    );
  }

  listAgents(query: ListQuery = {}): Promise<Page<CanonicalAgent>> {
    return this.request<Page<CanonicalAgent>>(`/agents${toQuery(query)}`);
  }

  getAgent(agentId: string): Promise<CanonicalAgent> {
    return this.request<CanonicalAgent>(`/agents/${encodeURIComponent(agentId)}`);
  }

  listAgentTeams(query: ListQuery = {}): Promise<Page<CanonicalAgentTeam>> {
    return this.request<Page<CanonicalAgentTeam>>(`/agent-teams${toQuery(query)}`);
  }

  getAgentTeam(teamId: string): Promise<CanonicalAgentTeam> {
    return this.request<CanonicalAgentTeam>(`/agent-teams/${encodeURIComponent(teamId)}`);
  }

  listAgentRuns(query: ListQuery = {}): Promise<Page<CanonicalAgentRun>> {
    return this.request<Page<CanonicalAgentRun>>(`/agent-runs${toQuery(query)}`);
  }

  getAgentRun(agentRunId: string): Promise<CanonicalAgentRun> {
    return this.request<CanonicalAgentRun>(
      `/agent-runs/${encodeURIComponent(agentRunId)}`,
    );
  }

  listCapabilities(query: ListQuery = {}): Promise<Page<CanonicalCapability>> {
    return this.request<Page<CanonicalCapability>>(`/capabilities${toQuery(query)}`);
  }

  getCapability(capabilityId: string): Promise<CanonicalCapability> {
    return this.request<CanonicalCapability>(
      `/capabilities/${encodeURIComponent(capabilityId)}`,
    );
  }

  listCapabilityProviders(
    query: ListQuery = {},
  ): Promise<Page<CanonicalCapabilityProvider>> {
    return this.request<Page<CanonicalCapabilityProvider>>(
      `/capability-providers${toQuery(query)}`,
    );
  }

  getCapabilityProvider(providerId: string): Promise<CanonicalCapabilityProvider> {
    return this.request<CanonicalCapabilityProvider>(
      `/capability-providers/${encodeURIComponent(providerId)}`,
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

  listTerminalSessions(query: ListQuery = {}): Promise<Page<CanonicalTerminalSession>> {
    return this.request<Page<CanonicalTerminalSession>>(`/terminal-sessions${toQuery(query)}`);
  }

  getTerminalSession(sessionId: string): Promise<CanonicalTerminalSession> {
    return this.request<CanonicalTerminalSession>(
      `/terminal-sessions/${encodeURIComponent(sessionId)}`,
    );
  }

  createTerminalSession(
    projectId: string,
    input: CreateTerminalSessionInput,
  ): Promise<CanonicalTerminalSession> {
    return this.command<CanonicalTerminalSession>("/commands/terminal.session.create", {
      body: { resource_ref: projectId, ...input },
    });
  }

  sendTerminalInput(
    sessionId: string,
    data: string,
    approvalId?: string,
  ): Promise<CanonicalTerminalSession> {
    return this.command<CanonicalTerminalSession>("/commands/terminal.session.input", {
      body: { resource_ref: sessionId, data, approval_id: approvalId },
    });
  }

  resizeTerminalSession(
    sessionId: string,
    dimensions: TerminalDimensions,
  ): Promise<CanonicalTerminalSession> {
    return this.command<CanonicalTerminalSession>("/commands/terminal.session.resize", {
      body: { resource_ref: sessionId, ...dimensions },
    });
  }

  terminateTerminalSession(
    sessionId: string,
    reason?: string,
    approvalId?: string,
  ): Promise<CanonicalTerminalSession> {
    return this.command<CanonicalTerminalSession>("/commands/terminal.session.terminate", {
      body: { resource_ref: sessionId, reason, approval_id: approvalId },
    });
  }

  terminalStreamUrl(
    sessionId: string,
    afterSequence = 0,
    pageOrigin?: string,
  ): string {
    return buildTerminalStreamUrl(this.baseUrl, sessionId, afterSequence, pageOrigin);
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

  private request<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
    return this.transport.request<T>(path, options);
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

function toSearchQuery(query: SearchRequest): string {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.id) params.set("id", query.id);
  if (query.types?.length) params.set("type", query.types.join(","));
  if (query.project_id) params.set("project_id", query.project_id);
  if (query.workspace_id) params.set("workspace_id", query.workspace_id);
  if (query.statuses?.length) params.set("status", query.statuses.join(","));
  if (query.tags?.length) params.set("tag", query.tags.join(","));
  if (query.sources?.length) params.set("source", query.sources.join(","));
  if (query.providers?.length) params.set("provider", query.providers.join(","));
  if (query.updated_after) params.set("updated_after", query.updated_after);
  if (query.updated_before) params.set("updated_before", query.updated_before);
  if (query.mode) params.set("mode", query.mode);
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  if (query.cursor) params.set("cursor", query.cursor);
  if (query.sort) params.set("sort", query.sort);
  if (query.direction) params.set("direction", query.direction);
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function prettyJson(value: Record<string, JsonValue> | JsonValue): string {
  return JSON.stringify(value, null, 2);
}
