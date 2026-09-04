import { ControlPlaneError } from "./client";
import {
  ConversationResponseClient,
  type ConversationResponseCommittedEvent,
  type ConversationResponseHandlers,
} from "./conversationResponses";
import type { APIErrorBody, CanonicalTask, JsonValue, Page } from "./types";
import type { LiveConnectionState } from "./live";

export type ConversationStatus = "open" | "archived" | "tombstoned";
export type ConversationMessageStatus = "active" | "edited" | "tombstoned";
export type ConversationMessageRole = "user" | "assistant" | "system" | "tool" | "event";
export type ConversationTargetKind = "orchestrator" | "agent" | "agent_team" | "project" | "task";
export type ConversationReferenceKind =
  | "file"
  | "artifact"
  | "task"
  | "run"
  | "result"
  | "agent"
  | "agent_team"
  | "knowledge";
export type ConversationContentKind = "text" | "markdown" | "json" | "reference";

export interface ConversationParticipant {
  kind: "user" | "service" | "agent" | "agent_team";
  id: string;
  display_name: string | null;
}

export interface AgentSelectionRef {
  kind: "agent" | "agent_team";
  id: string;
  revision: number | null;
}

export interface ModelRoutingPreference {
  model_config_id: string | null;
  routing_requirements: Record<string, JsonValue>;
}

export interface ConversationReference {
  kind: ConversationReferenceKind;
  id: string;
  label: string | null;
  metadata: Record<string, JsonValue>;
}

export interface ConversationContentBlock {
  kind: ConversationContentKind;
  text?: string;
  value?: JsonValue;
  reference?: ConversationReference;
}

export interface CanonicalConversation {
  id: string;
  type: "conversation";
  title: string;
  summary: string | null;
  owner_ref: string;
  project_id: string | null;
  workspace_id: string | null;
  participants: ConversationParticipant[];
  status: ConversationStatus;
  default_agent: AgentSelectionRef | null;
  model_preference: ModelRoutingPreference | null;
  task_ids: string[];
  run_ids: string[];
  artifact_ids: string[];
  created_at: string;
  updated_at: string;
  metadata: Record<string, JsonValue>;
}

export interface CanonicalConversationMessage {
  id: string;
  type: "conversation-message";
  conversation_id: string;
  sender_ref: string;
  role: ConversationMessageRole;
  content: ConversationContentBlock[];
  references: ConversationReference[];
  model_config_id: string | null;
  model_provider_ref: string | null;
  created_at: string;
  edited_at: string | null;
  status: ConversationMessageStatus;
  revision: number;
  correlation_id: string | null;
  causation_id: string | null;
  metadata: Record<string, JsonValue>;
}

export interface ConversationTarget {
  kind: ConversationTargetKind;
  id: string;
  revision?: number;
}

export interface CreateConversationInput {
  title: string;
  summary?: string;
  project_id?: string;
  workspace_id?: string;
  target?: ConversationTarget;
  model_preference?: {
    model_config_id?: string;
    routing_requirements?: Record<string, JsonValue>;
  };
}

export interface AddConversationMessageInput {
  content: ConversationContentBlock[];
  references?: Array<{
    kind: ConversationReferenceKind;
    id: string;
    label?: string;
    metadata?: Record<string, JsonValue>;
  }>;
  model_config_id?: string;
  model_provider_ref?: string;
}

export interface ConversationTaskHandoff {
  id: string;
  type: "conversation-task-handoff";
  conversation: CanonicalConversation;
  message: CanonicalConversationMessage;
  task: CanonicalTask;
}

export interface ConversationTaskAttachment {
  id: string;
  type: "conversation-task-attachment";
  conversation_id: string;
  message: CanonicalConversationMessage;
  task: CanonicalTask;
}

export interface ConversationTaskEvent {
  id: string;
  type: "conversation.task-event";
  conversation_id: string;
  task_id: string;
  authoritative: true;
  event: {
    id: string;
    event_type: string;
    occurred_at?: string;
    timestamp?: string;
    [key: string]: JsonValue | undefined;
  };
}

export interface ConversationClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

export interface ConversationEventStreamOptions {
  baseUrl?: string;
  conversationId: string;
  afterEventId?: string;
  onEvent: (event: ConversationTaskEvent) => void;
  onError?: (error: APIErrorBody | Error) => void;
  onState?: (state: LiveConnectionState) => void;
}

export class ConversationClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ConversationClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  list(includeArchived = true): Promise<Page<CanonicalConversation>> {
    const params = new URLSearchParams({ limit: "100", sort: "updated_at", direction: "desc" });
    if (includeArchived) params.set("filter[include_archived]", "true");
    return this.request<Page<CanonicalConversation>>(`/conversations?${params.toString()}`);
  }

  get(conversationId: string): Promise<CanonicalConversation> {
    return this.request<CanonicalConversation>(
      `/conversations/${encodeURIComponent(conversationId)}`,
    );
  }

  create(input: CreateConversationInput): Promise<CanonicalConversation> {
    return this.command<CanonicalConversation>("/conversations", { method: "POST", body: input });
  }

  listMessages(conversationId: string): Promise<Page<CanonicalConversationMessage>> {
    const params = new URLSearchParams({ limit: "200", sort: "created_at", direction: "asc" });
    return this.request<Page<CanonicalConversationMessage>>(
      `/conversations/${encodeURIComponent(conversationId)}/messages?${params.toString()}`,
    );
  }

  addMessage(
    conversationId: string,
    input: AddConversationMessageInput,
  ): Promise<CanonicalConversationMessage> {
    return this.command<CanonicalConversationMessage>(
      `/conversations/${encodeURIComponent(conversationId)}/messages`,
      { method: "POST", body: input },
    );
  }

  streamResponse(
    messageId: string,
    handlers: ConversationResponseHandlers = {},
    idempotencyKey = crypto.randomUUID(),
  ): Promise<ConversationResponseCommittedEvent> {
    const responseClient = new ConversationResponseClient({
      baseUrl: this.baseUrl,
      fetchImpl: this.fetchImpl,
    });
    return responseClient.stream(messageId, handlers, idempotencyKey);
  }

  archive(conversationId: string): Promise<CanonicalConversation> {
    return this.command<CanonicalConversation>(
      `/conversations/${encodeURIComponent(conversationId)}:archive`,
    );
  }

  reopen(conversationId: string): Promise<CanonicalConversation> {
    return this.command<CanonicalConversation>(
      `/conversations/${encodeURIComponent(conversationId)}:reopen`,
    );
  }

  delete(conversationId: string): Promise<CanonicalConversation> {
    return this.command<CanonicalConversation>(
      `/conversations/${encodeURIComponent(conversationId)}`,
      { method: "DELETE" },
    );
  }

  export(conversationId: string): Promise<Record<string, JsonValue>> {
    return this.request<Record<string, JsonValue>>(
      `/conversations/${encodeURIComponent(conversationId)}/export`,
    );
  }

  createTask(
    messageId: string,
    input: { title: string; objective: string },
  ): Promise<ConversationTaskHandoff> {
    return this.command<ConversationTaskHandoff>(
      `/conversation-messages/${encodeURIComponent(messageId)}:create-task`,
      { body: input },
    );
  }

  attachTask(messageId: string, taskId: string): Promise<ConversationTaskAttachment> {
    return this.command<ConversationTaskAttachment>(
      `/conversation-messages/${encodeURIComponent(messageId)}:attach-task`,
      { body: { task_id: taskId } },
    );
  }

  resumeTask(messageId: string, taskId: string): Promise<CanonicalTask> {
    return this.command<CanonicalTask>(
      `/conversation-messages/${encodeURIComponent(messageId)}:resume-task`,
      { body: { task_id: taskId } },
    );
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
    options: { method?: string; body?: unknown; idempotencyKey?: string } = {},
  ): Promise<T> {
    const headers = new Headers({
      Accept: "application/json",
      "X-Correlation-ID": crypto.randomUUID(),
    });
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);

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

export class ConversationEventStream {
  private source: EventSource | null = null;
  private readonly options: ConversationEventStreamOptions;

  constructor(options: ConversationEventStreamOptions) {
    this.options = options;
  }

  open(): void {
    this.close();
    this.options.onState?.("connecting");
    const url = buildConversationStreamUrl(
      this.options.baseUrl,
      this.options.conversationId,
      this.options.afterEventId,
    );
    const source = new EventSource(url, { withCredentials: true });
    this.source = source;
    source.onopen = () => this.options.onState?.("open");
    source.addEventListener("conversation.task-event", (message) => {
      try {
        const event = JSON.parse((message as MessageEvent<string>).data) as ConversationTaskEvent;
        this.options.onEvent(event);
      } catch {
        this.options.onError?.(new Error("Malformed Conversation task-event payload"));
      }
    });
    source.addEventListener("platform.error", (message) => {
      try {
        const error = JSON.parse((message as MessageEvent<string>).data) as APIErrorBody;
        this.options.onError?.(error);
      } catch {
        this.options.onError?.(new Error("Malformed Conversation stream error"));
      }
    });
    source.onerror = () => this.options.onState?.("reconnecting");
  }

  close(): void {
    if (this.source) {
      this.source.close();
      this.source = null;
      this.options.onState?.("closed");
    }
  }
}

export function buildConversationStreamUrl(
  baseUrl: string | undefined,
  conversationId: string,
  afterEventId?: string,
  pageOrigin = typeof window === "undefined" ? "http://localhost" : window.location.origin,
): URL {
  const base = (baseUrl ?? "").replace(/\/$/, "");
  const url = new URL(
    `${base}/api/v1/conversations/${encodeURIComponent(conversationId)}/events/stream`,
    pageOrigin,
  );
  if (afterEventId) url.searchParams.set("after_event_id", afterEventId);
  return url;
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