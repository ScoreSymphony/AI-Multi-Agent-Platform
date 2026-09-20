import type { AuthenticatedActor } from "./types";

export interface SecretStorage {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
  deleteItem(key: string): Promise<void>;
}

export interface MobileSession {
  baseUrl: string;
}

export interface MobilePairingInput {
  baseUrl: string;
  pairingCode: string;
  pairingId?: string | null;
  deviceName: string;
  devicePlatform: "android" | "ios";
  protocolVersion?: 1;
}

interface MobilePairingResponse {
  device: {
    id: string;
    credential_id: string;
    display_name: string;
    platform: string;
    active: boolean;
  };
  credential: {
    id: string;
    secret: string;
    expires_at: string | null;
    secret_display: "one_time";
  };
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

    const actor = await validateCredential(normalizedBaseUrl, candidate, fetchImpl);
    await this.storage.setItem(TOKEN_KEY, candidate);
    await this.storage.setItem(SERVER_KEY, normalizedBaseUrl);
    return actor;
  }

  async pair(
    input: MobilePairingInput,
    fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis),
  ): Promise<AuthenticatedActor> {
    const normalizedBaseUrl = normalizeServerUrl(input.baseUrl);
    const pairingCode = input.pairingCode.trim().toUpperCase();
    if (!pairingCode) throw new Error("Pairing code is required");
    const response = await fetchImpl(`${normalizedBaseUrl}/api/v1/auth/mobile-pairings:consume`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        pairing_code: pairingCode,
        pairing_id: input.pairingId?.trim() || undefined,
        server_origin: normalizedBaseUrl,
        device_name: input.deviceName.trim(),
        device_platform: input.devicePlatform,
        protocol_version: input.protocolVersion ?? 1,
      }),
    });
    if (!response.ok) {
      throw new Error(`Pairing failed with HTTP ${response.status}`);
    }
    const result = (await response.json()) as MobilePairingResponse;
    if (
      !result.credential?.secret ||
      result.credential.secret_display !== "one_time" ||
      !result.device?.id
    ) {
      throw new Error("Pairing returned an invalid device credential");
    }

    // The pairing proof is single-use. Persist the newly issued secret immediately so a
    // transient network failure during /auth/me verification cannot strand the device with
    // a consumed pairing challenge and no recoverable credential.
    await this.storage.setItem(TOKEN_KEY, result.credential.secret);
    await this.storage.setItem(SERVER_KEY, normalizedBaseUrl);
    try {
      return await validateCredential(normalizedBaseUrl, result.credential.secret, fetchImpl);
    } catch (error) {
      if (error instanceof CredentialValidationError && error.status === 401) {
        await this.clear();
      }
      throw error;
    }
  }

  async clear(): Promise<void> {
    await Promise.all([
      this.storage.deleteItem(TOKEN_KEY),
      this.storage.deleteItem(SERVER_KEY),
    ]);
  }
}

class CredentialValidationError extends Error {
  constructor(readonly status: number) {
    super(`Credential validation failed with HTTP ${status}`);
    this.name = "CredentialValidationError";
  }
}

async function validateCredential(
  baseUrl: string,
  token: string,
  fetchImpl: typeof fetch,
): Promise<AuthenticatedActor> {
  const response = await fetchImpl(`${baseUrl}/api/v1/auth/me`, {
    method: "GET",
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
    },
  });
  if (!response.ok) {
    throw new CredentialValidationError(response.status);
  }
  const actor = (await response.json()) as AuthenticatedActor;
  if (!actor.actor_id || !actor.actor_type) {
    throw new Error("Credential validation returned an invalid actor projection");
  }
  return actor;
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
