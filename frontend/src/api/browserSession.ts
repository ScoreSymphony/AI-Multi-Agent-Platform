import { ControlPlaneError } from "./client";
import type { APIErrorBody, JsonValue } from "./types";

export interface AuthenticatedActor {
  actor_id: string;
  actor_type: string;
  authentication_method: string;
  credential_id: string | null;
  authenticated_at: string;
  expires_at: string | null;
  organization_id: string | null;
  project_id: string | null;
  request_id: string;
  correlation_id: string;
  provider_metadata: Record<string, JsonValue>;
}

export interface BrowserSessionSummary {
  id: string;
  user_id: string;
  created_at: string;
  authenticated_at: string;
  expires_at: string;
  revoked_at: string | null;
  last_seen_at: string | null;
  active: boolean;
}

export interface ReleaseVersionSnapshot {
  platform_release: string;
  domain_schema: string;
  api: string;
  migration_revision: string;
  plugin_manifest: string;
  portable_format: string;
  template_schema: string;
  backup_format: string;
  worker_protocol: string;
  message_protocol: string;
  adapter_versions: Record<string, string>;
  plugin_interface_versions: Record<string, string>;
}

export interface ReleaseCompatibilityEntry {
  component: string;
  source_url: string;
  revision: string;
  status: string;
  integration_mode: string;
  boundary: string;
  license: string;
  last_checked_at: string;
  latest_known_revision: string;
  update_risk: string;
  local_modifications: boolean;
  patches: string[];
  notes: string[];
}

export interface ReleaseDependencySet {
  name: string;
  ecosystem: string;
  kind: string;
  source_ref: string;
  digest: string;
}

export interface ReleaseEvidence {
  kind: string;
  ref: string;
  source_commit: string | null;
  digest: string | null;
}

export interface ReleaseGate {
  name: string;
  status: string;
  evidence: ReleaseEvidence;
  required: boolean;
}

export interface ReleaseManifestUpstream {
  component: string;
  revision: string;
  source_url: string;
  license: string;
  modified: boolean;
  last_verified_at: string;
}

export interface ReleaseManifestCompatibility {
  component: string;
  upstream_revision: string;
  status: string;
  tested_at: string;
  platform_constraint: string;
  notes: string[];
}

export interface ReleaseManifestStatus {
  release_version: string;
  release_kind: string;
  source_commit: string;
  created_at: string;
  release_notes_ref: string;
  versions: ReleaseVersionSnapshot;
  dependency_sets: ReleaseDependencySet[];
  upstreams: ReleaseManifestUpstream[];
  compatibility: ReleaseManifestCompatibility[];
  gates: ReleaseGate[];
  sbom_ref: string;
  provenance_ref: string;
  artifact_hashes: Record<string, string>;
  release_ready: boolean;
  release_blockers: string[];
  release_warnings: string[];
}

export interface ReleaseUpdateCandidate {
  component: string;
  source_url: string;
  current_revision: string;
  candidate_revision: string | null;
  disposition: string;
  classifications: string[];
  manual_review_required: boolean;
  reasons: string[];
  release_ref: string | null;
  published_at: string | null;
  validation: Record<string, string> | null;
}

export interface ReleaseOperatorStatus {
  platform_release: string;
  versions: ReleaseVersionSnapshot;
  release_manifest: ReleaseManifestStatus | null;
  compatibility_inventory: {
    schema_version: string;
    platform_release: string;
    versions: ReleaseVersionSnapshot;
    generated_from: string;
    last_reviewed_at: string;
    components: ReleaseCompatibilityEntry[];
  };
  update_discovery: {
    mode: string;
    observed_at: string | null;
    update_available: boolean;
    candidates: ReleaseUpdateCandidate[];
  };
  update_discovery_reviewed_at: string | null;
  release_ready: boolean | null;
  operator_warnings: string[];
  automatic_production_updates: boolean;
  production_pin_mutation: string;
}

export interface LoginResult {
  actor: AuthenticatedActor;
  csrf_token: string;
  expires_at: string;
}

export interface SessionRenewal {
  csrf_token: string;
  expires_at: string;
}

interface BrowserStorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface BrowserSessionClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  storage?: BrowserStorageLike | null;
}

const CSRF_STORAGE_KEY = "ai-agent-platform.csrf-token";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export class BrowserSessionClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly storage: BrowserStorageLike | null;
  private csrfToken: string | null;

  constructor(options: BrowserSessionClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
    this.storage = options.storage === undefined ? browserCsrfStorage() : options.storage;
    this.csrfToken = this.storage?.getItem(CSRF_STORAGE_KEY) ?? null;
  }

  readonly fetch: typeof fetch = async (input, init = {}) => {
    const headers = new Headers(init.headers);
    const method = (init.method ?? "GET").toUpperCase();
    const csrfToken = this.currentCsrfToken();
    if (
      !SAFE_METHODS.has(method)
      && csrfToken
      && !headers.has("Authorization")
      && !headers.has("X-CSRF-Token")
    ) {
      headers.set("X-CSRF-Token", csrfToken);
    }
    return this.fetchImpl(input, { ...init, headers });
  };

  login(username: string, password: string): Promise<LoginResult> {
    return this.request<LoginResult>("/auth/login", {
      method: "POST",
      body: { username, password },
    }).then((result) => {
      this.setCsrfToken(result.csrf_token);
      return result;
    });
  }

  me(): Promise<AuthenticatedActor> {
    return this.request<AuthenticatedActor>("/auth/me");
  }

  listSessions(): Promise<BrowserSessionSummary[]> {
    return this.request<{ items: BrowserSessionSummary[] }>("/auth/sessions").then(
      (result) => result.items,
    );
  }

  releaseStatus(): Promise<ReleaseOperatorStatus> {
    return this.request<ReleaseOperatorStatus>("/release/status");
  }

  revokeSession(sessionId: string): Promise<{ id: string; revoked: boolean }> {
    return this.request<{ id: string; revoked: boolean }>(
      `/auth/sessions/${encodeURIComponent(sessionId)}:revoke`,
      { method: "POST" },
    );
  }

  renew(): Promise<SessionRenewal> {
    return this.request<SessionRenewal>("/auth/session:renew", { method: "POST" }).then(
      (result) => {
        this.setCsrfToken(result.csrf_token);
        return result;
      },
    );
  }

  async logout(): Promise<void> {
    await this.request<{ logged_out: boolean }>("/auth/logout", { method: "POST" });
    this.clearLocalSession();
  }

  clearLocalSession(): void {
    this.csrfToken = null;
    this.storage?.removeItem(CSRF_STORAGE_KEY);
  }

  hasCsrfToken(): boolean {
    return this.currentCsrfToken() !== null;
  }

  private currentCsrfToken(): string | null {
    if (this.storage !== null) {
      this.csrfToken = this.storage.getItem(CSRF_STORAGE_KEY);
    }
    return this.csrfToken;
  }

  private setCsrfToken(token: string): void {
    this.csrfToken = token;
    this.storage?.setItem(CSRF_STORAGE_KEY, token);
  }

  private async request<T>(
    path: string,
    options: { method?: string; body?: unknown } = {},
  ): Promise<T> {
    const headers = new Headers({
      Accept: "application/json",
      "X-Correlation-ID": crypto.randomUUID(),
    });
    if (options.body !== undefined) headers.set("Content-Type", "application/json");

    const response = await this.fetch(`${this.baseUrl}/api/v1${path}`, {
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

function browserCsrfStorage(): BrowserStorageLike | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
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
