import { describe, expect, it } from "vitest";
import { classifyBrowserAuthenticationTransport } from "./browserAuthenticationTransport";

describe("browser authentication transport", () => {
  it("accepts HTTPS origins", () => {
    expect(
      classifyBrowserAuthenticationTransport({
        protocol: "https:",
        hostname: "agents.example.test",
        origin: "https://agents.example.test",
      }),
    ).toMatchObject({ supported: true, mode: "secure" });
  });

  it.each([
    ["localhost", "http://localhost:5173"],
    ["dev.localhost", "http://dev.localhost:5173"],
    ["127.0.0.1", "http://127.0.0.1:5173"],
    ["127.2.3.4", "http://127.2.3.4:5173"],
    ["[::1]", "http://[::1]:5173"],
  ])("keeps maintained loopback HTTP development reachable for %s", (hostname, origin) => {
    expect(
      classifyBrowserAuthenticationTransport({ protocol: "http:", hostname, origin }),
    ).toMatchObject({ supported: true, mode: "loopback-http" });
  });

  it.each([
    ["203.0.113.10", "http://203.0.113.10:8080"],
    ["agents.example.test", "http://agents.example.test:8080"],
    ["localhost.example.test", "http://localhost.example.test:8080"],
  ])("blocks insecure non-loopback browser authentication for %s", (hostname, origin) => {
    expect(
      classifyBrowserAuthenticationTransport({ protocol: "http:", hostname, origin }),
    ).toEqual({ supported: false, mode: "unsupported", origin });
  });

  it("does not block server-side rendering where there is no browser origin", () => {
    expect(classifyBrowserAuthenticationTransport(null)).toEqual({
      supported: true,
      mode: "non-browser",
      origin: null,
    });
  });
});
