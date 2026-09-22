import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { BrowserSessionClient } from "../api/browserSession";
import { classifyBrowserAuthenticationTransport } from "../security/browserAuthenticationTransport";
import { SignInPage } from "./SignInPage";

describe("SignInPage", () => {
  const session = new BrowserSessionClient({
    fetchImpl: vi.fn(async () => new Response("{}", { status: 200 })),
    storage: null,
  });

  it("renders the initialized-install sign-in flow without a bootstrap form", () => {
    const html = renderToStaticMarkup(
      <SignInPage session={session} onAuthenticated={() => undefined} />,
    );

    expect(html).toContain("Sign in");
    expect(html).toContain("already initialized");
    expect(html).toContain('autoComplete="username"');
    expect(html).toContain('autoComplete="current-password"');
    expect(html).not.toContain("Create your administrator account");
  });

  it("does not present a credential form on unsupported public HTTP origins", () => {
    const transport = classifyBrowserAuthenticationTransport({
      protocol: "http:",
      hostname: "203.0.113.10",
      origin: "http://203.0.113.10:8080",
    });
    const html = renderToStaticMarkup(
      <SignInPage
        session={session}
        transport={transport}
        onAuthenticated={() => undefined}
      />,
    );

    expect(html).toContain("HTTPS required");
    expect(html).toContain("Browser authentication is blocked on this insecure origin");
    expect(html).toContain("http://203.0.113.10:8080");
    expect(html).not.toContain("<form");
  });
});
