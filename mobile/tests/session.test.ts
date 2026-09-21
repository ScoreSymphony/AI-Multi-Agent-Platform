import { describe, expect, it, vi } from "vitest";

import {
  MobileSessionStore,
  normalizeServerUrl,
  parseMobilePairingUri,
  type SecretStorage,
} from "../src/session";

class MemoryStorage implements SecretStorage {
  readonly values = new Map<string, string>();

  async getItem(key: string): Promise<string | null> {
    return this.values.get(key) ?? null;
  }

  async setItem(key: string, value: string): Promise<void> {
    this.values.set(key, value);
  }

  async deleteItem(key: string): Promise<void> {
    this.values.delete(key);
  }
}

describe("MobileSessionStore", () => {
  it("validates bearer credentials before persisting session material", async () => {
    const storage = new MemoryStorage();
    const store = new MobileSessionStore(storage);
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          actor_id: "user-1",
          actor_type: "human",
          authentication_method: "credential",
          credential_id: "cred-1",
          authenticated_at: "2026-09-19T00:00:00Z",
          expires_at: null,
          organization_id: null,
          project_id: null,
        }),
        { status: 200 },
      ),
    );

    const actor = await store.activate("https://platform.example", "secret-token", fetchImpl);

    expect(actor.actor_id).toBe("user-1");
    expect(await store.current()).toMatchObject({ baseUrl: "https://platform.example" });
    expect(await store.getToken()).toBe("secret-token");
    expect(fetchImpl).toHaveBeenCalledWith(
      "https://platform.example/api/v1/auth/me",
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer secret-token" }),
      }),
    );
  });

  it("clears persisted session material on explicit sign-out", async () => {
    const storage = new MemoryStorage();
    const store = new MobileSessionStore(storage);
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          actor_id: "user-1",
          actor_type: "human",
          authentication_method: "personal_access",
          credential_id: "cred-1",
          authenticated_at: "2026-09-19T00:00:00Z",
          expires_at: null,
          organization_id: null,
          project_id: null,
        }),
        { status: 200 },
      ),
    );

    await store.activate("https://platform.example", "secret-token", fetchImpl);
    await store.clear();

    expect(await store.current()).toBeNull();
    expect(await store.getToken()).toBeNull();
  });

  it("does not persist an invalid credential", async () => {
    const storage = new MemoryStorage();
    const store = new MobileSessionStore(storage);
    const fetchImpl = vi.fn().mockResolvedValue(new Response("{}", { status: 401 }));

    await expect(store.activate("https://platform.example", "bad", fetchImpl)).rejects.toThrow(
      "Credential validation failed",
    );
    expect(await store.current()).toBeNull();
  });

  it("requires TLS except for explicit loopback development", () => {
    expect(normalizeServerUrl("http://127.0.0.1:8000")).toBe("http://127.0.0.1:8000");
    expect(() => normalizeServerUrl("http://platform.example")).toThrow(
      "Remote Control Plane connections require HTTPS",
    );
    expect(() => normalizeServerUrl("https://user:pass@platform.example")).toThrow(
      "Credentials must not be embedded",
    );
  });
  it("parses a safe QR pairing payload and rejects unsafe remote origins", () => {
    const descriptor = parseMobilePairingUri(
      "amp-mobile://pair?server=https%3A%2F%2Fplatform.example&id=pairing_123&code=ABCD-EFGH-JKLM&v=1",
    );
    expect(descriptor).toEqual({
      baseUrl: "https://platform.example",
      pairingId: "pairing_123",
      code: "ABCDEFGHJKLM",
      protocolVersion: "1",
    });
    expect(() =>
      parseMobilePairingUri(
        "amp-mobile://pair?server=http%3A%2F%2Fplatform.example&id=pairing_123&code=ABCD-EFGH-JKLM&v=1",
      ),
    ).toThrow("Remote Control Plane connections require HTTPS");
    expect(() =>
      parseMobilePairingUri(
        "amp-mobile://pair?server=https%3A%2F%2Fplatform.example&id=..%2Fsecret&code=ABCD-EFGH-JKLM&v=1",
      ),
    ).toThrow("identifier is invalid");
    expect(() =>
      parseMobilePairingUri(
        "amp-mobile://pair?server=https%3A%2F%2Fplatform.example&id=pairing_123&code=ABCD-EFGH-JKLM&v=2",
      ),
    ).toThrow("Unsupported pairing protocol");
  });

  it("pairs successfully from a parsed QR descriptor and binds the pairing request ID", async () => {
    const storage = new MemoryStorage();
    const store = new MobileSessionStore(storage);
    const descriptor = parseMobilePairingUri(
      "amp-mobile://pair?server=https%3A%2F%2Fplatform.example&id=pairing_123&code=ABCD-EFGH-JKLM&v=1",
    );
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            device: {
              id: "mobile_device_qr",
              display_name: "QR phone",
              server_origin: "https://platform.example",
              credential_id: "credential_qr",
              active: true,
            },
            credential: {
              id: "credential_qr",
              secret: "amp1.credential_qr.device-secret",
              expires_at: null,
              secret_display: "one_time",
            },
          }),
          { status: 201 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            actor_id: "user-1",
            actor_type: "human",
            authentication_method: "mobile_device_token",
            credential_id: "credential_qr",
            authenticated_at: "2026-09-21T08:00:00Z",
            expires_at: null,
            organization_id: null,
            project_id: null,
          }),
          { status: 200 },
        ),
      );

    const actor = await store.pair(descriptor, "QR phone", "android", fetchImpl);

    expect(actor.authentication_method).toBe("mobile_device_token");
    expect(await store.current()).toMatchObject({ baseUrl: "https://platform.example" });
    expect(await store.getToken()).toBe("amp1.credential_qr.device-secret");
    const [consumeUrl, consumeInit] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(consumeUrl).toBe("https://platform.example/api/v1/auth/mobile-pairings:consume");
    expect(JSON.parse(String(consumeInit.body))).toMatchObject({
      pairing_id: "pairing_123",
      code: "ABCDEFGHJKLM",
      device_name: "QR phone",
      platform: "android",
      protocol_version: "1",
    });
  });

  it("pairs with a fallback code, persists the returned credential, and verifies identity", async () => {
    const storage = new MemoryStorage();
    const store = new MobileSessionStore(storage);
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            device: {
              id: "mobile_device_1",
              display_name: "Alice phone",
              server_origin: "https://platform.example",
              credential_id: "credential_1",
              active: true,
            },
            credential: {
              id: "credential_1",
              secret: "amp1.credential_1.device-secret",
              expires_at: null,
              secret_display: "one_time",
            },
          }),
          { status: 201 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            actor_id: "user-1",
            actor_type: "human",
            authentication_method: "mobile_device_token",
            credential_id: "credential_1",
            authenticated_at: "2026-09-20T20:00:00Z",
            expires_at: null,
            organization_id: null,
            project_id: null,
          }),
          { status: 200 },
        ),
      );

    const actor = await store.pairWithCode(
      "https://platform.example",
      "ABCD-EFGH-JKLM",
      "Alice phone",
      "android",
      fetchImpl,
    );

    expect(actor.authentication_method).toBe("mobile_device_token");
    expect(await store.current()).toMatchObject({ baseUrl: "https://platform.example" });
    expect(await store.getToken()).toBe("amp1.credential_1.device-secret");

    const [consumeUrl, consumeInit] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(consumeUrl).toBe("https://platform.example/api/v1/auth/mobile-pairings:consume");
    expect(JSON.parse(String(consumeInit.body))).toMatchObject({
      pairing_id: null,
      code: "ABCDEFGHJKLM",
      device_name: "Alice phone",
      platform: "android",
      protocol_version: "1",
    });
    const [meUrl, meInit] = fetchImpl.mock.calls[1] as [string, RequestInit];
    expect(meUrl).toBe("https://platform.example/api/v1/auth/me");
    expect(new Headers(meInit.headers).get("Authorization")).toBe(
      "Bearer amp1.credential_1.device-secret",
    );
  });

  it("isolates credentials and metadata across multiple server profiles", async () => {
    const storage = new MemoryStorage();
    const store = new MobileSessionStore(storage);
    const actor = {
      actor_id: "user-1",
      actor_type: "human",
      authentication_method: "credential",
      credential_id: "cred-1",
      authenticated_at: "2026-09-21T00:00:00Z",
      expires_at: null,
      organization_id: null,
      project_id: null,
    };
    const fetchImpl = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify(actor), { status: 200 }),
    );

    await store.activate("https://one.example", "token-one", fetchImpl, "Primary");
    const first = (await store.listProfiles())[0]!;
    await store.recordSuccessfulConnection(first.id, {
      apiVersion: "v1",
      resources: ["tasks", "runs"],
      commands: [],
    });

    await store.activate("https://two.example", "token-two", fetchImpl, "Backup");
    const profiles = await store.listProfiles();
    const primary = profiles.find((profile) => profile.baseUrl === "https://one.example")!;
    const backup = profiles.find((profile) => profile.baseUrl === "https://two.example")!;

    expect(primary.displayName).toBe("Primary");
    expect(primary.apiVersion).toBe("v1");
    expect(primary.lastSuccessfulConnection).toBeTruthy();
    expect(backup.displayName).toBe("Backup");
    expect(await store.getToken(primary.id)).toBe("token-one");
    expect(await store.getToken(backup.id)).toBe("token-two");
    expect((await store.current())?.profileId).toBe(backup.id);

    await store.selectProfile(primary.id);
    expect((await store.current())?.profileId).toBe(primary.id);
    await store.clear(primary.id);
    expect(await store.getToken(primary.id)).toBeNull();
    expect(await store.getToken(backup.id)).toBe("token-two");
  });

  it("migrates the legacy single-session keys into an isolated profile", async () => {
    const storage = new MemoryStorage();
    storage.values.set("ai-agent-platform.mobile.server-url", "https://legacy.example");
    storage.values.set("ai-agent-platform.mobile.bearer-token", "legacy-token");
    const store = new MobileSessionStore(storage);

    const session = await store.current();
    expect(session).toMatchObject({
      displayName: "legacy.example",
      baseUrl: "https://legacy.example",
    });
    expect(await store.getToken(session!.profileId)).toBe("legacy-token");
    expect(storage.values.has("ai-agent-platform.mobile.server-url")).toBe(false);
    expect(storage.values.has("ai-agent-platform.mobile.bearer-token")).toBe(false);
  });

  it("retains a consumed credential for restart recovery on transient identity-check failure", async () => {
    const storage = new MemoryStorage();
    const store = new MobileSessionStore(storage);
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            device: {
              id: "mobile_device_1",
              display_name: "Alice phone",
              server_origin: "https://platform.example",
              credential_id: "credential_1",
              active: true,
            },
            credential: {
              id: "credential_1",
              secret: "amp1.credential_1.device-secret",
              expires_at: null,
              secret_display: "one_time",
            },
          }),
          { status: 201 },
        ),
      )
      .mockRejectedValueOnce(new Error("offline"));

    await expect(
      store.pairWithCode(
        "https://platform.example",
        "ABCD-EFGH-JKLM",
        "Alice phone",
        "android",
        fetchImpl,
      ),
    ).rejects.toThrow("offline");

    const restarted = new MobileSessionStore(storage);
    expect(await restarted.current()).toEqual({ baseUrl: "https://platform.example" });
    expect(await restarted.getToken()).toBe("amp1.credential_1.device-secret");
  });

});
