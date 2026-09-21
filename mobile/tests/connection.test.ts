import { describe, expect, it, vi } from "vitest";

import {
  classifyConnectionError,
  connectionStateMessage,
  initialConnectionState,
  probeControlPlane,
} from "../src/connection";

describe("mobile connection compatibility", () => {
  it("starts unconfigured without a saved server and requests re-pairing for a profile without a credential", () => {
    expect(initialConnectionState(false, false)).toBe("never_configured");
    expect(initialConnectionState(true, false)).toBe("authentication_expired");
    expect(initialConnectionState(true, true)).toBe("connecting");
  });

  it("accepts the advertised v1 Control Plane manifest", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          api_version: "v1",
          resources: ["tasks", "runs", "notifications"],
          commands: ["approval.approve"],
          release_status: "/api/v1/release/status",
        }),
        { status: 200 },
      ),
    );

    await expect(
      probeControlPlane("https://platform.example", "v1", fetchImpl),
    ).resolves.toEqual({
      apiVersion: "v1",
      resources: ["tasks", "runs", "notifications"],
      commands: ["approval.approve"],
      releaseStatusPath: "/api/v1/release/status",
    });
    expect(fetchImpl).toHaveBeenCalledWith(
      "https://platform.example/api/v1",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("reports an incompatible API instead of guessing compatibility", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ api_version: "v2", resources: [] }), { status: 200 }),
    );

    await expect(
      probeControlPlane("https://platform.example", "v1", fetchImpl),
    ).rejects.toMatchObject({
      state: "incompatible",
      code: "unsupported_api_version",
    });
  });

  it("distinguishes permission, authentication, TLS and offline failures", async () => {
    const forbidden = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ code: "forbidden", message: "denied" }), { status: 403 }),
    );
    await expect(
      probeControlPlane("https://platform.example", "v1", forbidden),
    ).rejects.toMatchObject({
      name: "MobileConnectionError",
      state: "permission_denied",
    });

    expect(classifyConnectionError({ status: 401, code: "unauthorized" })).toBe(
      "authentication_expired",
    );
    expect(classifyConnectionError(new Error("TLS certificate handshake failed"))).toBe(
      "tls_failure",
    );
    expect(classifyConnectionError(new Error("network unreachable"))).toBe("offline");
    expect(connectionStateMessage("tls_failure")).toContain("TLS/certificate");
  });
});
