import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { BrowserSessionClient, type FirstUserBootstrapStatus } from "../api/browserSession";
import { FirstUserSetupPage } from "./FirstUserSetupPage";

const session = new BrowserSessionClient({
  fetchImpl: vi.fn(async () => new Response("{}", { status: 200 })),
  storage: null,
});

function render(state: FirstUserBootstrapStatus["state"]): string {
  return renderToStaticMarkup(
    <FirstUserSetupPage
      session={session}
      status={{
        state,
        bootstrap_available: true,
        password_policy: { min_length: 12, max_bytes: 1024 },
      }}
      onComplete={() => undefined}
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
});
