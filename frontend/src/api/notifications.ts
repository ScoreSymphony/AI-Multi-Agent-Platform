import { ControlPlaneError } from "./client";
import { ControlPlaneCollectionClient } from "./collections";
import type { APIErrorBody, JsonValue, ListQuery, Page } from "./types";

export type NotificationCategory =
  | "task"
  | "approval"
  | "verification"
  | "agent_input"
  | "deadline"
  | "assignment"
  | "dependency"
  | "worker"
  | "automation"
  | "security"
  | "resource"
  | "connector"
  | "membership"
  | "general";

export type NotificationSeverity = "info" | "warning" | "error" | "critical";
export type NotificationState = "unread" | "read" | "acknowledged" | "dismissed" | "archived";
export type NotificationDeliveryStatus =
  | "delivered"
  | "retryable_failure"
  | "permanent_failure"
  | "unavailable";

export interface CanonicalNotificationRecipient {
  type: "user" | "team" | "organization";
  id: string;
}

export interface CanonicalNotificationSource {
  resource_type: string;
  resource_id: string;
}

export interface CanonicalNotificationAction {
  action_id: string;
  label: string;
  command: string | null;
  resource_type: string | null;
  resource_id: string | null;
  href: string | null;
}

export interface CanonicalNotificationDeliveryAttempt {
  id: string;
  type: "notification-delivery-attempt";
  notification_id: string;
  recipient: CanonicalNotificationRecipient;
  channel: string;
  status: NotificationDeliveryStatus;
  attempt: number;
  attempted_at: string;
  provider_reference: string | null;
  retry_after_seconds: number | null;
  metadata: Record<string, JsonValue>;
}

export interface CanonicalNotification {
  id: string;
  type: "notification";
  category: NotificationCategory;
  severity: NotificationSeverity;
  title: string;
  summary: Record<string, JsonValue>;
  state: NotificationState;
  recipient: CanonicalNotificationRecipient;
  source: CanonicalNotificationSource;
  project_id: string | null;
  workspace_id: string | null;
  task_id: string | null;
  run_id: string | null;
  approval_id: string | null;
  verification_id: string | null;
  node_id: string | null;
  automation_id: string | null;
  membership_id: string | null;
  resource_ref: CanonicalNotificationSource | null;
  actions: CanonicalNotificationAction[];
  aggregation_key: string | null;
  occurrence_count: number;
  created_at: string;
  updated_at: string;
  read_at: string | null;
  acknowledged_at: string | null;
  dismissed_at: string | null;
  archived_at: string | null;
  expires_at: string | null;
  correlation_id: string | null;
  causation_id: string | null;
  delivery: {
    metadata: Record<string, JsonValue>;
    attempts: CanonicalNotificationDeliveryAttempt[];
  };
}

export interface CanonicalNotificationPreference {
  id: string;
  type: "notification-preference";
  recipient: CanonicalNotificationRecipient;
  enabled_categories: NotificationCategory[];
  minimum_severity: NotificationSeverity;
  project_ids: string[];
  muted: boolean;
  in_app_enabled: boolean;
  external_channels: string[];
  aggregate_duplicates: boolean;
  unread_count: number;
}

export interface NotificationPreferenceUpdate {
  enabled_categories?: NotificationCategory[];
  minimum_severity?: NotificationSeverity;
  project_ids?: string[];
  muted?: boolean;
  in_app_enabled?: boolean;
  external_channels?: string[];
  aggregate_duplicates?: boolean;
}

export interface NotificationClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

const NOTIFICATION_COLLECTION = "notifications";
const PREFERENCE_COLLECTION = "notification-preferences";

export class NotificationClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: NotificationClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.collections = new ControlPlaneCollectionClient(options);
  }

  list(query: ListQuery = {}): Promise<Page<CanonicalNotification>> {
    return this.collections.list<CanonicalNotification>(NOTIFICATION_COLLECTION, query);
  }

  get(notificationId: string): Promise<CanonicalNotification> {
    return this.collections.get<CanonicalNotification>(NOTIFICATION_COLLECTION, notificationId);
  }

  async preference(): Promise<CanonicalNotificationPreference> {
    const page = await this.collections.list<CanonicalNotificationPreference>(PREFERENCE_COLLECTION, {
      limit: 1,
    });
    const preference = page.items[0];
    if (!preference) throw new Error("Canonical notification preference is unavailable");
    return preference;
  }

  markRead(notificationId: string): Promise<CanonicalNotification> {
    return this.command<CanonicalNotification>("notification.mark-read", notificationId);
  }

  markAllRead(): Promise<{ id: string; updated_count: number; unread_count: number }> {
    return this.command("notification.mark-all-read", NOTIFICATION_COLLECTION);
  }

  acknowledge(notificationId: string): Promise<CanonicalNotification> {
    return this.command<CanonicalNotification>("notification.acknowledge", notificationId);
  }

  dismiss(notificationId: string): Promise<CanonicalNotification> {
    return this.command<CanonicalNotification>("notification.dismiss", notificationId);
  }

  archive(notificationId: string): Promise<CanonicalNotification> {
    return this.command<CanonicalNotification>("notification.archive", notificationId);
  }

  updatePreference(
    recipientId: string,
    update: NotificationPreferenceUpdate,
  ): Promise<CanonicalNotificationPreference> {
    return this.command<CanonicalNotificationPreference>(
      "notification.preference.update",
      recipientId,
      update,
    );
  }

  retryDelivery(
    notificationId: string,
    channelId: string,
  ): Promise<CanonicalNotificationDeliveryAttempt> {
    return this.command<CanonicalNotificationDeliveryAttempt>(
      "notification.delivery.retry",
      notificationId,
      { channel_id: channelId },
    );
  }

  private async command<T>(
    command: string,
    resourceRef: string,
    payload: object = {},
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
