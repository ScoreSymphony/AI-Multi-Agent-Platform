import type { AuthenticatedActor, JsonValue } from "./types";

export interface SecretStorage {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
  deleteItem(key: string): Promise<void>;
}

export interface MobileSession {
  baseUrl: string;
}

export interface MobilePairingDescriptor {
  baseUrl: string;
  pairingId: string | null;
  code: string;
  protocolVersion: "1";
}

interface PairingResponse {
  device: {
    id: string;
    display_name: string;
    server_origin: string;
    credential_id: string;
    active: boolean;
    [key: string]: JsonValue | undefined;
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
    descriptor: MobilePairingDescriptor,
    deviceName: string,
    platform: string,
    fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis),
  ): Promise<AuthenticatedActor> {
    const baseUrl = normalizeServerUrl(descriptor.baseUrl);
    const normalizedName = deviceName.trim();
    if (!normalizedName) throw new Error("Device name is required");
    const code = normalizePairingCode(descriptor.code);
    if (!code) throw new Error("Pairing code is required");

    const response = await fetchImpl(`${baseUrl}/api/v1/auth/mobile-pairings:consume`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-Correlation-ID": requestId(),
      },
      body: JSON.stringify({
        pairing_id: descriptor.pairingId,
        code,
        device_name: normalizedName,
        platform: platform.trim() || "unknown",
        protocol_version: descriptor.protocolVersion,
      }),
    });
    const payload = (await response.json()) as PairingResponse | { message?: string; code?: string };
    if (!response.ok) {
      const message =
        "message" in payload && typeof payload.message === "string"
          ? payload.message
          : `Pairing failed with HTTP ${response.status}`;
      throw new Error(message);
    }
    if (
      !("credential" in payload) ||
      typeof payload.credential?.secret !== "string" ||
      !payload.credential.secret
    ) {
      throw new Error("Pairing response did not contain a one-time device credential");
    }

    // Persist immediately after the one-time consume succeeds. If identity verification is
    // temporarily unreachable, restart recovery can retry /auth/me without losing the secret.
    await this.storage.setItem(TOKEN_KEY, payload.credential.secret);
    await this.storage.setItem(SERVER_KEY, baseUrl);
    try {
      return await validateCredential(baseUrl, payload.credential.secret, fetchImpl);
    } catch (error) {
      if (error instanceof CredentialRejectedError) {
        await this.clear();
      }
      throw error;
    }
  }

  async pairWithCode(
    baseUrl: string,
    code: string,
    deviceName: string,
    platform: string,
    fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis),
  ): Promise<AuthenticatedActor> {
    return this.pair(
      {
        baseUrl: normalizeServerUrl(baseUrl),
        pairingId: null,
        code: normalizePairingCode(code),
        protocolVersion: "1",
      },
      deviceName,
      platform,
      fetchImpl,
    );
  }

  async clear(): Promise<void> {
    await Promise.all([
      this.storage.deleteItem(TOKEN_KEY),
      this.storage.deleteItem(SERVER_KEY),
    ]);
  }
}

class CredentialRejectedError extends Error {}

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
    if (response.status === 401) {
      throw new CredentialRejectedError("Credential validation failed with HTTP 401");
    }
    throw new Error(`Credential validation failed with HTTP ${response.status}`);
  }
  const actor = (await response.json()) as AuthenticatedActor;
  if (!actor.actor_id || !actor.actor_type) {
    throw new Error("Credential validation returned an invalid actor projection");
  }
  return actor;
}

export function parseMobilePairingUri(value: string): MobilePairingDescriptor {
  let parsed: URL;
  try {
    parsed = new URL(value.trim());
  } catch {
    throw new Error("Pairing QR payload is not a valid URI");
  }
  if (parsed.protocol !== "amp-mobile:" || parsed.hostname !== "pair") {
    throw new Error("Pairing QR payload uses an unsupported protocol");
  }
  const version = parsed.searchParams.get("v");
  if (version !== "1") throw new Error("Unsupported pairing protocol version");
  const server = parsed.searchParams.get("server");
  const pairingId = parsed.searchParams.get("id");
  const code = parsed.searchParams.get("code");
  if (!server || !pairingId || !code) {
    throw new Error("Pairing QR payload is missing required fields");
  }
  return {
    baseUrl: normalizeServerUrl(server),
    pairingId: requirePairingIdentifier(pairingId),
    code: normalizePairingCode(code),
    protocolVersion: "1",
  };
}

export function normalizePairingCode(value: string): string {
  return value.replace(/[-\s]/g, "").toUpperCase().trim();
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

function requirePairingIdentifier(value: string): string {
  const normalized = value.trim();
  if (!normalized || normalized.includes("/") || normalized.includes("\\")) {
    throw new Error("Pairing request identifier is invalid");
  }
  return normalized;
}

function requestId(): string {
  return globalThis.crypto?.randomUUID?.() ??
    `mobile-pair-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
