import { normalizeServerUrl } from "./session";

export type MobileConnectionState =
  | "never_configured"
  | "connecting"
  | "connected"
  | "offline"
  | "authentication_expired"
  | "tls_failure"
  | "incompatible"
  | "permission_denied";

export interface ControlPlaneCompatibility {
  apiVersion: string;
  resources: string[];
  commands: string[];
  releaseStatusPath: string | null;
}

export class MobileConnectionError extends Error {
  constructor(
    readonly state: Exclude<
      MobileConnectionState,
      "never_configured" | "connecting" | "connected"
    >,
    message: string,
    readonly status: number | null = null,
    readonly code: string | null = null,
  ) {
    super(message);
    this.name = "MobileConnectionError";
  }
}

export async function probeControlPlane(
  baseUrl: string,
  supportedApiVersion: string,
  fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis),
): Promise<ControlPlaneCompatibility> {
  const normalizedBaseUrl = normalizeServerUrl(baseUrl);
  let response: Response;
  try {
    response = await fetchImpl(`${normalizedBaseUrl}/api/${supportedApiVersion}`, {
      method: "GET",
      headers: {
        Accept: "application/json",
        "X-Correlation-ID": requestId(),
      },
    });
  } catch (error) {
    throw networkConnectionError(error);
  }

  const text = await response.text();
  const payload = text ? safeJson(text) : null;
  if (!response.ok) {
    const body = isRecord(payload) ? payload : {};
    const code = typeof body.code === "string" ? body.code : null;
    const message =
      typeof body.message === "string"
        ? body.message
        : `Control Plane compatibility probe failed with HTTP ${response.status}`;
    if (response.status === 401) {
      throw new MobileConnectionError("authentication_expired", message, response.status, code);
    }
    if (response.status === 403) {
      throw new MobileConnectionError("permission_denied", message, response.status, code);
    }
    if (code === "unsupported_api_version" || response.status === 404) {
      throw new MobileConnectionError("incompatible", message, response.status, code);
    }
    throw new MobileConnectionError("offline", message, response.status, code);
  }

  if (!isRecord(payload)) {
    throw new MobileConnectionError(
      "incompatible",
      "Control Plane compatibility manifest is not a JSON object",
    );
  }
  const apiVersion = typeof payload.api_version === "string" ? payload.api_version : "";
  if (apiVersion !== supportedApiVersion) {
    throw new MobileConnectionError(
      "incompatible",
      `This app supports Control Plane ${supportedApiVersion}; server reports ${apiVersion || "no API version"}`,
      response.status,
      "unsupported_api_version",
    );
  }

  return {
    apiVersion,
    resources: stringArray(payload.resources),
    commands: stringArray(payload.commands),
    releaseStatusPath:
      typeof payload.release_status === "string" ? payload.release_status : null,
  };
}

export function classifyConnectionError(error: unknown): MobileConnectionState {
  if (error instanceof MobileConnectionError) return error.state;
  const record = isRecord(error) ? error : {};
  const status = typeof record.status === "number" ? record.status : null;
  const code = typeof record.code === "string" ? record.code : null;
  const message =
    error instanceof Error
      ? error.message
      : typeof record.message === "string"
        ? record.message
        : String(error);

  if (status === 401 || code === "missing_credential" || code === "unauthorized") {
    return "authentication_expired";
  }
  if (status === 403 || code === "forbidden") return "permission_denied";
  if (code === "unsupported_api_version") return "incompatible";
  if (looksLikeTlsFailure(message)) return "tls_failure";
  return "offline";
}

export function connectionStateMessage(state: MobileConnectionState): string {
  switch (state) {
    case "never_configured":
      return "No platform is configured yet.";
    case "connecting":
      return "Connecting to the selected platform…";
    case "connected":
      return "Connected and authenticated.";
    case "offline":
      return "The platform is unreachable. Cached reads remain read-only.";
    case "authentication_expired":
      return "This device credential is missing, expired, or revoked. Pair again.";
    case "tls_failure":
      return "TLS/certificate validation failed. The app will not downgrade to insecure HTTP.";
    case "incompatible":
      return "The server API is incompatible with this app version.";
    case "permission_denied":
      return "The server is reachable, but this device is not allowed to access the requested surface.";
  }
}

function networkConnectionError(error: unknown): MobileConnectionError {
  const message = error instanceof Error ? error.message : "Control Plane network request failed";
  return new MobileConnectionError(
    looksLikeTlsFailure(message) ? "tls_failure" : "offline",
    message,
  );
}

function looksLikeTlsFailure(message: string): boolean {
  return /(certificate|certpath|trust anchor|ssl|tls|handshake|hostname verification)/i.test(message);
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new MobileConnectionError(
      "incompatible",
      "Control Plane compatibility manifest returned invalid JSON",
    );
  }
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requestId(): string {
  return globalThis.crypto?.randomUUID?.() ??
    `mobile-connect-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
