import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { BrowserSessionClient } from "../api/browserSession";
import { SignInPage } from "./SignInPage";

describe("SignInPage", () => {
  it("renders the initialized-install sign-in flow without a bootstrap form", () => {
    const session = new BrowserSessionClient({
      fetchImpl: vi.fn(async () => new Response("{}", { status: 200 })),
      storage: null,
    });

    const html = renderToStaticMarkup(
      <SignInPage session={session} onAuthenticated={() => undefined} />,
    );

    expect(html).toContain("Sign in");
    expect(html).toContain("already initialized");
    expect(html).toContain('autoComplete="username"');
    expect(html).toContain('autoComplete="current-password"');
    expect(html).not.toContain("Create your administrator account");
  });
});
