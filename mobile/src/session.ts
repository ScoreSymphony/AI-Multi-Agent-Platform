import type { AuthenticatedActor, JsonValue } from "./types";

export interface SecretStorage {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
  deleteItem(key: string): Promise<void>;
}

export interface ServerProfile {
  id: string;
  displayName: string;
  baseUrl: string;
  lastSuccessfulConnection: string | null;
  apiVersion: string | null;
  resources: string[];
  commands: string[];
}

export interface MobileSession {
  profileId: string;
  displayName: string;
  baseUrl: string;
}

export interface MobilePairingDescriptor {
  baseUrl: string;
  pairingId: string | null;
  code: string;
  protocolVersion: "1";
}

export interface ProfileCompatibilitySnapshot {
  apiVersion: string;
  resources: string[];
  commands: string[];
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

interface ProfileDocument {
  schemaVersion: 1;
  selectedProfileId: string | null;
  profiles: ServerProfile[];
}

const PROFILE_DOCUMENT_KEY = "ai-agent-platform.mobile.profiles.v1";
const PROFILE_TOKEN_PREFIX = "ai-agent-platform.mobile.profile-token.";
const LEGACY_TOKEN_KEY = "ai-agent-platform.mobile.bearer-token";
const LEGACY_SERVER_KEY = "ai-agent-platform.mobile.server-url";

export class MobileSessionStore {
  constructor(private readonly storage: SecretStorage) {}

  async listProfiles(): Promise<ServerProfile[]> {
    return (await this.loadDocument()).profiles.map(cloneProfile);
  }

  async selectedProfile(): Promise<ServerProfile | null> {
    const document = await this.loadDocument();
    if (!document.selectedProfileId) return null;
    const profile =
      document.profiles.find((candidate) => candidate.id === document.selectedProfileId) ?? null;
    return profile ? cloneProfile(profile) : null;
  }

  async current(): Promise<MobileSession | null> {
    const profile = await this.selectedProfile();
    if (!profile) return null;
    const token = await this.storage.getItem(tokenKey(profile.id));
    if (!token) return null;
    return {
      profileId: profile.id,
      displayName: profile.displayName,
      baseUrl: profile.baseUrl,
    };
  }

  async getToken(profileId?: string): Promise<string | null> {
    const resolvedProfileId = profileId ?? (await this.selectedProfile())?.id ?? null;
    if (!resolvedProfileId) return null;
    return this.storage.getItem(tokenKey(resolvedProfileId));
  }

  async selectProfile(profileId: string): Promise<ServerProfile> {
    const document = await this.loadDocument();
    const profile = requireProfile(document, profileId);
    await this.saveDocument({
      ...document,
      selectedProfileId: profile.id,
    });
    return cloneProfile(profile);
  }

  async removeProfile(profileId: string): Promise<void> {
    const document = await this.loadDocument();
    const remaining = document.profiles.filter((profile) => profile.id !== profileId);
    if (remaining.length === document.profiles.length) return;
    await this.storage.deleteItem(tokenKey(profileId));
    const selectedProfileId =
      document.selectedProfileId === profileId
        ? (remaining[0]?.id ?? null)
        : document.selectedProfileId;
    await this.saveDocument({
      schemaVersion: 1,
      selectedProfileId,
      profiles: remaining,
    });
  }

  async recordSuccessfulConnection(
    profileId: string,
    compatibility: ProfileCompatibilitySnapshot,
  ): Promise<ServerProfile> {
    const document = await this.loadDocument();
    const profile = requireProfile(document, profileId);
    const updated: ServerProfile = {
      ...profile,
      lastSuccessfulConnection: new Date().toISOString(),
      apiVersion: compatibility.apiVersion,
      resources: uniqueStrings(compatibility.resources),
      commands: uniqueStrings(compatibility.commands),
    };
    await this.saveDocument({
      ...document,
      profiles: document.profiles.map((candidate) =>
        candidate.id === profileId ? updated : candidate
      ),
    });
    return cloneProfile(updated);
  }

  async activate(
    baseUrl: string,
    token: string,
    fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis),
    displayName?: string,
  ): Promise<AuthenticatedActor> {
    const normalizedBaseUrl = normalizeServerUrl(baseUrl);
    const candidate = token.trim();
    if (!candidate) throw new Error("Bearer credential is required");

    const actor = await validateCredential(normalizedBaseUrl, candidate, fetchImpl);
    await this.storeCredential(normalizedBaseUrl, candidate, displayName);
    return actor;
  }

  async pair(
    descriptor: MobilePairingDescriptor,
    deviceName: string,
    platform: string,
    fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis),
    profileName?: string,
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
    if (
      !("device" in payload) ||
      typeof payload.device?.server_origin !== "string" ||
      normalizeServerUrl(payload.device.server_origin) !== baseUrl
    ) {
      throw new Error("Pairing response server identity does not match the trusted Control Plane");
    }

    // Persist immediately after the one-time consume succeeds. If identity verification is
    // temporarily unreachable, restart recovery can retry /auth/me without losing the secret.
    const profile = await this.storeCredential(
      baseUrl,
      payload.credential.secret,
      profileName,
    );
    try {
      return await validateCredential(baseUrl, payload.credential.secret, fetchImpl);
    } catch (error) {
      if (error instanceof CredentialRejectedError) {
        await this.clear(profile.id);
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
    profileName?: string,
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
      profileName,
    );
  }

  async clear(profileId?: string): Promise<void> {
    const resolvedProfileId = profileId ?? (await this.selectedProfile())?.id ?? null;
    if (!resolvedProfileId) return;
    await this.storage.deleteItem(tokenKey(resolvedProfileId));
  }

  private async storeCredential(
    baseUrl: string,
    token: string,
    displayName?: string,
  ): Promise<ServerProfile> {
    const normalizedBaseUrl = normalizeServerUrl(baseUrl);
    const document = await this.loadDocument();
    const existing = document.profiles.find(
      (profile) => profile.baseUrl === normalizedBaseUrl
    );
    const profile: ServerProfile = existing
      ? {
          ...existing,
          displayName: normalizeDisplayName(displayName, existing.displayName),
        }
      : {
          id: profileIdFor(normalizedBaseUrl),
          displayName: normalizeDisplayName(displayName, defaultDisplayName(normalizedBaseUrl)),
          baseUrl: normalizedBaseUrl,
          lastSuccessfulConnection: null,
          apiVersion: null,
          resources: [],
          commands: [],
        };

    await this.storage.setItem(tokenKey(profile.id), token);
    await this.saveDocument({
      schemaVersion: 1,
      selectedProfileId: profile.id,
      profiles: existing
        ? document.profiles.map((candidate) =>
            candidate.id === profile.id ? profile : candidate
          )
        : [...document.profiles, profile],
    });
    return cloneProfile(profile);
  }

  private async loadDocument(): Promise<ProfileDocument> {
    const raw = await this.storage.getItem(PROFILE_DOCUMENT_KEY);
    if (raw) return parseProfileDocument(raw);

    const [legacyServer, legacyToken] = await Promise.all([
      this.storage.getItem(LEGACY_SERVER_KEY),
      this.storage.getItem(LEGACY_TOKEN_KEY),
    ]);
    if (!legacyServer || !legacyToken) return emptyDocument();

    const baseUrl = normalizeServerUrl(legacyServer);
    const profile: ServerProfile = {
      id: profileIdFor(baseUrl),
      displayName: defaultDisplayName(baseUrl),
      baseUrl,
      lastSuccessfulConnection: null,
      apiVersion: null,
      resources: [],
      commands: [],
    };
    await this.storage.setItem(tokenKey(profile.id), legacyToken);
    const migrated: ProfileDocument = {
      schemaVersion: 1,
      selectedProfileId: profile.id,
      profiles: [profile],
    };
    await this.saveDocument(migrated);
    await Promise.all([
      this.storage.deleteItem(LEGACY_SERVER_KEY),
      this.storage.deleteItem(LEGACY_TOKEN_KEY),
    ]);
    return migrated;
  }

  private async saveDocument(document: ProfileDocument): Promise<void> {
    await this.storage.setItem(PROFILE_DOCUMENT_KEY, JSON.stringify(document));
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

function parseProfileDocument(raw: string): ProfileDocument {
  let value: unknown;
  try {
    value = JSON.parse(raw) as unknown;
  } catch {
    throw new Error("Stored mobile server profiles are invalid JSON");
  }
  if (!isRecord(value) || value.schemaVersion !== 1 || !Array.isArray(value.profiles)) {
    throw new Error("Stored mobile server profiles use an unsupported format");
  }
  const profiles = value.profiles.map(parseServerProfile);
  const selectedProfileId =
    typeof value.selectedProfileId === "string" ? value.selectedProfileId : null;
  if (
    selectedProfileId !== null &&
    !profiles.some((profile) => profile.id === selectedProfileId)
  ) {
    throw new Error("Stored selected mobile server profile does not exist");
  }
  return {
    schemaVersion: 1,
    selectedProfileId,
    profiles,
  };
}

function parseServerProfile(value: unknown): ServerProfile {
  if (!isRecord(value)) throw new Error("Stored mobile server profile is invalid");
  const baseUrl = normalizeServerUrl(requireStoredString(value.baseUrl, "baseUrl"));
  const id = requireStoredString(value.id, "id");
  if (id !== profileIdFor(baseUrl)) {
    throw new Error("Stored mobile server profile identity does not match its server");
  }
  return {
    id,
    displayName: requireStoredString(value.displayName, "displayName"),
    baseUrl,
    lastSuccessfulConnection:
      typeof value.lastSuccessfulConnection === "string"
        ? value.lastSuccessfulConnection
        : null,
    apiVersion: typeof value.apiVersion === "string" ? value.apiVersion : null,
    resources: stringArray(value.resources),
    commands: stringArray(value.commands),
  };
}

function emptyDocument(): ProfileDocument {
  return { schemaVersion: 1, selectedProfileId: null, profiles: [] };
}

function requireProfile(document: ProfileDocument, profileId: string): ServerProfile {
  const profile = document.profiles.find((candidate) => candidate.id === profileId);
  if (!profile) throw new Error("Mobile server profile does not exist");
  return profile;
}

function tokenKey(profileId: string): string {
  if (!/^profile-[0-9a-f]{8}$/.test(profileId)) {
    throw new Error("Mobile server profile identifier is invalid");
  }
  return `${PROFILE_TOKEN_PREFIX}${profileId}`;
}

function profileIdFor(baseUrl: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < baseUrl.length; index += 1) {
    hash ^= baseUrl.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `profile-${hash.toString(16).padStart(8, "0")}`;
}

function defaultDisplayName(baseUrl: string): string {
  return new URL(baseUrl).host;
}

function normalizeDisplayName(value: string | undefined, fallback: string): string {
  const normalized = value?.trim();
  return normalized || fallback;
}

function cloneProfile(profile: ServerProfile): ServerProfile {
  return {
    ...profile,
    resources: [...profile.resources],
    commands: [...profile.commands],
  };
}

function uniqueStrings(values: string[]): string[] {
  return [...new Set(values.filter((value) => value.trim()).map((value) => value.trim()))];
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return uniqueStrings(value.filter((item): item is string => typeof item === "string"));
}

function requireStoredString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`Stored mobile server profile ${field} is invalid`);
  }
  return value.trim();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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
