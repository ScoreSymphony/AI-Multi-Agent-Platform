import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { BrowserSessionClient, type FirstUserBootstrapStatus } from "../api/browserSession";
import {
  classifyBrowserAuthenticationTransport,
  type BrowserAuthenticationTransport,
} from "../security/browserAuthenticationTransport";
import { FirstUserSetupPage } from "./FirstUserSetupPage";

const session = new BrowserSessionClient({
  fetchImpl: vi.fn(async () => new Response("{}", { status: 200 })),
  storage: null,
});

function render(
  state: FirstUserBootstrapStatus["state"],
  transport?: BrowserAuthenticationTransport,
): string {
  return renderToStaticMarkup(
    <FirstUserSetupPage
      session={session}
      status={{
        state,
        bootstrap_available: true,
        password_policy: { min_length: 12, max_bytes: 1024 },
      }}
      onComplete={() => undefined}
      transport={transport}
    />,
  );
}

describe("FirstUserSetupPage", () => {
  it("renders a fresh-install administrator form from server password requirements", () => {
    const html = render("uninitialized");
    expect(html).toContain("Create your administrator account");
    expect(html).toContain("Confirm password");
    expect(html).toContain("at least 12 characters");
    expect(html).toContain("1024 UTF-8 bytes");
    expect(html).toContain('autoComplete="new-password"');
  });

  it("renders the narrow recovery state without inventing a second account flow", () => {
    const html = render("incomplete");
    expect(html).toContain("Resume administrator setup");
    expect(html).toContain("same account credentials");
    expect(html).toContain("Resume setup");
  });

  it("blocks first-admin creation before credentials can be submitted on public HTTP", () => {
    const transport = classifyBrowserAuthenticationTransport({
      protocol: "http:",
      hostname: "203.0.113.10",
      origin: "http://203.0.113.10:8080",
    });
    const html = render("uninitialized", transport);

    expect(html).toContain("HTTPS required");
    expect(html).toContain("Browser authentication is blocked on this insecure origin");
    expect(html).not.toContain("<form");
    expect(html).not.toContain("Confirm password");
  });
});
