import { describe, expect, it, vi } from "vitest";

import {
  completeMobilePairing,
  pairingRequestFromFallbackCode,
  parseMobilePairingDeepLink,
} from "../src/pairing";
import { MobileSessionStore, type SecretStorage } from "../src/session";

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

describe("mobile pairing", () => {
  it("parses a versioned HTTPS pairing deep link and rejects unsafe variants", () => {
    expect(
      parseMobilePairingDeepLink(
        "aiagentplatform://pair?v=1&origin=https%3A%2F%2Fplatform.example&request_id=pairing_123&secret=proof",
      ),
    ).toEqual({
      protocolVersion: "1",
      serverOrigin: "https://platform.example",
      requestId: "pairing_123",
      proof: "proof",
    });

    expect(
      parseMobilePairingDeepLink(
        "aiagentplatform://pair?v=2&origin=https%3A%2F%2Fplatform.example&request_id=pairing_123&secret=proof",
      ),
    ).toBeNull();
    expect(
      parseMobilePairingDeepLink(
        "aiagentplatform://pair?v=1&origin=http%3A%2F%2Fplatform.example&request_id=pairing_123&secret=proof",
      ),
    ).toBeNull();
    expect(
      parseMobilePairingDeepLink(
        "aiagentplatform://pair/path?v=1&origin=https%3A%2F%2Fplatform.example&request_id=pairing_123&secret=proof",
      ),
    ).toBeNull();
    expect(
      parseMobilePairingDeepLink(
        "aiagentplatform://pair?v=1&origin=https%3A%2F%2Fplatform.example&request_id=pairing_123&secret=a&secret=b",
      ),
    ).toBeNull();
  });

  it("accepts loopback fallback codes but rejects remote HTTP", () => {
    expect(pairingRequestFromFallbackCode("http://127.0.0.1:8000", "pairing_1", "ABC123")).toEqual({
      protocolVersion: "1",
      serverOrigin: "http://127.0.0.1:8000",
      requestId: "pairing_1",
      proof: "ABC123",
    });
    expect(() =>
      pairingRequestFromFallbackCode("http://platform.example", "pairing_1", "ABC123"),
    ).toThrow("Remote Control Plane connections require HTTPS");
  });

  it("stores the issued credential only after canonical auth/me verification", async () => {
    const storage = new MemoryStorage();
    const sessionStore = new MobileSessionStore(storage);
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            credential_id: "credential_mobile",
            secret: "amp1.credential_mobile.secret",
            expires_at: null,
            secret_display: "one_time",
          }),
          { status: 201 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            actor_id: "user_1",
            actor_type: "human",
            authentication_method: "personal_access_token",
            credential_id: "credential_mobile",
            authenticated_at: "2026-09-20T18:00:00Z",
            expires_at: null,
            organization_id: null,
            project_id: null,
          }),
          { status: 200 },
        ),
      );

    const result = await completeMobilePairing(
      sessionStore,
      {
        protocolVersion: "1",
        serverOrigin: "https://platform.example",
        requestId: "pairing_1",
        proof: "one-time-proof",
      },
      "Android phone",
      { platform: "android" },
      fetchImpl,
    );

    expect(result.credentialId).toBe("credential_mobile");
    expect(result.actor.actor_id).toBe("user_1");
    expect(await sessionStore.current()).toEqual({ baseUrl: "https://platform.example" });
    expect(await sessionStore.getToken()).toBe("amp1.credential_mobile.secret");

    const restarted = new MobileSessionStore(storage);
    expect(await restarted.current()).toEqual({ baseUrl: "https://platform.example" });
    expect(await restarted.getToken()).toBe("amp1.credential_mobile.secret");
  });

  it("does not persist the device credential when auth/me verification fails", async () => {
    const storage = new MemoryStorage();
    const sessionStore = new MobileSessionStore(storage);
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            credential_id: "credential_mobile",
            secret: "amp1.credential_mobile.secret",
          }),
          { status: 201 },
        ),
      )
      .mockResolvedValueOnce(new Response("{}", { status: 401 }));

    await expect(
      completeMobilePairing(
        sessionStore,
        {
          protocolVersion: "1",
          serverOrigin: "https://platform.example",
          requestId: "pairing_1",
          proof: "one-time-proof",
        },
        "Android phone",
        {},
        fetchImpl,
      ),
    ).rejects.toThrow("Credential validation failed");
    expect(await sessionStore.current()).toBeNull();
    expect(await sessionStore.getToken()).toBeNull();
  });
});
