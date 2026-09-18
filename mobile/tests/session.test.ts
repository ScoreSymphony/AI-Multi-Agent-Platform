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
});
