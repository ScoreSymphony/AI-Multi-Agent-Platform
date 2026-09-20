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
    expect(await store.current()).toEqual({ baseUrl: "https://platform.example" });
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
      code: "ABCDEFGHIJKL",
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
    expect(await store.current()).toEqual({ baseUrl: "https://platform.example" });
    expect(await store.getToken()).toBe("amp1.credential_1.device-secret");

    const [consumeUrl, consumeInit] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(consumeUrl).toBe("https://platform.example/api/v1/auth/mobile-pairings:consume");
    expect(JSON.parse(String(consumeInit.body))).toMatchObject({
      pairing_id: null,
      code: "ABCDEFGHIJKL",
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

    expect(await store.current()).toEqual({ baseUrl: "https://platform.example" });
    expect(await store.getToken()).toBe("amp1.credential_1.device-secret");
  });

});
