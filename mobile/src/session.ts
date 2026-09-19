import type { AuthenticatedActor } from "./types";

export interface SecretStorage {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
  deleteItem(key: string): Promise<void>;
}

export interface MobileSession {
  baseUrl: string;
}

const TOKEN_KEY = "ai-agent-platform.mobile.bearer-token";
const SERVER_KEY = "ai-agent-platform.mobile.server-url";

export class MobileSessionStore {
  constructor(private readonly storage: SecretStorage) {}

  async current(): Promise<MobileSession | null> {
    const [baseUrl, token] = await Promise.all([
      this.storage.getItem(SERVER_KEY),
      this.storage.getItem(TOKEN_KEY),
    ]);
    if (!baseUrl || !token) return null;
    return { baseUrl: normalizeServerUrl(baseUrl) };
  }

  getToken(): Promise<string | null> {
    return this.storage.getItem(TOKEN_KEY);
  }

  async activate(
    baseUrl: string,
    token: string,
    fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis),
  ): Promise<AuthenticatedActor> {
    const normalizedBaseUrl = normalizeServerUrl(baseUrl);
    const candidate = token.trim();
    if (!candidate) throw new Error("Bearer credential is required");

    const response = await fetchImpl(`${normalizedBaseUrl}/api/v1/auth/me`, {
      method: "GET",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${candidate}`,
      },
    });
    if (!response.ok) {
      throw new Error(`Credential validation failed with HTTP ${response.status}`);
    }
    const actor = (await response.json()) as AuthenticatedActor;
    if (!actor.actor_id || !actor.actor_type) {
      throw new Error("Credential validation returned an invalid actor projection");
    }

    await this.storage.setItem(TOKEN_KEY, candidate);
    await this.storage.setItem(SERVER_KEY, normalizedBaseUrl);
    return actor;
  }

  async clear(): Promise<void> {
    await Promise.all([
      this.storage.deleteItem(TOKEN_KEY),
      this.storage.deleteItem(SERVER_KEY),
    ]);
  }
}

export function normalizeServerUrl(value: string): string {
  const trimmed = value.trim().replace(/\/$/, "");
  if (!trimmed) throw new Error("Control Plane URL is required");

  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new Error("Control Plane URL must be an absolute URL");
  }

  const local =
    parsed.hostname === "localhost" ||
    parsed.hostname === "127.0.0.1" ||
    parsed.hostname === "::1";
  if (parsed.protocol !== "https:" && !(local && parsed.protocol === "http:")) {
    throw new Error("Remote Control Plane connections require HTTPS");
  }
  if (parsed.username || parsed.password) {
    throw new Error("Credentials must not be embedded in the Control Plane URL");
  }
  if (parsed.pathname !== "/" || parsed.search || parsed.hash) {
    throw new Error("Control Plane URL must contain only scheme, host and optional port");
  }
  return parsed.origin;
}
