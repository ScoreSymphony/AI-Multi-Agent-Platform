import { describe, expect, it, vi } from "vitest";

import { MobileSessionStore, normalizeServerUrl, type SecretStorage } from "../src/session";

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

  it("exchanges a pairing code, validates the issued credential, and survives restart", async () => {
    const storage = new MemoryStorage();
    const store = new MobileSessionStore(storage);
    const actor = {
      actor_id: "user-1",
      actor_type: "human",
      authentication_method: "mobile_token",
      credential_id: "credential-1",
      authenticated_at: "2026-09-20T00:00:00Z",
      expires_at: null,
      organization_id: null,
      project_id: null,
    };
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            device: {
              id: "mobile_device_1",
              credential_id: "credential-1",
              display_name: "Pixel",
              platform: "android",
              active: true,
            },
            credential: {
              id: "credential-1",
              secret: "amp1.credential-1.device-secret",
              expires_at: null,
              secret_display: "one_time",
            },
          }),
          { status: 201 },
        ),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify(actor), { status: 200 }));

    const paired = await store.pair(
      {
        baseUrl: "https://platform.example",
        pairingCode: "ABCDEFGH-JKLMNPQRSTUVWX234567",
        pairingId: "mobile_pairing_123e4567-e89b-12d3-a456-426614174000",
        deviceName: "Pixel",
        devicePlatform: "android",
      },
      fetchImpl,
    );

    expect(paired.authentication_method).toBe("mobile_token");
    expect(await store.getToken()).toBe("amp1.credential-1.device-secret");
    expect(await new MobileSessionStore(storage).current()).toEqual({
      baseUrl: "https://platform.example",
    });
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(
      "https://platform.example/api/v1/auth/mobile-pairings:consume",
    );
    const request = fetchImpl.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toMatchObject({
      server_origin: "https://platform.example",
      device_name: "Pixel",
      device_platform: "android",
      protocol_version: 1,
    });
    expect(fetchImpl.mock.calls[1]?.[0]).toBe("https://platform.example/api/v1/auth/me");
  });

  it("never persists a device credential when post-pair identity verification fails", async () => {
    const storage = new MemoryStorage();
    const store = new MobileSessionStore(storage);
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            device: {
              id: "mobile_device_1",
              credential_id: "credential-1",
              display_name: "Pixel",
              platform: "android",
              active: true,
            },
            credential: {
              id: "credential-1",
              secret: "amp1.credential-1.device-secret",
              expires_at: null,
              secret_display: "one_time",
            },
          }),
          { status: 201 },
        ),
      )
      .mockResolvedValueOnce(new Response("{}", { status: 401 }));

    await expect(
      store.pair(
        {
          baseUrl: "https://platform.example",
          pairingCode: "ABCDEFGH-JKLMNPQRSTUVWX234567",
          deviceName: "Pixel",
          devicePlatform: "android",
        },
        fetchImpl,
      ),
    ).rejects.toThrow("Credential validation failed");
    expect(await store.current()).toBeNull();
    expect(await store.getToken()).toBeNull();
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
});
