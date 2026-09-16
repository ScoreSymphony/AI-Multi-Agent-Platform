import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { BootstrapAccountForm, LoginForm } from "./FirstRunGate";

describe("FirstRunGate access forms", () => {
  it("renders first-account creation with server-provided password rules", () => {
    const html = renderToStaticMarkup(
      <BootstrapAccountForm
        status={{
          state: "uninitialized",
          bootstrap_available: true,
          password_policy: { minimum_length: 12, maximum_bytes: 1024 },
        }}
        busy={false}
        error={null}
        onSubmit={vi.fn()}
      />,
    );

    expect(html).toContain("Create the first administrator account");
    expect(html).toContain("No terminal bootstrap command is required");
    expect(html).toContain('name="password_confirmation"');
    expect(html).toContain('minLength="12"');
    expect(html).toContain("1024 UTF-8 bytes");
    expect(html).not.toContain("Sign in with an existing local account");
  });

  it("renders normal sign-in separately from first-user creation", () => {
    const html = renderToStaticMarkup(
      <LoginForm busy={false} error={null} onSubmit={vi.fn()} />,
    );

    expect(html).toContain("Sign in");
    expect(html).toContain("existing local account");
    expect(html).toContain('autocomplete="current-password"');
    expect(html).not.toContain("Create the first administrator account");
  });
});
