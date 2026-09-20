import { MobileSessionStore, normalizeServerUrl } from "./session";
import type { AuthenticatedActor } from "./types";

const PAIRING_SCHEME = "aiagentplatform:";
const PAIRING_HOST = "pair";
const PAIRING_VERSION = "1";
const PAIRING_REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;

export interface MobilePairingRequest {
  protocolVersion: "1";
  serverOrigin: string;
  requestId: string;
  proof: string;
}

export interface MobilePairingDeviceMetadata {
  platform?: string;
  device_model?: string;
  os_name?: string;
  os_version?: string;
  app_version?: string;
}

export interface MobilePairingResult {
  actor: AuthenticatedActor;
  credentialId: string;
}

export class MobilePairingError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "MobilePairingError";
  }
}

export function parseMobilePairingDeepLink(value: string): MobilePairingRequest | null {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== PAIRING_SCHEME || url.hostname !== PAIRING_HOST) return null;
  if (url.username || url.password || (url.pathname !== "" && url.pathname !== "/") || url.hash) {
    return null;
  }

  const allowed = new Set(["v", "origin", "request_id", "secret"]);
  for (const key of url.searchParams.keys()) {
    if (!allowed.has(key)) return null;
  }
  const version = singleParameter(url, "v");
  const origin = singleParameter(url, "origin");
  const requestId = singleParameter(url, "request_id");
  const secret = singleParameter(url, "secret");
  if (version !== PAIRING_VERSION || !origin || !requestId || !secret) return null;
  if (!PAIRING_REQUEST_ID.test(requestId) || secret.length > 512) return null;

  try {
    return {
      protocolVersion: PAIRING_VERSION,
      serverOrigin: normalizeServerUrl(origin),
      requestId,
      proof: secret,
    };
  } catch {
    return null;
  }
}

export function pairingRequestFromFallbackCode(
  serverUrl: string,
  requestId: string,
  code: string,
): MobilePairingRequest {
  const normalizedRequestId = requestId.trim();
  const normalizedCode = code.trim();
  if (!PAIRING_REQUEST_ID.test(normalizedRequestId)) {
    throw new MobilePairingError("Pairing request ID is invalid");
  }
  if (!normalizedCode || normalizedCode.length > 64) {
    throw new MobilePairingError("Pairing code is invalid");
  }
  return {
    protocolVersion: PAIRING_VERSION,
    serverOrigin: normalizeServerUrl(serverUrl),
    requestId: normalizedRequestId,
    proof: normalizedCode,
  };
}

export async function completeMobilePairing(
  sessionStore: MobileSessionStore,
  request: MobilePairingRequest,
  deviceName: string,
  deviceMetadata: MobilePairingDeviceMetadata = {},
  fetchImpl: typeof fetch = fetch,
): Promise<MobilePairingResult> {
  const normalizedName = deviceName.trim();
  if (!normalizedName || normalizedName.length > 120) {
    throw new MobilePairingError("Device name must be between 1 and 120 characters");
  }
  const response = await fetchImpl(
    `${request.serverOrigin}/api/v1/auth/mobile-pairing/${encodeURIComponent(request.requestId)}:complete`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        proof: request.proof,
        device_name: normalizedName,
        device_metadata: deviceMetadata,
      }),
    },
  );
  const payload = await jsonObject(response);
  if (!response.ok) {
    const error = asRecord(payload.error);
    const code = stringValue(payload.code) ?? stringValue(error?.code);
    const message =
      stringValue(payload.message) ??
      stringValue(error?.message) ??
      `Pairing failed with HTTP ${response.status}`;
    throw new MobilePairingError(message, response.status, code);
  }

  const secret = stringValue(payload.secret);
  const credentialId = stringValue(payload.credential_id);
  if (!secret || !credentialId) {
    throw new MobilePairingError("Pairing response did not contain a device credential");
  }
  const actor = await sessionStore.activate(request.serverOrigin, secret, fetchImpl);
  return { actor, credentialId };
}

function singleParameter(url: URL, name: string): string | null {
  const values = url.searchParams.getAll(name);
  return values.length === 1 ? values[0] : null;
}

async function jsonObject(response: Response): Promise<Record<string, unknown>> {
  try {
    const value: unknown = await response.json();
    return asRecord(value) ?? {};
  } catch {
    return {};
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}
