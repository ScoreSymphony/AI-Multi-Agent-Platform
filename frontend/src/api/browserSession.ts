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
    this.fetchImpl = options.fetchImpl ?? fetch;
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
