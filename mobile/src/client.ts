import type {
  AuthenticatedActor,
  CanonicalAgent,
  CanonicalApproval,
  CanonicalNotification,
  CanonicalReference,
  CanonicalRun,
  CanonicalTask,
  CanonicalVerification,
  CanonicalWorker,
  HealthStatus,
  Page,
  SearchPage,
} from "./types";
import { normalizeServerUrl } from "./session";

export interface CredentialSource {
  getToken(): Promise<string | null>;
}

export interface MobileControlPlaneClientOptions {
  baseUrl: string;
  credentialSource: CredentialSource;
  fetchImpl?: typeof fetch;
  onUnauthorized?: () => void | Promise<void>;
}

export interface ReadResult<T> {
  data: T;
  stale: boolean;
  fetchedAt: string;
}

export class MobileControlPlaneError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "MobileControlPlaneError";
  }
}

export class OfflineMutationError extends Error {
  constructor() {
    super("Mutations are disabled while the Control Plane is offline or unreachable");
    this.name = "OfflineMutationError";
  }
}

export class MobileNetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MobileNetworkError";
  }
}

interface CacheEntry {
  data: unknown;
  fetchedAt: string;
}

export class MobileControlPlaneClient {
  readonly baseUrl: string;
  private readonly credentialSource: CredentialSource;
  private readonly fetchImpl: typeof fetch;
  private readonly onUnauthorized?: () => void | Promise<void>;
  private readonly cache = new Map<string, CacheEntry>();
  private offline = false;

  constructor(options: MobileControlPlaneClientOptions) {
    this.baseUrl = normalizeServerUrl(options.baseUrl);
    this.credentialSource = options.credentialSource;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
    this.onUnauthorized = options.onUnauthorized;
  }

  isOffline(): boolean {
    return this.offline;
  }

  async me(): Promise<AuthenticatedActor> {
    return this.requestJson<AuthenticatedActor>("/auth/me", { method: "GET" });
  }

  health(): Promise<ReadResult<HealthStatus>> {
    return this.read("/health");
  }

  listTasks(): Promise<ReadResult<Page<CanonicalTask>>> {
    return this.read("/tasks?limit=50&sort=updated_at&direction=desc");
  }

  listRuns(): Promise<ReadResult<Page<CanonicalRun>>> {
    return this.read("/runs?limit=50&sort=updated_at&direction=desc");
  }

  listResults(): Promise<ReadResult<Page<CanonicalReference>>> {
    return this.read("/results?limit=50");
  }

  listArtifacts(): Promise<ReadResult<Page<CanonicalReference>>> {
    return this.read("/artifacts?limit=50");
  }

  listApprovals(): Promise<ReadResult<Page<CanonicalApproval>>> {
    return this.read("/approvals?limit=50&sort=created_at&direction=desc");
  }

  listPendingVerification(): Promise<ReadResult<Page<CanonicalVerification>>> {
    return this.read("/verification-reviews?limit=50");
  }

  listNotifications(): Promise<ReadResult<Page<CanonicalNotification>>> {
    return this.read("/notifications?limit=50&sort=created_at&direction=desc");
  }

  listAgents(): Promise<ReadResult<Page<CanonicalAgent>>> {
    return this.read("/agents?limit=50");
  }

  listWorkers(): Promise<ReadResult<Page<CanonicalWorker>>> {
    return this.read("/workers?limit=50");
  }

  search(query: string): Promise<ReadResult<SearchPage>> {
    const q = query.trim();
    if (!q) throw new Error("Search query is required");
    return this.read(`/search?q=${encodeURIComponent(q)}&limit=50`);
  }

  createTask(
    actor: AuthenticatedActor,
    title: string,
    objective: string,
  ): Promise<CanonicalTask> {
    return this.mutate("/tasks", {
      title: requireText(title, "Task title"),
      objective: requireText(objective, "Task objective"),
      owner_type: ownerTypeForActor(actor.actor_type),
      owner_id: actor.actor_id,
    });
  }

  approve(approval: CanonicalApproval, comment?: string): Promise<CanonicalApproval> {
    return this.approvalDecision("approval.approve", approval, comment);
  }

  deny(approval: CanonicalApproval, comment?: string): Promise<CanonicalApproval> {
    return this.approvalDecision("approval.deny", approval, comment);
  }

  verificationReview(
    verificationId: string,
    action: "verification.accept" | "verification.reject" | "verification.request-changes",
    comment?: string,
  ): Promise<CanonicalVerification> {
    const body: Record<string, string> = { resource_ref: requireText(verificationId, "Verification ID") };
    if (comment?.trim()) body.comment = comment.trim();
    return this.mutate(`/commands/${action}`, body);
  }

  markNotificationRead(notificationId: string): Promise<CanonicalNotification> {
    return this.mutate("/commands/notification.mark-read", {
      resource_ref: requireText(notificationId, "Notification ID"),
    });
  }

  private approvalDecision(
    command: "approval.approve" | "approval.deny",
    approval: CanonicalApproval,
    comment?: string,
  ): Promise<CanonicalApproval> {
    const body: Record<string, string> = {
      resource_ref: requireText(approval.id, "Approval ID"),
      requested_action_digest: requireText(
        approval.requested_action_digest,
        "Requested action digest",
      ),
    };
    if (comment?.trim()) body.comment = comment.trim();
    return this.mutate(`/commands/${command}`, body);
  }

  private async read<T>(path: string): Promise<ReadResult<T>> {
    try {
      const data = await this.requestJson<T>(path, { method: "GET" });
      const fetchedAt = new Date().toISOString();
      this.cache.set(path, { data, fetchedAt });
      this.offline = false;
      return { data, stale: false, fetchedAt };
    } catch (error) {
      if (!(error instanceof MobileNetworkError)) throw error;
      this.offline = true;
      const cached = this.cache.get(path);
      if (!cached) throw error;
      return {
        data: cached.data as T,
        stale: true,
        fetchedAt: cached.fetchedAt,
      };
    }
  }

  private async mutate<T>(path: string, body: unknown): Promise<T> {
    if (this.offline) throw new OfflineMutationError();
    return this.requestJson<T>(path, {
      method: "POST",
      body,
      idempotencyKey: requestId(),
    });
  }

  private async requestJson<T>(
    path: string,
    options: { method: "GET" | "POST"; body?: unknown; idempotencyKey?: string },
  ): Promise<T> {
    const token = await this.credentialSource.getToken();
    if (!token) {
      await this.onUnauthorized?.();
      throw new MobileControlPlaneError(401, "missing_credential", "Mobile credential is unavailable");
    }

    const headers = new Headers({
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
      "X-Correlation-ID": requestId(),
    });
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);

    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}/api/v1${path}`, {
        method: options.method,
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
      });
    } catch (error) {
      this.offline = true;
      throw new MobileNetworkError(
        error instanceof Error ? error.message : "Control Plane network request failed",
      );
    }

    this.offline = false;
    const text = await response.text();
    const payload = text ? safeJson(text) : null;
    if (!response.ok) {
      const body = isRecord(payload) ? payload : {};
      const code = typeof body.code === "string" ? body.code : "request_failed";
      const message =
        typeof body.message === "string"
          ? body.message
          : `Control Plane request failed with HTTP ${response.status}`;
      if (response.status === 401) await this.onUnauthorized?.();
      throw new MobileControlPlaneError(response.status, code, message);
    }
    this.offline = false;
    return payload as T;
  }
}

function requestId(): string {
  const random = globalThis.crypto?.randomUUID?.();
  if (random) return random;
  return `mobile-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new Error("Control Plane returned invalid JSON");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function ownerTypeForActor(actorType: string): "user" | "service" {
  if (actorType === "human") return "user";
  if (actorType === "service") return "service";
  throw new Error(`Lightweight Task submission is unsupported for actor type ${actorType}`);
}

function requireText(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${label} is required`);
  return normalized;
}
